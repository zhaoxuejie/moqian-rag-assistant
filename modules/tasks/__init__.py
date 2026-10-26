"""tasks：数据库任务表队列 + 常驻 worker 基础设施。S1 落地。

`queue` 是纯 SQL 队列（入队/领取/心跳/回收），`worker` 是通用轮询循环，
任务类型由 `app` 注册 —— 机制在这里，业务在那头。
"""

from .queue import (
    ClaimedJob,
    claim_one,
    enqueue,
    heartbeat,
    mark_done,
    mark_failed,
    queue_stats,
    reap_stale,
)
from .worker import Handler, TaskWorker, WorkerConfig

__all__ = [
    "ClaimedJob",
    "Handler",
    "TaskWorker",
    "WorkerConfig",
    "claim_one",
    "enqueue",
    "heartbeat",
    "mark_done",
    "mark_failed",
    "queue_stats",
    "reap_stale",
]
