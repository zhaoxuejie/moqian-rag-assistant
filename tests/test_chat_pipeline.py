"""`/chat` 正式管线（pipeline）行为测试：检索 → 证据门 → 引用校验。

用受控语料把检索变成确定性的：切片向量用单位向量构造，假 embedder 返回与目标切片
相同的向量，dense 的 top1 必然是它。这样测的是"编排与纪律"，不是模型质量：

- 正常回答：structured → steps → citations → done，非法引用被剥离；
- 引用全非法：不发步骤，直接 gap（纪律 1：没有可核验引用就不回答）；
- 证据不足：不进模型调用，直接 gap（纪律 4）；
- 多批次命中：出 conflict，并列展示而不是替工程师选（纪律 7）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from conftest import requires_db
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.routers.deps import get_conn, get_embedder, get_llm, get_reranker
from app.sessions import create_session

pytestmark = requires_db

TEST_TITLE = "S3 测试：制动器手册片段"

# 单位向量：chunk A 用 e1，冲突用例的 chunk B 也用 e1（便于同时命中）
E1 = [1.0] + [0.0] * 1023


def _ensure_vectors() -> None:
    """确认 1024 维与库表一致（避免测试自己写错维度）。"""
    assert len(E1) == 1024


class FakeCompleteLlm:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, messages, **kwargs) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(text=self.text)


class FixedEmbedder:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.calls = 0

    def embed_one(self, _text: str) -> list[float]:
        self.calls += 1
        return self.vector


@pytest.fixture()
def corpus(conn):
    """建一份受控文档 + 一个切片（batch=B22），用完清理。"""
    _ensure_vectors()
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into documents (source_type, title, device_model, status, meta_json)
            values ('pdf', %s, 'BK系列', 'parsed', '{"category": "manual"}'::jsonb)
            returning id
            """,
            (TEST_TITLE,),
        )
        document_id = int(cur.fetchone()[0])
        cur.execute(
            """
            insert into chunks
                (document_id, evidence_id, content, section_no, page_from, page_to,
                 batch, manual_version, meta_json, embedding, bm25_tokens)
            values (%s, %s, %s, '3.2', 5, 5, 'B22', 'Ver.3', '{}'::jsonb, %s::vector, %s)
            returning id
            """,
            (
                document_id,
                "M99001",
                "制动器间隙调整：用塞尺检查间隙，标准值 0.3 至 0.5 毫米，调整后复核。",
                _vector_text(E1),
                "制动器 间隙 调整 标准 值 塞尺 检查 复核",
            ),
        )
        chunk_id = int(cur.fetchone()[0])
    conn.commit()
    yield {"document_id": document_id, "chunk_id": chunk_id}
    with conn.cursor() as cur:
        cur.execute("delete from documents where id = %s", (document_id,))
    conn.commit()


def _vector_text(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw.strip().split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        if name and data is not None:
            events.append((name, data))
    return events


def make_client(conn, *, llm, embedder=None, settings: Settings | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_settings] = lambda: settings or Settings(
        chat_mode="pipeline",
        retrieval_top_k_dense=5,
        retrieval_top_k_bm25=5,
        retrieval_final_top_n=3,
        evidence_min_hits=1,
        retrieval_min_score=0.2,
        evidence_min_coverage=0.3,
    )
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_embedder] = lambda: embedder or FixedEmbedder(E1)
    app.dependency_overrides[get_reranker] = lambda: None
    return TestClient(app)


def new_session(conn) -> str:
    return create_session(conn, requested_source="host_injected", claimed_account=None)["sessionId"]


def chat(client: TestClient, session_id: str, question: str) -> list[tuple[str, dict]]:
    resp = client.post(
        "/chat",
        json={"sessionId": session_id, "question": question},
        headers={"X-Widget-Session": session_id},
    )
    assert resp.status_code == 200
    return parse_sse(resp.text)


def test_answered_with_valid_and_stripped_citation(conn, corpus) -> None:
    llm = FakeCompleteLlm(
        "1. 用塞尺检查制动器间隙 [M99001]\n"
        "2. 按手册调整至标准值 [M99999]\n"
        "3. 调整后复核并记录 [M99001]"
    )
    client = make_client(conn, llm=llm)
    session_id = new_session(conn)

    events = chat(client, session_id, "制动器间隙怎么调整")
    names = [name for name, _ in events]
    assert names[0] == "structured"
    assert "steps" in names and "citations" in names
    assert names[-1] == "done"
    assert "gap" not in names

    steps = [payload for name, payload in events if name == "steps"]
    assert len(steps) == 3
    assert steps[0]["citations"] == ["M99001"]
    assert steps[1]["citations"] == [], "非法引用剥离后该步没有可核验引用"
    assert steps[2]["citations"] == ["M99001"]
    assert all("M99999" not in step["text"] for step in steps)

    citations = next(payload for name, payload in events if name == "citations")
    assert [item["evidenceId"] for item in citations["items"]] == ["M99001"]
    assert citations["items"][0]["loc"] and "P5" in citations["items"][0]["loc"]

    done = events[-1][1]
    assert done["refused"] is False
    assert done["citationsCount"] == 1


def test_all_invalid_citations_become_gap(conn, corpus) -> None:
    llm = FakeCompleteLlm("1. 直接给出结论 [M88888]")
    client = make_client(conn, llm=llm)
    session_id = new_session(conn)

    events = chat(client, session_id, "制动器间隙怎么调整")
    names = [name for name, _ in events]
    assert names[0] == "structured"
    assert names[-1] == "gap", "没有可核验引用时必须拒答"
    assert "steps" not in names and "citations" not in names
    assert "M88888" not in json.dumps(events, ensure_ascii=False)


def test_gate_failure_skips_llm(conn, corpus) -> None:
    llm = FakeCompleteLlm("1. 不该被调用")
    client = make_client(conn, llm=llm)
    session_id = new_session(conn)

    events = chat(client, session_id, "量子计算机的退相干时间怎么测量")
    names = [name for name, _ in events]
    assert names == ["structured", "gap"]
    assert llm.calls == 0, "证据不足时不允许调用模型（纪律 4）"


def test_conflict_when_multiple_batches_hit(conn, corpus) -> None:
    """再插一个 B21 批次切片（同向量、同关键词），应出 conflict 并列展示。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into chunks
                (document_id, evidence_id, content, section_no, page_from, page_to,
                 batch, manual_version, meta_json, embedding, bm25_tokens)
            values (%s, 'M99002', %s, '4.1', 8, 8, 'B21', 'Ver.2', '{}'::jsonb, %s::vector, %s)
            """,
            (
                corpus["document_id"],
                "制动器间隙调整：B21 及以前用电位器方式，调整量每次不超过 1/8 圈。",
                _vector_text(E1),
                "制动器 间隙 调整 电位器 批次 圈",
            ),
        )
    conn.commit()

    llm = FakeCompleteLlm("1. 按批次对应的方法调整 [M99001]")
    client = make_client(conn, llm=llm)
    session_id = new_session(conn)
    events = chat(client, session_id, "制动器间隙怎么调整")
    names = [name for name, _ in events]
    assert "conflict" in names, "两个批次都命中时必须并列展示"
    conflict = next(payload for name, payload in events if name == "conflict")
    assert len(conflict["options"]) >= 2
    assert "不替你选择" in conflict["conflict"]
