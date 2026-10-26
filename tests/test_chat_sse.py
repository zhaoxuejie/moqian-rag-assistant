"""`/chat` SSE 行为测试（需要数据库 + 假 LLM，不联网）。

验证的是**契约里的行为约束**，不是措辞：
- 事件顺序 structured → steps → done；
- 骨架模式每帧 steps 不带引用，done 明确 `skeleton: true`（不假装是正式答案）；
- 安全话题只出 `safe`，不出任何步骤（纪律 3）；
- 没有会话头 / 会话无效 → 401（双链前置校验）。

正式 pipeline 的检索/证据门/引用校验用例在 `test_chat_pipeline.py`。
"""

from __future__ import annotations

from conftest import FakeEmbedder, requires_db
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.routers.deps import get_conn, get_embedder, get_llm, get_reranker
from app.sessions import create_session
from modules.llm import StreamStats

pytestmark = requires_db


class FakeLlm:
    """假 LLM：按块吐字，模拟真实流式的 chunk 序列。"""

    def __init__(self, chunks: list[str] | None = None, error: Exception | None = None) -> None:
        self.chunks = chunks or ["链路", "正常，", "已连通。"]
        self.error = error
        self.calls = 0

    def stream_with_stats(self, messages, **kwargs):
        self.calls += 1
        stats = StreamStats()

        def iterator():
            for chunk in self.chunks:
                stats.chunks += 1
                stats.chars += len(chunk)
                stats.first_token_ms = stats.first_token_ms or 10
                yield chunk
            if self.error:
                raise self.error

        return iterator(), stats


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    import json

    events: list[tuple[str, dict]] = []
    for block in raw.strip().split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:") :].strip())
        if name:
            events.append((name, data))
    return events


def make_client(
    conn, *, settings: Settings | None = None, llm: FakeLlm | None = None
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_conn] = lambda: conn
    app.dependency_overrides[get_settings] = lambda: settings or Settings(chat_mode="skeleton")
    app.dependency_overrides[get_llm] = lambda: llm or FakeLlm()
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    app.dependency_overrides[get_reranker] = lambda: None
    return TestClient(app)


def new_session(conn, *, claimed: str | None = None) -> str:
    payload = create_session(
        conn,
        requested_source="host_injected",
        claimed_account=claimed,
        host_context={"deviceModel": "ZX-300", "batch": "B23"},
    )
    return payload["sessionId"]


def test_skeleton_stream_order_and_marker(conn) -> None:
    session_id = new_session(conn)
    client = make_client(conn)

    resp = client.post(
        "/chat",
        json={"sessionId": session_id, "question": "你好，能听到吗"},
        headers={"X-Widget-Session": session_id},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["Cache-Control"] == "no-cache"

    events = parse_sse(resp.text)
    names = [name for name, _ in events]
    assert names[0] == "structured"
    assert names[-1] == "done"
    assert "steps" in names
    assert "citations" not in names, "骨架模式不得发引用事件"

    structured = events[0][1]["structured"]
    assert structured["model"] == "ZX-300"
    assert structured["batch"] == "B23"
    assert structured["batchOrigin"] == "host_context"

    steps = [payload for name, payload in events if name == "steps"]
    assert [step["index"] for step in steps] == list(range(1, len(steps) + 1))
    assert all(step["citations"] == [] for step in steps), "骨架模式步骤不带证据"

    done = events[-1][1]
    assert done["skeleton"] is True
    assert done["citationsCount"] == 0
    assert done["messageId"].startswith("msg-")


def test_safety_word_blocks_before_llm(conn) -> None:
    session_id = new_session(conn)
    llm = FakeLlm()
    client = make_client(conn, llm=llm)

    resp = client.post(
        "/chat",
        json={"sessionId": session_id, "question": "安全回路短接的方法是什么"},
        headers={"X-Widget-Session": session_id},
    )
    events = parse_sse(resp.text)
    names = [name for name, _ in events]
    assert names == ["structured", "safe"], "安全话题只能出 safe，不能有步骤"
    assert llm.calls == 0, "安全话题不该发给模型"
    safe = events[1][1]["safe"]
    assert "不提供操作步骤" in safe["title"]
    assert safe["citations"] == []


def test_missing_session_header_is_401(conn) -> None:
    client = make_client(conn)
    resp = client.post("/chat", json={"sessionId": "ses-1", "question": "你好"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "UNAUTHORIZED"


def test_unknown_session_is_401(conn) -> None:
    client = make_client(conn)
    resp = client.post(
        "/chat",
        json={"sessionId": "ses-999999", "question": "你好"},
        headers={"X-Widget-Session": "ses-999999"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "SESSION_INVALID"


def test_llm_failure_becomes_error_event(conn) -> None:
    from modules.llm import LlmTimeoutError

    session_id = new_session(conn)
    client = make_client(conn, llm=FakeLlm(chunks=["半句"], error=LlmTimeoutError("超时")))

    resp = client.post(
        "/chat",
        json={"sessionId": session_id, "question": "问个问题"},
        headers={"X-Widget-Session": session_id},
    )
    events = parse_sse(resp.text)
    names = [name for name, _ in events]
    assert names[-1] == "error"
    assert events[-1][1]["error"]["retryable"] is True


def test_session_creation_identified_and_anonymous(conn) -> None:
    """身份：在册工号 → identified；未在册 → 不阻断但未识别。"""
    from uuid import uuid4

    from app.security import hash_password
    from app.users import create_user

    # 用一次性账号，别依赖（也别改动）本机真实账号的启停状态：
    # 本机可能有人把测试号禁用，测试若复用它就会误报。
    account = f"T-SSE-{uuid4().hex[:8]}"
    create_user(
        conn, account=account, name="测试员", password_hash=hash_password("Test-Pass-1")
    )
    try:
        client = make_client(conn)
        ok = client.post(
            "/widget/session",
            json={"identity": {"source": "host_injected", "claimedEngineerId": account}},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["engineer"]["identified"] is True
        assert body["engineer"]["account"] == account

        unknown = client.post(
            "/widget/session",
            json={"identity": {"source": "host_injected", "claimedEngineerId": "T-NOT-EXIST"}},
        )
        unknown_body = unknown.json()
        assert unknown_body["engineer"]["identified"] is False
        assert unknown_body["engineer"]["claimed"] == "T-NOT-EXIST"
        assert unknown_body["sessionId"].startswith("ses-")
    finally:
        # 先删会话（engineer_id 外键），再删临时账号
        with conn.cursor() as cur:
            cur.execute(
                "delete from sessions where engineer_id = "
                "(select id from users where account = %s)",
                (account,),
            )
            cur.execute("delete from users where account = %s", (account,))
        conn.commit()


def test_widget_config_endpoint(conn) -> None:
    client = make_client(conn)
    resp = client.get("/widget/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["welcome"] and body["examples"]
    assert body["handoffText"]
