"""S0 冒烟测试：/health 与配置读取；S4/S5 追加静态产物托管；P1 追加版本可观测。"""

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import _detect_version
from app.main import create_app

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_health_ok() -> None:
    client = TestClient(create_app())
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "moqian-service-assistant"
    assert "baseline" not in body


def test_health_reports_version_and_build() -> None:
    """/health 要能回答「线上跑的是哪一版」。

    版本号来自包元数据，构建信息由部署脚本注入（Dockerfile ARG → ENV）。
    字段缺失时管理台的版本徽标就只能显示「版本未知」——所以这里锁死三个字段存在。
    """
    body = TestClient(create_app()).get("/health").json()
    assert body["version"]
    # 0.1.0 是历史上写死的占位值；再出现说明有人又把版本号写进配置了
    assert body["version"] != "0.1.0"
    assert "buildSha" in body
    assert "buildTime" in body


def test_version_override_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """排障用：不重新构建镜像也能临时确认「线上到底是哪一版」。"""
    monkeypatch.setenv("APP_VERSION", "9.9.9")
    assert _detect_version() == "9.9.9"


def test_version_matches_pyproject() -> None:
    """版本号唯一来源是 pyproject.toml。

    改了 pyproject 但没重装（即镜像没重建）时，包元数据还是旧值 ——
    这个断言就是那道提醒：发版前先确认版本真的进了镜像。
    """
    declared = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]["version"]
    detected = _detect_version()
    if detected == "0.0.0-dev":
        pytest.skip("包元数据不可用（未 pip install），跳过一致性检查")
    assert detected == declared, (
        f"版本不一致：元数据 {detected} / pyproject {declared}；"
        "改版本后需 pip install -e . 或重建镜像"
    )


def test_docs_closed_in_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """演示环境关掉 /docs、/redoc、/openapi.json（nginx 404 之外的第二层）。

    反代配置会被改，改错之后接口文档不该跟着暴露 —— 那份 schema 等于公开后台结构。
    """
    from app.config import get_settings

    monkeypatch.setenv("DEMO_ENABLED", "true")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, f"{path} 在演示模式下应 404"
    finally:
        get_settings.cache_clear()


def test_docs_open_in_dev_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """本地开发仍然要能看接口文档。

    `get_settings` 是 lru_cache 的：上一个用例改过环境变量，这里必须显式清缓存，
    否则读到的是上一个用例留下的 Settings —— 这种"用例间互相污染"最容易变成偶发失败。
    """
    from app.config import get_settings

    monkeypatch.setenv("DEMO_ENABLED", "false")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        assert client.get("/openapi.json").status_code == 200
    finally:
        get_settings.cache_clear()


def test_llm_settings_alias() -> None:
    # 兼容旧变量名：.env 里是 BASE_URL / MODEL / API_KEY
    from app.config import get_settings

    s = get_settings()
    assert s.llm_base_url.startswith("http")
    assert s.llm_model


def test_widget_static_hosted_same_origin() -> None:
    """widget 由 app 同源托管：宿主页两行 script 接入的前提（frontend/widget）。"""
    client = TestClient(create_app())
    resp = client.get("/widget-app/loader.js")
    assert resp.status_code == 200
    assert "moqian:context" in resp.text


def test_health_env_label_is_display_only() -> None:
    """顶栏环境名来自 .env 的 APP_ENV_LABEL，只给人看；判定仍看 env。

    这样同一份代码在本地与线上是两种面貌（本地环境 / 生产环境），
    而不需要靠"改代码/开分支"来区分环境。
    """
    from app.config import Settings
    from app.routers.health import health

    prod = health(Settings(app_env="docker", app_env_label="生产环境"))
    assert prod["envLabel"] == "生产环境"
    assert prod["env"] == "docker"

    local = health(Settings(app_env="dev"))
    assert local["envLabel"] == "本地环境", "默认值应是「本地环境」，线上靠 .env 覆盖"


def test_admin_ui_static_hosted() -> None:
    """admin 是原型多页静态站（D16/D17），由 app 同源托管在 /admin-app。"""
    index = _REPO_ROOT / "frontend" / "admin" / "pages" / "index.html"
    assert index.is_file(), "frontend/admin/pages 静态站缺失"
    client = TestClient(create_app())
    resp = client.get("/admin-app/")
    assert resp.status_code == 200
    assert "知识源管理" in resp.text
    login = client.get("/admin-app/pages/login.html")
    assert login.status_code == 200
    assert "治理后台" in login.text or "售后知识助手" in login.text
