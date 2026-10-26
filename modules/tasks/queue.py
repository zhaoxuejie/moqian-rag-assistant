"""任务表队列：PostgreSQL 当队列，不引 Redis / Celery（P0 选型，architecture §3）。

机制要点：
- **领取**用 `SELECT ... FOR UPDATE SKIP LOCKED`，多 worker 并发不会抢同一行；
- **心跳**写 `heartbeat_at`，worker 崩了由 `reap_stale()` 把任务放回队列；
- **重试**由 `attempts` + `max_attempts` 控制，超过则 `failed` 并留 `error`；
- **幂等**靠 `type + payload` 由 handler 自己保证（队列只保证"至少一次"）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import psycopg

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedJob:
    """被本 worker 领取到的任务。"""

    id: int
    type: str
    payload: dict[str, Any]
    attempts: int

    @property
    def public_id(self) -> str:
        """对外 id：契约里所有 id 是字符串。"""
        return f"job-{self.id}"


def enqueue(conn: psycopg.Connection, job_type: str, payload: dict[str, Any] | None = None) -> int:
    """入队，返回任务 id。"""
    with conn.cursor() as cur:
        cur.execute(
            "insert into jobs (type, payload) values (%s, %s::jsonb) returning id",
            (job_type, psycopg.types.json.Jsonb(payload or {})),
        )
        job_id = cur.fetchone()[0]
    conn.commit()
    return int(job_id)


def claim_one(conn: psycopg.Connection, worker_id: str) -> ClaimedJob | None:
    """领取一个 queued 任务并置为 running（原子操作）。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            with candidate as (
              select id from jobs
              where status = 'queued'
              order by created_at
              for update skip locked
              limit 1
            )
            update jobs j
               set status = 'running',
                   worker_id = %s,
                   started_at = now(),
                   heartbeat_at = now(),
                   attempts = j.attempts + 1
              from candidate c
             where j.id = c.id
         returning j.id, j.type, j.payload, j.attempts
            """,
            (worker_id,),
        )
        row = cur.fetchone()
    conn.commit()
    if row is None:
        return None
    job_id, job_type, payload, attempts = row
    return ClaimedJob(
        id=int(job_id), type=str(job_type), payload=payload or {}, attempts=int(attempts)
    )


def heartbeat(conn: psycopg.Connection, job_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("update jobs set heartbeat_at = now() where id = %s", (job_id,))
    conn.commit()


def mark_done(conn: psycopg.Connection, job_id: int) -> str:
    """标记完成；若运行期间收到取消请求，最终状态为 cancelled。返回最终状态。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            update jobs
               set status = case when cancel_requested then 'cancelled' else 'done' end,
                   finished_at = now(),
                   error = null
             where id = %s and status = 'running'
         returning status
            """,
            (job_id,),
        )
        row = cur.fetchone()
    conn.commit()
    return str(row[0]) if row else "unknown"


def cancel_requested(conn: psycopg.Connection, job_id: int) -> bool:
    """运行中的任务是否已收到取消请求。"""
    with conn.cursor() as cur:
        cur.execute(
            "select status = 'cancelled' or cancel_requested from jobs where id = %s",
            (job_id,),
        )
        row = cur.fetchone()
    return bool(row and row[0])


def mark_cancelled(conn: psycopg.Connection, job_id: int) -> None:
    """把运行中的任务落为 cancelled（取消请求已在收到时写入）。"""
    with conn.cursor() as cur:
        cur.execute(
            "update jobs set status = 'cancelled', finished_at = now(), error = null "
            "where id = %s and status = 'running'",
            (job_id,),
        )
    conn.commit()


def mark_failed(
    conn: psycopg.Connection,
    job_id: int,
    error: str,
    *,
    max_attempts: int,
) -> str:
    """失败处理：未超次数放回队列，超了置 failed。返回最终状态。"""
    with conn.cursor() as cur:
        cur.execute("select attempts from jobs where id = %s", (job_id,))
        row = cur.fetchone()
        attempts = int(row[0]) if row else max_attempts
        if attempts < max_attempts:
            cur.execute(
                """
                update jobs
                   set status = 'queued', error = %s, heartbeat_at = null, worker_id = null
                 where id = %s
                """,
                (error[:2000], job_id),
            )
            final = "queued"
        else:
            cur.execute(
                "update jobs set status = 'failed', error = %s, finished_at = now() where id = %s",
                (error[:2000], job_id),
            )
            final = "failed"
    conn.commit()
    return final


def reap_stale(
    conn: psycopg.Connection,
    *,
    stale_after_seconds: int,
    max_attempts: int,
) -> list[int]:
    """把心跳超时的 running 任务放回队列（worker 崩溃自救）。返回被回收的任务 id。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            update jobs
               set status = case
                              when cancel_requested then 'cancelled'
                              when attempts >= %s then 'failed'
                              else 'queued'
                            end,
                   error = coalesce(error, 'worker heartbeat timeout'),
                   finished_at = case
                                   when cancel_requested or attempts >= %s then now()
                                   else null
                                 end,
                   worker_id = null
             where status = 'running'
               and heartbeat_at < now() - make_interval(secs => %s)
         returning id
            """,
            (max_attempts, max_attempts, stale_after_seconds),
        )
        ids = [int(row[0]) for row in cur.fetchall()]
    conn.commit()
    if ids:
        logger.warning("reaped stale jobs: %s", ids)
    return ids


def queue_stats(conn: psycopg.Connection) -> dict[str, int]:
    """四态计数（供后台任务页与徽标）。"""
    with conn.cursor() as cur:
        cur.execute("select status, count(*) from jobs group by status")
        rows = cur.fetchall()
    return {str(status): int(count) for status, count in rows}
