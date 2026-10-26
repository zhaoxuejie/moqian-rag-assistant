"""任务队列与 worker 单测（需要数据库）。

注意：用例会清空 `jobs` 表，**跑测试时不要让 compose 的 worker 同时在消费**；
本地顺序建议：`docker compose stop worker` → `pytest` → `docker compose start worker`。
CI 里没有常驻 worker，不受影响。
"""

from __future__ import annotations

import pytest
from conftest import requires_db

from modules.tasks import queue
from modules.tasks.worker import TaskWorker, WorkerConfig

pytestmark = requires_db


@pytest.fixture(autouse=True)
def clean_jobs(conn):
    """队列用例需要空表，但**不能把开发库里的任务历史清掉**。

    以前这里只 `truncate jobs restart identity`：跑一次全量测试，后台「解析任务」
    里的历史记录就全没了、id 还会回到 1（实测清掉了一条真实 parse 任务）。

    现在先在**数据库内**把 jobs 拷进临时表、用例结束原样写回（连 id 与自增序列一起还原）。
    走 SQL 而不是把行读进 Python 再插回，是因为 `payload` 是 jsonb：读回成 dict 后再
    作为参数插入会报"无法适配 dict"，临时表能原样搬不丢类型。
    """
    with conn.cursor() as cur:
        cur.execute("drop table if exists jobs_snapshot_restore")
        cur.execute("create temp table jobs_snapshot_restore as select * from jobs")
        cur.execute("truncate jobs restart identity")
    conn.commit()
    try:
        yield
    finally:
        with conn.cursor() as cur:
            cur.execute("truncate jobs restart identity")
            # 临时表与原表列序一致（由 create table as select * 保证）
            cur.execute("insert into jobs select * from jobs_snapshot_restore")
            cur.execute(
                "select setval('jobs_id_seq', coalesce((select max(id) from jobs), 0) + 1, false)"
            )
            cur.execute("drop table if exists jobs_snapshot_restore")
        conn.commit()


def test_enqueue_claim_done(conn) -> None:
    job_id = queue.enqueue(conn, "ping", {"n": 1})
    claimed = queue.claim_one(conn, "worker-a")
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.type == "ping"
    assert claimed.payload == {"n": 1}
    assert claimed.attempts == 1

    queue.mark_done(conn, claimed.id)
    assert queue.queue_stats(conn)["done"] == 1
    assert queue.claim_one(conn, "worker-a") is None


def test_skip_locked_gives_different_jobs(conn) -> None:
    """两个 worker 并发领取，不会拿到同一行（SKIP LOCKED 的关键行为）。"""
    first = queue.enqueue(conn, "ping", {"n": 1})
    second = queue.enqueue(conn, "ping", {"n": 2})

    claimed_a = queue.claim_one(conn, "worker-a")
    claimed_b = queue.claim_one(conn, "worker-b")
    assert {claimed_a.id, claimed_b.id} == {first, second}


def test_failure_requeues_until_max_attempts(conn) -> None:
    job_id = queue.enqueue(conn, "ping", {})
    claimed = queue.claim_one(conn, "worker-a")
    assert claimed

    # 第 1 次失败 → 回到 queued
    assert queue.mark_failed(conn, job_id, "boom", max_attempts=2) == "queued"
    # 再领一次（attempts=2），第 2 次失败 → failed
    assert queue.claim_one(conn, "worker-a") is not None
    assert queue.mark_failed(conn, job_id, "boom again", max_attempts=2) == "failed"
    assert queue.queue_stats(conn)["failed"] == 1


def test_reap_stale_requeues_dead_worker_job(conn) -> None:
    job_id = queue.enqueue(conn, "ping", {})
    claimed = queue.claim_one(conn, "worker-dead")
    assert claimed
    # 把心跳拨到很久以前，模拟 worker 崩了
    with conn.cursor() as cur:
        cur.execute(
            "update jobs set heartbeat_at = now() - interval '1 hour' where id = %s", (job_id,)
        )
    conn.commit()

    reaped = queue.reap_stale(conn, stale_after_seconds=60, max_attempts=3)
    assert job_id in reaped
    assert queue.queue_stats(conn)["queued"] == 1


def test_worker_executes_registered_handler(conn) -> None:
    from app.db import connect

    seen: list[int] = []

    def handle(job, _conn) -> None:
        seen.append(job.id)

    worker = TaskWorker(
        connect=connect,
        handlers={"ping": handle},
        config=WorkerConfig(poll_seconds=0.01, heartbeat_seconds=5, max_attempts=2),
        worker_id="test-worker",
    )
    job_id = queue.enqueue(conn, "ping", {})
    assert worker.run_once() is True
    assert seen == [job_id]
    assert queue.queue_stats(conn)["done"] == 1


def test_worker_marks_unknown_type_failed(conn) -> None:
    from app.db import connect

    worker = TaskWorker(
        connect=connect,
        handlers={},
        config=WorkerConfig(poll_seconds=0.01, heartbeat_seconds=5, max_attempts=3),
        worker_id="test-worker",
    )
    queue.enqueue(conn, "not.registered", {})
    assert worker.run_once() is True
    assert queue.queue_stats(conn)["failed"] == 1
