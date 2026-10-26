"""单轮追问（D-005 / widget spec §4.3A）的确定性测试。"""

from __future__ import annotations

import json

import pytest
from conftest import FakeEmbedder, requires_db
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.rag_runner import build_batch_ask, build_structured, run_turn
from app.routers.deps import get_conn, get_embedder, get_reranker
from app.schemas import ClarifySelection, HostContext
from app.sessions import create_session

pytestmark = requires_db

MODEL = "ZX-300"
QUESTION = "平层误差偏大怎么调整？"


@pytest.fixture()
def batch_corpus(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into documents (source_type, title, device_model, status, meta_json)
            values ('pdf', 'D-005 追问测试手册', %s, 'parsed', '{"category": "manual"}'::jsonb)
            returning id
            """,
            (MODEL,),
        )
        document_id = int(cur.fetchone()[0])
        for index, batch in enumerate(("B21", "B22"), start=1):
            cur.execute(
                """
                insert into chunks
                    (document_id, evidence_id, content, batch, manual_version,
                     meta_json, bm25_tokens)
                values (%s, %s, %s, %s, 'Ver.3', '{}'::jsonb, %s)
                """,
                (
                    document_id,
                    f"M99{index:03d}",
                    f"{batch} 批次平层调整：按该批次方法执行。",
                    batch,
                    "平层 调整 批次 方法",
                ),
            )
    conn.commit()
    yield {"document_id": document_id}
    with conn.cursor() as cur:
        cur.execute("delete from documents where id = %s", (document_id,))
    conn.commit()


def _context() -> HostContext:
    return HostContext(device_model=MODEL)


def test_batch_ask_options_and_one_round(conn, batch_corpus) -> None:
    structured = build_structured(QUESTION, context=_context())
    ask = build_batch_ask(conn, QUESTION, structured, None)
    assert ask is not None
    assert ask["field"] == "batch"
    assert [item["value"] for item in ask["options"]] == ["B21", "B22", "unknown"]

    outcome = run_turn(
        settings=Settings(),
        conn=conn,
        llm=None,
        embedder=None,
        question=QUESTION,
        context=_context(),
    )
    assert outcome.ask is not None
    assert outcome.ask["field"] == "batch"
    assert outcome.gate_reason == "ask"

    # 已带 clarify 的请求不得再追问（一轮为限）
    again = ClarifySelection(field="batch", value="B22")
    assert build_batch_ask(conn, QUESTION, structured, again) is None


def test_unknown_choice_keeps_batch_empty_and_marks_origin() -> None:
    structured = build_structured(
        QUESTION,
        context=_context(),
        clarify=ClarifySelection(field="batch", value="unknown"),
    )
    assert structured["batch"] is None
    assert structured["batchOrigin"] == "user_uncertain"
    assert "batch" in structured["missing"]


def test_non_sensitive_question_does_not_ask(conn, batch_corpus) -> None:
    question = "电梯运行时有异响，先看哪里？"
    structured = build_structured(question, context=_context())
    assert build_batch_ask(conn, question, structured, None) is None


def test_fault_code_question_does_not_ask(conn, batch_corpus) -> None:
    """故障码问题直接答（处置摘编已按批次写明参数），不因含"平层"被误拦成追问。"""
    question = "HY-E013 平层感应器故障怎么处理？"
    structured = build_structured(question, context=_context())
    assert build_batch_ask(conn, question, structured, None) is None


def test_chat_emits_ask_event_before_retrieval(conn, batch_corpus) -> None:
    session = create_session(
        conn,
        requested_source="host_injected",
        claimed_account=None,
        host_context={"deviceModel": MODEL},
    )
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_settings] = lambda: Settings(chat_mode="pipeline")
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    app.dependency_overrides[get_reranker] = lambda: None
    client = TestClient(app)
    try:
        resp = client.post(
            "/chat",
            headers={"X-Widget-Session": session["sessionId"]},
            json={
                "sessionId": session["sessionId"],
                "question": QUESTION,
                "context": {"deviceModel": MODEL},
            },
        )
        assert resp.status_code == 200, resp.text
        events = []
        for block in resp.text.strip().split("\n\n"):
            name = None
            data = None
            for line in block.splitlines():
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[len("data:") :].strip())
            if name:
                events.append((name, data))
        names = [name for name, _ in events]
        assert names == ["structured", "ask"], names
        ask = events[1][1]["ask"]
        assert ask["field"] == "batch"
        assert [item["value"] for item in ask["options"]] == ["B21", "B22", "unknown"]
    finally:
        session_pk = int(session["sessionId"].split("-")[-1])
        with conn.cursor() as cur:
            cur.execute("delete from sessions where id = %s", (session_pk,))
        conn.commit()
