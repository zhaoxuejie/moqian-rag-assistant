"""运行时配置接线（D-012 / 需要数据库）。

钉住：
- `app_config.config.llm` 覆盖 LLM baseUrl/model；
- `config.embedding` 覆盖 embedding baseUrl/model（dim 不覆盖，必须与库一致）；
- `config.safety` 覆盖问答安全词表（在检索/模型之前生效）；
- `config.widget` 覆盖 `/widget/config` 下发的文案；
- 检索参数覆盖只影响下一次构建的 RetrievalSettings。
"""

from __future__ import annotations

import pytest
from conftest import requires_db
from psycopg.types.json import Jsonb

from app.config import Settings
from app.rag import build_retrieval_config
from app.rag_runner import run_turn
from app.routers.deps import get_embedder, get_llm, get_reranker
from app.routers.widget import widget_config

pytestmark = requires_db

KEYS = (
    "config.llm",
    "config.embedding",
    "config.reranker",
    "config.safety",
    "config.widget",
)


@pytest.fixture()
def saved(conn):
    """隔离运行时要读的 5 个配置组：先快照再清空，用例结束原样恢复。

    这些键在真实开发库里通常有已保存值（模型端点 / 检索参数 / 词表等）；
    只删除不恢复会把本机配置清空，页面看起来像"配置丢了"。
    """
    with conn.cursor() as cur:
        cur.execute(
            "select key, value from app_config where key = any(%s)", (list(KEYS),)
        )
        snapshot = cur.fetchall()
        cur.execute("delete from app_config where key = any(%s)", (list(KEYS),))
    conn.commit()
    try:
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute("delete from app_config where key = any(%s)", (list(KEYS),))
            for key, value in snapshot:
                cur.execute(
                    "insert into app_config(key, value, updated_at) values (%s, %s, now()) "
                    "on conflict (key) do update "
                    "set value = excluded.value, updated_at = now()",
                    (key, Jsonb(value)),
                )
        conn.commit()


def _put(conn, key: str, value: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "insert into app_config(key, value, updated_at) values (%s, %s, now()) "
            "on conflict (key) do update set value = excluded.value, updated_at = now()",
            (key, Jsonb(value)),
        )
    conn.commit()


def test_llm_override_used_by_dependency(saved) -> None:
    _put(
        saved,
        "config.llm",
        {"baseUrl": "https://override.invalid/v1", "model": "override-model"},
    )
    settings = Settings(
        llm_base_url="https://env.invalid/v1",
        llm_model="env-model",
        llm_api_key="k",
    )
    client = get_llm(settings=settings, conn=saved)
    assert client.config.base_url == "https://override.invalid/v1"
    assert client.config.model == "override-model"


def test_embedding_override_used_by_dependency(saved) -> None:
    _put(
        saved,
        "config.embedding",
        {"baseUrl": "https://emb.invalid/v1", "model": "override-emb", "dim": 768},
    )
    settings = Settings(
        embedding_base_url="https://env.invalid/v1",
        embedding_model="env-emb",
        embedding_dim=1024,
    )
    client = get_embedder(settings=settings, conn=saved)
    assert client.config.base_url == "https://emb.invalid/v1"
    assert client.config.model == "override-emb"
    assert client.config.dim == 1024, "dim 必须保持与 chunks.embedding 一致，不能被配置静默改掉"


def test_safety_override_blocks_before_retrieval(saved) -> None:
    _put(saved, "config.safety", {"words": ["测试红线"]})
    settings = Settings()
    outcome = run_turn(
        settings=settings,
        conn=saved,
        llm=None,
        embedder=None,
        question="测试红线时应该怎么做？",
    )
    assert outcome.gate_reason == "safe"
    assert outcome.safety_word == "测试红线"
    assert outcome.retrieval is None, "安全拦截必须在检索之前"


def test_widget_override_used_by_config_endpoint(saved) -> None:
    _put(
        saved,
        "config.widget",
        {
            "welcome": "运行时欢迎语",
            "samples": ["示例甲", "示例乙"],
            "handoffText": "运行时转人工文案",
            "brandColor": "#123456",
        },
    )
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.routers.deps import get_conn

    app = create_app()
    app.dependency_overrides[get_conn] = lambda: saved
    body = TestClient(app).get("/widget/config").json()
    assert body["welcome"] == "运行时欢迎语"
    assert [item["q"] for item in body["examples"]] == ["示例甲", "示例乙"]
    assert body["handoffText"] == "运行时转人工文案"
    assert body["brandColor"] == "#123456"


def test_reranker_override_used_by_dependency(saved) -> None:
    """保存的 reranker 配置覆盖环境变量默认值；密钥仍来自 Settings。"""
    from psycopg.types.json import Jsonb

    with saved.cursor() as cur:
        cur.execute(
            "insert into app_config(key, value, updated_at) values "
            "('config.reranker', %s, now()) on conflict (key) do update set value = excluded.value",
            (
                Jsonb(
                    {
                        "provider": "tencent",
                        "baseUrl": "https://rerank.example/v1",
                        "model": "bge-reranker-v2-m3",
                    }
                ),
            ),
        )
    saved.commit()
    settings = Settings(
        reranker_provider="none",
        reranker_base_url="http://127.0.0.1:9997",
        reranker_api_key="test-key",
    )
    client = get_reranker(settings=settings, conn=saved)
    assert client is not None
    assert client.config.base_url == "https://rerank.example/v1"
    assert client.config.model == "bge-reranker-v2-m3"
    assert client.config.api_key == "test-key"


def test_retrieval_config_accepts_overrides() -> None:
    from app.config import Settings

    settings = Settings()
    cfg = build_retrieval_config(settings, {"recallPerRoute": 33, "topK": 7, "rrfK": 40})
    assert cfg.top_k_dense == 33
    assert cfg.top_k_bm25 == 33
    assert cfg.final_top_n == 7
    assert cfg.rrf_k == 40
    # 非法值回退环境变量，不抛错
    cfg2 = build_retrieval_config(settings, {"topK": "bad", "recallPerRoute": None})
    assert cfg2.final_top_n == settings.retrieval_final_top_n
    assert cfg2.top_k_dense == settings.retrieval_top_k_dense


def test_widget_config_import_is_callable(saved) -> None:
    """确保 `/widget/config` 的依赖签名可直接用（防止路由重构后测试失联）。"""
    body = widget_config(conn=saved, settings=Settings())
    assert body["examples"]
