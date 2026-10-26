"""后台「系统配置」接口（PRD FR-P2 / admin prototype spec §4.8）。

本批次口径（前端必须按 `effect` 展示，**不要读成"保存即生效"**）：

1. 配置值持久化在 `app_config`：一个组一行，key = `config.<group>`，value 为 camelCase JSON；
   未保存过的组回落到当前运行时值（环境变量 / 代码常量）。
2. 运行时读取路径**未改**：问答、检索、浮层文案与安全词表仍分别读
   `app.config.Settings`（环境变量）与 `app/domain.py`、`app/routers/widget.py` 的常量。
   因此本批次保存到 `app_config` 的值**不会自动生效，重启也不会**——响应里的 `effect`
   逐组标注 `mode`（immediate / restartRequired / displayOnly / notWired）与 `wired`，
   `runtime` 给出进程内真实生效值，`notes` 给可读说明。这就是"不把未接线配置假装已生效"的落点；
   契约里 `PUT /admin/config` 的"保存后立即生效"描述与当前实现不一致，已作为未决点上报
   （`contracts/openapi.yaml` 不在本页可改范围）。
3. 密钥只读：`apiKeySet` 表示环境变量里是否配置，`apiKeyMasked` 恒为固定掩码；
   后台**不接收明文密钥写入**（提交 `apiKey` 即 422），避免密钥明文落库。
4. `POST /admin/config/test-llm` 用「已保存的 LLM 配置（缺省回落到环境变量）」真实调用一次
   `LlmClient.complete`；密钥仍取环境变量。成功与失败都以 200 返回（契约 LlmTestResult）。
5. `PUT /admin/theme` 与 `PUT /admin/config` 的 `brand` 组写同一份 `config.brand`
   （契约 Q8「两者是否合并为同一写入口」待定；当前按"同一份数据、两个写入口"处理）。
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from modules.llm import MIN_MAX_TOKENS, LlmClient, LlmConfig, LlmError

from ..branding import DEFAULT_BRAND
from ..config import Settings, get_settings
from ..domain import DEFAULT_HANDOFF_TEXT
from .deps import get_conn, require_governance_admin
from .widget import DEFAULT_EXAMPLES

router = APIRouter(
    prefix="/admin",
    tags=["admin-config"],
    dependencies=[Depends(require_governance_admin)],
)

# app_config 里的配置组：一组一行，便于部分更新与"这组是否保存过"的判断
_SAVED_KEY_PREFIX = "config."
_GROUPS: tuple[str, ...] = (
    "llm",
    "embedding",
    "reranker",
    "retrieval",
    "widget",
    "safety",
    "security",
    "preview",
    "demo",
    "brand",
)

# 每组的可写字段（camelCase，与契约一致）；响应里的附加字段（apiKeyMasked / provider /
# rrfK 等）属于只读观测值，回传时在这里被过滤掉，不落库。
_ALLOWED_KEYS: dict[str, tuple[str, ...]] = {
    "llm": ("baseUrl", "model", "timeoutSec"),
    "embedding": ("baseUrl", "model", "dim", "provider"),
    "reranker": ("baseUrl", "model", "provider"),
    "retrieval": (
        "recallPerRoute",
        "fusion",
        "topK",
        "evidenceThreshold",
        "rrfK",
        "evidenceMinCoverage",
    ),
    "widget": ("welcome", "samples", "handoffText", "brandColor"),
    "safety": ("words",),
    "security": ("captchaEnabled",),
    "preview": ("enabled",),
    # 演示总开关：只收窄不放大（DEMO_ENABLED=false 的部署不会被它打开）
    "demo": ("enabled",),
    "brand": ("systemName", "productName", "orgName", "primaryColor", "logo", "logoFileName"),
}

_THEME_FIELDS: tuple[str, ...] = (
    "systemName",
    "productName",
    "orgName",
    "primaryColor",
    "logo",
    "logoFileName",
)

# 默认值来源说明（本批次不抽公共配置源，见文件末尾"未决点"）：
# - welcome / brandColor / 品牌默认值：与 frontend 的 MOCK.systemConfig 一致；
# - samples / handoffText：直接复用 app 层已有的行业词汇常量（widget.py / domain.py）。
_DEFAULT_WELCOME = "我是售后知识助手，可以按故障现象帮你查手册与历史工单，答案都带出处。"
_DEFAULT_BRAND: dict[str, str] = DEFAULT_BRAND

_HEX_COLOR = r"^#[0-9a-fA-F]{6}$"

# 自定义标识：配置页在浏览器内把 SVG/PNG（≤100KB）读成 data URL，随品牌配置保存。
# 100KB 二进制经 base64 约 137KB，留出前缀余量后字符上限取 200000。
_LOGO_MAX_CHARS = 200_000
_LOGO_MAX_BYTES = 100 * 1024
_LOGO_DATA_URL_PREFIXES = ("data:image/png;base64,", "data:image/svg+xml;base64,")


def _validate_logo(value: str | None) -> str | None:
    """标识只接受内置 default（兼容历史 custom）或 SVG/PNG 的 data URL。

    服务端不信任前端的 100KB 校验：解码 base64 再量真实字节数。
    """
    if value is None:
        return None
    text = value.strip()
    if not text or text in {"default", "custom"}:
        return text
    if not text.startswith(_LOGO_DATA_URL_PREFIXES):
        raise ValueError("标识只支持 SVG / PNG 图片或内置 default")
    if len(text) > _LOGO_MAX_CHARS:
        raise ValueError("标识文件过大：上限 100KB")
    payload = text.split(",", 1)[1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except Exception as exc:  # noqa: BLE001 - 非法 base64 一律按格式错误处理
        raise ValueError("标识文件不是有效的 base64 图片") from exc
    if len(raw) > _LOGO_MAX_BYTES:
        raise ValueError("标识文件过大：上限 100KB")
    return text

# 生效性说明：LLM / 检索 / 安全词表 / 浮层文案已接入运行时读取（下一次问答生效）；
# 已接线组在 effect 中逐组标注 immediate；embedding 维度/前端展示项不写假承诺。
_EFFECT_MODES: dict[str, str] = {
    "llm": "immediate",
    "embedding": "immediate",
    "reranker": "immediate",
    "retrieval": "immediate",
    "widget": "immediate",
    "safety": "immediate",
    "security": "immediate",
    "preview": "immediate",
    "demo": "immediate",
    "brand": "displayOnly",
}

_EFFECT_NOTES: dict[str, str] = {
    "llm": "已接线：保存后下一次问答使用新 baseUrl/model/timeout；API 密钥仍由环境变量管理。",
    "embedding": (
        "已接线：保存后下一次查询使用新 baseUrl/model；**维度不随配置改变**——"
        "`dim` 与 chunks.embedding 绑定，改维度必须重建全部向量。"
    ),
    "reranker": (
        "已接线：保存后下一次问答使用新 baseUrl/model/provider；API 密钥只从环境变量 "
        "RERANKER_API_KEY 读取；调用失败自动回退 RRF，不阻断回答。"
    ),
    "retrieval": (
        "已接线：保存后下一次问答使用新的召回数/证据集大小/RRF k/证据阈值。"
        "embedding 与 reranker 不在本组生效范围。"
    ),
    "widget": "已接线：下一轮拉取 GET /widget/config 时使用新欢迎语/示例/转人工文案/品牌色。",
    "safety": "已接线：下一次问答使用新的安全规则词表；空列表回退环境变量/默认词表。",
    "security": (
        "已接线：开启后登录页会拉取一次性图形验证码，登录必须带 captchaId/captchaCode；"
        "关闭后不再校验。验证码挑战 5 分钟过期且一次性。"
    ),
    "preview": (
        "已接线：关闭后所有管理员内页隐藏右下角「知识自测」入口并收起面板；"
        "只影响入口，不影响抽测接口与指标隔离。"
    ),
    "demo": (
        "已接线：关闭后演示站立即暂停——即使带入口码也不再建立会话、不产生回答，"
        "浮层显示「这个演示需要从官网入口进入」；重新打开即时恢复。"
        "只能收窄：DEMO_ENABLED=false 的部署（本机开发）不会被它打开。"
    ),
    "brand": (
        "后台 layout 与登录页均从公开接口读取品牌（页面加载/保存后刷新）；"
        "登录页不依赖登录态。"
    ),
}

_NOTES: tuple[str, ...] = (
    "LLM / embedding（baseUrl+model）/ 检索参数 / 安全词表 / 浮层文案 / 品牌 / reranker / "
    "登录验证码 / 自测入口已接线；embedding 维度仍固定（改维度需重建向量）。",
    "运行时生效值以响应 runtime 为准（环境变量为底，已保存配置覆盖）。",
    "品牌：后台侧栏与登录页均已读取；安全与自测入口已接入运行时。",
    "reranker 失败（超时/鉴权/上游错误）自动回退 RRF，不阻断回答；密钥只从环境变量读取。",
    "验证码开启后登录页拉取一次性挑战（5 分钟、一次性）；关闭后登录不再校验。"
    "自测入口开关只影响右下角入口，不影响抽测接口与指标隔离。",
    "演示总开关只收窄不放大：关闭后演示站立即暂停（会话 / 提问都拒绝），"
    "DEMO_ENABLED=false 的部署不会被它打开。",
    "密钥：LLM_API_KEY 由部署环境变量管理；接口只回传是否配置与固定掩码，"
    "不接受明文写入（提交 apiKey 返回 422）。",
    "test-llm 用已保存的 LLM 配置（缺省回落环境变量）真实调用一次模型；"
    "测试通过仅代表该端点可达。",
)


# --------------------------------------------------------------------------
# 请求体模型：只做形状校验，是否落库由 _clean_group 再裁剪一次
# --------------------------------------------------------------------------
class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class LlmPatch(_CamelModel):
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    timeout_sec: int | None = Field(default=None, ge=5, le=600)
    # 只读回显字段：允许回传，但从不落库（apiKeySet 永远由环境变量派生）
    api_key_set: bool | None = None
    # 契约没有写入口；若提交非空值则显式 422，绝不静默保存明文密钥
    api_key: str | None = Field(default=None, max_length=500)


class EmbeddingPatch(_CamelModel):
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    dim: int | None = Field(default=None, ge=1, le=16384)
    provider: Literal["local", "remote"] | None = None


class RerankerPatch(_CamelModel):
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    provider: Literal["none", "tencent", "llama_cpp"] | None = None


class RetrievalPatch(_CamelModel):
    recall_per_route: int | None = Field(default=None, ge=1, le=100)
    fusion: str | None = Field(default=None, max_length=40)
    top_k: int | None = Field(default=None, ge=1, le=50)
    evidence_threshold: float | None = Field(default=None, ge=0, le=1)
    rrf_k: int | None = Field(default=None, ge=1, le=1000)
    evidence_min_coverage: float | None = Field(default=None, ge=0, le=1)


class WidgetPatch(_CamelModel):
    welcome: str | None = Field(default=None, min_length=1, max_length=200)
    samples: list[str] | None = Field(default=None, max_length=3)
    handoff_text: str | None = Field(default=None, min_length=1, max_length=200)
    brand_color: str | None = Field(default=None, pattern=_HEX_COLOR)


class SafetyPatch(_CamelModel):
    words: list[str] | None = Field(default=None, max_length=100)


class SecurityPatch(_CamelModel):
    captcha_enabled: bool | None = None


class PreviewPatch(_CamelModel):
    enabled: bool | None = None


class DemoPatch(_CamelModel):
    """演示总开关：false = 暂停（带入口码也不建会话）；true = 恢复。"""

    enabled: bool | None = None


class BrandPatch(_CamelModel):
    system_name: str | None = Field(default=None, min_length=1, max_length=60)
    product_name: str | None = Field(default=None, max_length=60)
    org_name: str | None = Field(default=None, min_length=1, max_length=60)
    primary_color: str | None = Field(default=None, pattern=_HEX_COLOR)
    logo: str | None = Field(default=None, max_length=_LOGO_MAX_CHARS)
    logo_file_name: str | None = Field(default=None, max_length=200)

    @field_validator("logo")
    @classmethod
    def _logo_format(cls, value: str | None) -> str | None:
        return _validate_logo(value)


class ConfigPatch(_CamelModel):
    llm: LlmPatch | None = None
    embedding: EmbeddingPatch | None = None
    reranker: RerankerPatch | None = None
    retrieval: RetrievalPatch | None = None
    widget: WidgetPatch | None = None
    safety: SafetyPatch | None = None
    security: SecurityPatch | None = None
    preview: PreviewPatch | None = None
    demo: DemoPatch | None = None
    brand: BrandPatch | None = None


class ThemePatch(_CamelModel):
    system_name: str | None = Field(default=None, min_length=1, max_length=60)
    product_name: str | None = Field(default=None, max_length=60)
    org_name: str | None = Field(default=None, min_length=1, max_length=60)
    primary_color: str | None = Field(default=None, pattern=_HEX_COLOR)
    logo: str | None = Field(default=None, max_length=_LOGO_MAX_CHARS)
    logo_file_name: str | None = Field(default=None, max_length=200)

    @field_validator("logo")
    @classmethod
    def _logo_format(cls, value: str | None) -> str | None:
        return _validate_logo(value)


# --------------------------------------------------------------------------
# 运行时值（进程内真实生效）与默认值
# --------------------------------------------------------------------------
def _default_widget() -> dict[str, Any]:
    return {
        "welcome": _DEFAULT_WELCOME,
        "samples": [str(item["q"]) for item in DEFAULT_EXAMPLES],
        "handoffText": DEFAULT_HANDOFF_TEXT,
        "brandColor": _DEFAULT_BRAND["primaryColor"],
    }


def _mask_key(api_key: str) -> str | None:
    """密钥只给固定掩码：不回传任何前缀/后缀，避免泄露可用信息。"""
    return "******" if api_key else None


def _runtime_config(
    settings: Settings, saved: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """进程内真实生效的配置（环境变量为底，已保存的已接线组覆盖）。"""
    saved = saved or {}
    api_key = settings.llm_api_key or ""
    llm = saved.get("llm", {})
    embedding = saved.get("embedding", {})
    retrieval = saved.get("retrieval", {})
    safety = saved.get("safety", {})
    security = saved.get("security", {})
    preview = saved.get("preview", {})
    demo = saved.get("demo", {})
    reranker = saved.get("reranker", {})
    widget = {**_default_widget(), **saved.get("widget", {})}
    brand = {**_DEFAULT_BRAND, **saved.get("brand", {})}

    def number(data: dict[str, Any], key: str, default: Any) -> Any:
        value = data.get(key)
        if value in (None, ""):
            return default
        try:
            return type(default)(value)
        except (TypeError, ValueError):
            return default

    return {
        "llm": {
            "baseUrl": llm.get("baseUrl") or settings.llm_base_url,
            "model": llm.get("model") or settings.llm_model,
            "timeoutSec": int(
                number(llm, "timeoutSec", int(round(float(settings.llm_timeout_seconds))))
            ),
            "apiKeySet": bool(api_key),
            "apiKeyMasked": _mask_key(api_key),
        },
        "embedding": {
            "baseUrl": embedding.get("baseUrl") or settings.embedding_base_url,
            "model": embedding.get("model") or settings.embedding_model,
            "dim": int(settings.embedding_dim),
            "provider": "remote"
            if (embedding.get("baseUrl") or settings.embedding_base_url)
            else "local",
        },
        "reranker": {
            "baseUrl": reranker.get("baseUrl") or settings.reranker_base_url,
            "model": reranker.get("model") or settings.reranker_model,
            "provider": reranker.get("provider") or settings.reranker_provider,
        },
        "retrieval": {
            "recallPerRoute": int(
                number(retrieval, "recallPerRoute", int(settings.retrieval_top_k_dense))
            ),
            "fusion": "RRF",
            "topK": int(number(retrieval, "topK", int(settings.retrieval_final_top_n))),
            "evidenceThreshold": float(
                number(retrieval, "evidenceThreshold", float(settings.retrieval_min_score))
            ),
            "recallPerRouteBm25": int(
                number(retrieval, "recallPerRoute", int(settings.retrieval_top_k_bm25))
            ),
            "rrfK": int(number(retrieval, "rrfK", int(settings.retrieval_rrf_k))),
            "evidenceMinCoverage": float(
                number(
                    retrieval,
                    "evidenceMinCoverage",
                    float(settings.evidence_min_coverage),
                )
            ),
        },
        "widget": widget,
        "safety": {
            "words": list(safety.get("words") or settings.safety_word_list()),
        },
        "security": {
            "captchaEnabled": bool(
                security.get("captchaEnabled", settings.captcha_enabled)
            )
        },
        "preview": {
            "enabled": bool(preview.get("enabled", settings.preview_enabled))
        },
        # 进程内真实生效值：环境变量启用 且 后台开关未关闭
        "demo": {
            "enabled": bool(settings.demo_enabled) and demo.get("enabled", True) is not False,
            # 部署是否启用了演示（DEMO_ENABLED）；false 时后台开关置灰，避免"打开也无效"
            "envEnabled": bool(settings.demo_enabled),
        },
        "brand": brand,
    }


def _effect_summary() -> dict[str, Any]:
    groups = {
        group: {
            "mode": _EFFECT_MODES[group],
            "wired": _EFFECT_MODES[group] == "immediate",
            "note": _EFFECT_NOTES[group],
        }
        for group in _GROUPS
    }
    buckets: dict[str, list[str]] = {
        "immediate": [],
        "restartRequired": [],
        "displayOnly": [],
        "notWired": [],
    }
    for group in _GROUPS:
        buckets[_EFFECT_MODES[group]].append(group)
    return {"groups": groups, **buckets, "notes": list(_NOTES)}


# --------------------------------------------------------------------------
# app_config 读写
# --------------------------------------------------------------------------
def _read_saved(conn: psycopg.Connection) -> dict[str, dict[str, Any]]:
    keys = [_SAVED_KEY_PREFIX + group for group in _GROUPS]
    with conn.cursor() as cur:
        cur.execute("select key, value from app_config where key = any(%s)", (keys,))
        rows = cur.fetchall()
    saved: dict[str, dict[str, Any]] = {}
    for key, value in rows:
        group = str(key)[len(_SAVED_KEY_PREFIX) :]
        if group in _GROUPS and isinstance(value, dict):
            saved[group] = dict(value)
    return saved


def _upsert_group(conn: psycopg.Connection, group: str, data: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into app_config(key, value, updated_at)
            values (%s, %s, now())
            on conflict (key) do update
               set value = excluded.value, updated_at = now()
            """,
            (_SAVED_KEY_PREFIX + group, Jsonb(data)),
        )


