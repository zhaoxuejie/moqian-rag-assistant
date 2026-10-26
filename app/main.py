"""FastAPI 装配入口。

路由分层（architecture §4 / §7.2）：
  /health          → 探活（不依赖 DB）
  /widget/*、/chat → 工程师端（widget 会话链）
  /admin/*         → 治理域（管理后台会话链，双链隔离）

S1 已接入：会话建立、SSE 骨架问答、后台登录（JWT）。
S2/S3：知识源、切片、检索、证据门、引用校验。
S4：widget 支撑接口（引用原文 / 反馈 / 转人工）+ 前端静态托管（frontend/，D17）。
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .logging_setup import RequestIdMiddleware, configure_logging
from .routers import (
    admin_auth,
    admin_config,
    admin_evals,
    admin_logs,
    admin_preview,
    admin_settlements,
    admin_users,
    chat,
    health,
    knowledge,
    widget,
)

logger = logging.getLogger(__name__)

# 静态模板（如 FAQ 导入模板 .xlsx）要靠扩展名给对 Content-Type；
# Windows 的 mimetypes 不保证认识 .xlsx，不注册会退化成 application/octet-stream。
mimetypes.add_type("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx")

# 前端静态产物：frontend/ 由项目自己拥有（D17）。
# 早期原型目录已移除；运行与构建都只读 frontend/。
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
_ADMIN_UI_DIR = _FRONTEND_DIR / "admin"  # 整目录挂载；页面在 pages/ 下，资源用 ../styles 等相对路径
_ADMIN_PAGES_DIR = _ADMIN_UI_DIR / "pages"
_WIDGET_DIR = _FRONTEND_DIR / "widget"
_DESIGN_DIR = _FRONTEND_DIR / "design"


def _contract_error_response(status_code: int, detail: object) -> JSONResponse:
    """把 FastAPI 的 `{"detail": ...}` 转成契约里的 `{code, message, detail?}`。"""
    if isinstance(detail, dict) and "message" in detail:
        body = {
            "code": detail.get("code", "ERROR"),
            "message": detail["message"],
        }
        if "detail" in detail:
            body["detail"] = detail["detail"]
        return JSONResponse(status_code=status_code, content=body)
    if isinstance(detail, list):  # pydantic 校验错误
        return JSONResponse(
            status_code=status_code,
            content={
                "code": "VALIDATION_ERROR",
                "message": "参数不合法",
                "detail": {"errors": detail},
            },
        )
    return JSONResponse(
        status_code=status_code, content={"code": "ERROR", "message": str(detail)}
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # 治理域文档在演示环境必须关掉（第二层防御）。
    #
    # 第一层是 nginx 对 /docs、/redoc、/openapi.json 直接 404；但反代配置是会被人改的，
    # 改错之后接口文档不该跟着一起暴露 —— 那一份 schema 等于把后台接口结构公开。
    # 判定用 DEMO_ENABLED：只在演示环境关，本地开发照常可看。
    demo_mode = settings.demo_enabled
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description=f"制造业设备售后知识助手 · 文档基线 {settings.baseline}",
        docs_url=None if demo_mode else "/docs",
        redoc_url=None if demo_mode else "/redoc",
        openapi_url=None if demo_mode else "/openapi.json",
    )
    app.add_middleware(RequestIdMiddleware)

    # 静态资源（后台 / 浮层 / 设计令牌）URL 不带版本号，浏览器会按启发式规则缓存，
    # 于是「服务器已经改了 CSS，页面还是旧的」反复出现（实测：hint-sub 样式不生效）。
    # 统一加 no-cache：每次回源校验，没变就 304，代价很小。
    @app.middleware("http")
    async def _revalidate_static(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/admin-app/", "/widget-app/", "/ui-design/")):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return _contract_error_response(exc.status_code, exc.detail)

    app.include_router(health.router)
    app.include_router(widget.router)
    app.include_router(chat.router)
    app.include_router(admin_auth.router)
    app.include_router(knowledge.router)
    # 剩余后台页面（页面负责人各自维护；本行只做装配）
    app.include_router(admin_logs.router)
    app.include_router(admin_settlements.router)
    app.include_router(admin_users.router)
    app.include_router(admin_config.router)
    app.include_router(admin_evals.router)
    app.include_router(admin_preview.router)

    # 设计令牌单一来源：两端页面都引 /ui-design/tokens.css（前端目录见 D17）
    if _DESIGN_DIR.is_dir():
        app.mount(
            "/ui-design",
            StaticFiles(directory=str(_DESIGN_DIR)),
            name="ui-design",
        )
    # 同源托管 widget 产物：避免 M0 阶段额外配 CORS（/widget-app/index.html 是宿主演示页）
    if (_WIDGET_DIR / "index.html").is_file():
        app.mount(
            "/widget-app",
            StaticFiles(directory=str(_WIDGET_DIR), html=True),
            name="widget-app",
        )
    # 同源托管后台静态站：挂载 frontend/admin 整目录，页面在 /admin-app/pages/ 下；
    # 根路径重定向到 pages/，保证页面里 ../styles、../controllers 等相对路径可解析。
    @app.get("/admin-app", include_in_schema=False)
    @app.get("/admin-app/", include_in_schema=False)
    async def admin_ui_root() -> RedirectResponse:
        return RedirectResponse(url="/admin-app/pages/", status_code=307)

    if (_ADMIN_PAGES_DIR / "index.html").is_file():
        app.mount(
            "/admin-app",
            StaticFiles(directory=str(_ADMIN_UI_DIR), html=True),
            name="admin-app",
        )

        # 浏览器无论页面有没有声明 icon，都会顺手请求 /favicon.ico；重定向到后台的
        # 品牌图标（品牌绿底 + 白色扳手），免得日志里一直是 404 噪音。
        @app.get("/favicon.ico", include_in_schema=False)
        async def favicon() -> RedirectResponse:
            return RedirectResponse(url="/admin-app/favicon.svg", status_code=307)

    _log_routes(app)
    return app


def _log_routes(app: FastAPI) -> None:
    """启动时打印路由表：一眼看出"该有的接口有没有接上"。"""
    for route in app.routes:
        if isinstance(route, APIRoute):
            methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
            logger.debug("route: %-6s %s", methods, route.path)


app = create_app()
