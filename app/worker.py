"""worker 入口：`python -m app.worker`。

这里只做两件事：**注册任务类型**（业务属于 app）和**进程信号处理**。
队列机制在 `modules/tasks`，不在这里。
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Any

from modules.llm import LlmClient, config_from_settings
from modules.tasks import ClaimedJob, TaskWorker, WorkerConfig

from .config import Settings, get_settings
from .db import connect
from .embedding import get_embedding_client
from .ingest import parse_document
from .logging_setup import configure_logging

logger = logging.getLogger("app.worker")


def handle_ping(job: ClaimedJob, conn: Any) -> None:
    """连通性假任务（S1 验收用）：睡 0.2 秒，验证领取→执行→完成全链路。"""
    logger.info("ping job %s payload=%s", job.public_id, job.payload)
    time.sleep(0.2)


def make_llm_smoke(settings: Settings):
    """构造 `llm.smoke` handler：真调一次模型，验证 worker 侧配置可用。"""

    def handle_llm_smoke(job: ClaimedJob, conn: Any) -> None:
        client = LlmClient(config_from_settings(settings))
        result = client.complete(
            [
                {"role": "system", "content": "只回复：链路正常"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=800,
        )
        logger.info(
            "llm.smoke %s ok: model=%s chars=%d reasoning=%d elapsed=%dms",
            job.public_id,
            result.model,
            len(result.text),
            result.reasoning_chars,
            result.elapsed_ms,
        )

    return handle_llm_smoke


def make_parse_handler(settings: Settings):
    """`parse` 任务：解析 PDF → 切片 → 向量化 → 落库（S2 主链路）。

    注意：这里不做"要不要入库"的判断（那是人的决定）；只保证已建档的文档被正确解析。
    """

    def handle_parse(job: ClaimedJob, conn: Any) -> None:
        raw_id = (job.payload or {}).get("documentId")
        if raw_id is None:
            raise ValueError("parse 任务缺少 documentId")
        document_id = int(str(raw_id).removeprefix("doc-"))
        embedder = get_embedding_client(settings)
        result = parse_document(
            conn, document_id=document_id, settings=settings, embedder=embedder
        )
        logger.info(
            "parsed doc-%s: pages=%d chunks=%d toc=%s elapsed=%dms",
            result.document_id,
            result.pages,
            result.chunks,
            result.used_toc,
            result.elapsed_ms,
        )

    return handle_parse


def build_handlers(settings: Settings) -> dict[str, Any]:
    return {
        "ping": handle_ping,
        "llm.smoke": make_llm_smoke(settings),
        "parse": make_parse_handler(settings),
    }


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker = TaskWorker(
        connect=connect,
        handlers=build_handlers(settings),
        config=WorkerConfig(
            poll_seconds=settings.task_poll_seconds,
            heartbeat_seconds=settings.task_heartbeat_seconds,
            stale_after_seconds=settings.task_stale_seconds,
            max_attempts=settings.task_max_attempts,
        ),
    )

    stop = threading.Event()

    def _handle_signal(signum: int, _frame: Any) -> None:
        logger.info("收到信号 %s，准备退出", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    worker.run_forever(stop)


if __name__ == "__main__":
    main()