def _merge_saved(
    runtime: dict[str, Any], saved: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """配置值 = 已保存值覆盖在运行时值上；密钥相关字段永远以运行时为准。"""
    view: dict[str, Any] = {}
    for group in _GROUPS:
        current = dict(runtime[group])
        data = saved.get(group) or {}
        if group == "llm":
            data = {k: v for k, v in data.items() if k not in {"apiKeySet", "apiKeyMasked"}}
            merged = {**current, **data}
            merged["apiKeySet"] = runtime["llm"]["apiKeySet"]
            merged["apiKeyMasked"] = runtime["llm"]["apiKeyMasked"]
            view[group] = merged
        elif group in ("brand", "widget"):
            # 用默认值补齐早期快照缺失的字段
            defaults = _DEFAULT_BRAND if group == "brand" else _default_widget()
            allowed = {k: v for k, v in data.items() if k in _ALLOWED_KEYS[group]}
            view[group] = {**defaults, **allowed}
        else:
            allowed = {k: v for k, v in data.items() if k in _ALLOWED_KEYS[group]}
            view[group] = {**current, **allowed}
    return view


def _config_response(
    conn: psycopg.Connection, settings: Settings
) -> dict[str, Any]:
    saved = _read_saved(conn)
    runtime = _runtime_config(settings, saved)
    view = _merge_saved(runtime, saved)
    return {
        **view,
        # 以下为契约外的观测字段（OpenAPI 默认允许 additionalProperties）：
        "runtime": runtime,
        "effect": _effect_summary(),
        "savedGroups": sorted(saved),
    }


def _clean_group(group: str, data: dict[str, Any]) -> dict[str, Any]:
    """只保留可写字段并做字符串裁剪/去重；不改变数值口径。"""
    allowed = _ALLOWED_KEYS[group]
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if key not in allowed:
            continue
        if isinstance(value, str):
            value = value.strip()
            if key == "logoFileName":
                cleaned[key] = value
            elif value:
                cleaned[key] = value
            continue
        if group == "safety" and key == "words":
            words: list[str] = []
            for word in value:
                text = str(word).strip()
                if text and text not in words:
                    words.append(text)
            cleaned[key] = words
            continue
        if group == "widget" and key == "samples":
            cleaned[key] = [str(item).strip() for item in value if str(item).strip()][:3]
            continue
        cleaned[key] = value
    return cleaned


# --------------------------------------------------------------------------
# 端点
# --------------------------------------------------------------------------
@router.get("/config")
def get_admin_config(
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """读配置：各组是"配置值"（已保存值覆盖运行时值），`runtime` 才是当前生效值。"""
    return _config_response(conn, settings)


@router.put("/config")
def put_admin_config(
    payload: ConfigPatch,
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """部分更新：只写提交的组；写入 app_config，生效性见响应 `effect`。"""
    submitted = payload.model_dump(by_alias=True, exclude_unset=True, exclude_none=True)

    api_key = str((submitted.get("llm") or {}).get("apiKey") or "").strip()
    if api_key:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LLM_API_KEY_NOT_WRITABLE",
                "message": (
                    "后台不接收明文 API 密钥（避免明文落库）：请通过部署环境变量 "
                    "LLM_API_KEY 配置后重启服务；本接口只回传是否已配置。"
                ),
            },
        )

    changed: list[str] = []
    for group in _GROUPS:
        raw = submitted.get(group)
        if raw is None:
            continue
        data = _clean_group(group, raw)
        if not data:
            continue
        _upsert_group(conn, group, data)
        changed.append(group)
    if changed:
        conn.commit()

    body = _config_response(conn, settings)
    body["savedGroups"] = sorted(set(body["savedGroups"]) | set(changed))
    return body


@router.put("/theme")
def put_admin_theme(
    payload: ThemePatch,
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """品牌主题写入口：与 config.brand 同一份数据（契约 Q8 待定，见模块 docstring）。"""
    patch = _clean_group("brand", payload.model_dump(by_alias=True, exclude_unset=True))
    saved = _read_saved(conn)
    if patch:
        brand = {**_DEFAULT_BRAND, **saved.get("brand", {}), **patch}
        _upsert_group(
            conn,
            "brand",
            {key: brand[key] for key in _ALLOWED_KEYS["brand"] if key in brand},
        )
        conn.commit()
        saved = _read_saved(conn)
    brand = {**_DEFAULT_BRAND, **saved.get("brand", {})}
    return {key: brand[key] for key in _THEME_FIELDS if key in brand}


# --------------------------------------------------------------------------
# 连通性测试
# --------------------------------------------------------------------------
_TEST_PROMPT = "这是一个连通性测试，请只回复：pong"


@dataclass
class LlmProbe:
    """test-llm 实际使用的客户端与来源，便于响应里标注"测的是哪份配置"。"""

    client: LlmClient
    model: str
    base_url: str
    source: str


def get_llm_probe(
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
) -> LlmProbe:
    """可覆盖依赖：测试用 `app.dependency_overrides` 注入假客户端，不联网。"""
    saved = _read_saved(conn).get("llm", {})
    base_url = str(saved.get("baseUrl") or settings.llm_base_url)
    model = str(saved.get("model") or settings.llm_model)
    try:
        timeout = float(saved.get("timeoutSec") or settings.llm_timeout_seconds)
    except (TypeError, ValueError):
        timeout = float(settings.llm_timeout_seconds)
    config = LlmConfig(
        base_url=base_url,
        model=model,
        api_key=settings.llm_api_key or "",
        timeout_seconds=timeout,
        max_tokens=int(settings.llm_max_tokens),
        max_retries=int(settings.llm_max_retries),
    )
    source = "app_config" if (saved.get("baseUrl") or saved.get("model")) else "env"
    return LlmProbe(client=LlmClient(config), model=model, base_url=base_url, source=source)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@router.post("/config/test-llm")
def test_llm(
    probe: LlmProbe = Depends(get_llm_probe),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """真实调用一次 `LlmClient.complete`；失败原因可读，不抛 5xx（契约 LlmTestResult）。"""
    if not (settings.llm_api_key or ""):
        return {
            "ok": False,
            "model": probe.model or None,
            "latencyMs": None,
            "firstTokenMs": None,
            "error": "未配置 LLM API 密钥（环境变量 LLM_API_KEY）；后台不接收密钥写入。",
            "errorCode": "LLM_CONFIG",
            "source": probe.source,
        }

    started = time.perf_counter()
    try:
        result = probe.client.complete(
            [{"role": "user", "content": _TEST_PROMPT}], max_tokens=MIN_MAX_TOKENS
        )
    except LlmError as exc:
        return {
            "ok": False,
            "model": probe.model or None,
            "latencyMs": _elapsed_ms(started),
            "firstTokenMs": None,
            "error": exc.message,
            "errorCode": exc.code,
            "source": probe.source,
        }
    except Exception as exc:  # noqa: BLE001 - 测试端点要把未知异常也变成可读结果
        return {
            "ok": False,
            "model": probe.model or None,
            "latencyMs": _elapsed_ms(started),
            "firstTokenMs": None,
            "error": f"请求异常（{type(exc).__name__}）：{str(exc)[:200]}",
            "errorCode": "LLM_ERROR",
            "source": probe.source,
        }

    return {
        "ok": True,
        "model": result.model or probe.model,
        "latencyMs": result.elapsed_ms,
        "firstTokenMs": None,
        "error": None,
        "source": probe.source,
    }
