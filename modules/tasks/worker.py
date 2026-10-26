"""常驻 worker：轮询任务表、执行 handler、维护心跳。

设计取舍：
- **不引队列组件**（Redis/Celery）：P0 数据量与并发都不值得多一个中间件，PG 的
  `SKIP LOCKED` 足够（architecture §3 选型表）。
- **handler 注册制**：`modules` 只做机制，具体任务类型（解析、重建索引、评测）
  由 `app` 注册进来 —— 依赖单向 `app → modules`。
- **心跳独立线程**：handler 可能跑几分钟（解析 PDF），主线程阻塞时心跳要照常写，
  否则会被 `reap_stale` 当成僵尸回收，造成同一任务被跑两遍。
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from . import queue

logger = logging.getLogger(__name__)

Handler = Callable[[queue.ClaimedJob, psycopg.Connection], None]


@dataclass
class WorkerConfig:
    poll_seconds: float = 2.0
    heartbeat_seconds: float = 15.0
    stale_after_seconds: int = 120
    max_attempts: int = 3
    reap_every_seconds: float = 60.0


class TaskWorker:
    def __init__(
        self,
        *,
        connect: Callable[[], psycopg.Connection],
        handlers: dict[str, Handler],
        config: WorkerConfig | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._connect = connect
        self._handlers = handlers
        self.config = config or WorkerConfig()
        self.worker_id = worker_id or f"{socket.gethostname()}-{threading.get_ident()}"

    # ---------- 单轮 ----------
    def run_once(self) -> bool:
        """领一个任务并执行。返回 False 表示当时队列为空。"""
        with self._connect() as conn:
            job = queue.claim_one(conn, self.worker_id)
            if job is None:
                return False
            self._execute(conn, job)
            return True

    def _execute(self, conn: psycopg.Connection, job: queue.ClaimedJob) -> None:
        handler = self._handlers.get(job.type)
        if handler is None:
            # 类型没注册：多半是代码版本不一致，重试也不会成功，直接记失败但保留 attempts 语义
            queue.mark_failed(
                conn, job.id, f"未注册的任务类型：{job.type}", max_attempts=0
            )
            logger.error("job %s: unknown type %s", job.public_id, job.type)
            return

        stop_heartbeat = threading.Event()
        beat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job.id, stop_heartbeat),
            name=f"hb-{job.id}",
            daemon=True,
        )
        beat.start()
        started = time.perf_counter()
        try:
            # 领取后、开跑前再确认一次：排队期间可能已被取消
            if queue.cancel_requested(conn, job.id):
                queue.mark_cancelled(conn, job.id)
                logger.info("job %s cancelled before start", job.public_id)
                return
            handler(job, conn)
        except Exception as exc:  # noqa: BLE001 - worker 必须吞掉异常继续跑
            logger.exception("job %s failed: %s", job.public_id, exc)
            if queue.cancel_requested(conn, job.id):
                queue.mark_cancelled(conn, job.id)
                logger.info("job %s -> cancelled (error swallowed)", job.public_id)
            else:
                final = queue.mark_failed(
                    conn,
                    job.id,
                    f"{type(exc).__name__}: {exc}",
                    max_attempts=self.config.max_attempts,
                )
                logger.info("job %s -> %s", job.public_id, final)
        else:
            final = queue.mark_done(conn, job.id)
            logger.info(
                "job %s -> %s in %.0fms",
                job.public_id,
                final,
                (time.perf_counter() - started) * 1000,
            )
        finally:
            stop_heartbeat.set()
            beat.join(timeout=2)

    def _heartbeat_loop(self, job_id: int, stop: threading.Event) -> None:
        while not stop.wait(self.config.heartbeat_seconds):
            try:
                with self._connect() as conn:
                    queue.heartbeat(conn, job_id)
            except Exception as exc:  # noqa: BLE001 - 心跳失败不终止任务
                logger.warning("heartbeat failed for job %s: %s", job_id, exc)

    # ---------- 常驻 ----------
    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop_event = stop_event or threading.Event()
        logger.info(
            "worker %s started (poll=%.1fs, handlers=%s)",
            self.worker_id,
            self.config.poll_seconds,
            sorted(self._handlers),
        )
        last_reap = 0.0
        while not stop_event.is_set():
            try:
                if time.monotonic() - last_reap > self.config.reap_every_seconds:
                    with self._connect() as conn:
                        queue.reap_stale(
                            conn,
                            stale_after_seconds=self.config.stale_after_seconds,
                            max_attempts=self.config.max_attempts,
                        )
                    last_reap = time.monotonic()
                if not self.run_once():
                    stop_event.wait(self.config.poll_seconds)
            except Exception:  # noqa: BLE001 - 数据库抖动不应让 worker 退出
                logger.exception("worker loop error, retrying")
                stop_event.wait(min(self.config.poll_seconds * 3, 30))
        logger.info("worker %s stopped", self.worker_id)
