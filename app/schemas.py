"""接口请求/响应模型（与 `contracts/openapi.yaml` 一一对应，camelCase）。

只放"形状"：不做业务校验之外的推断，字段缺失一律按契约留空，不替用户补全
（纪律 2：未知字段留空走全库检索）。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CamelModel(BaseModel):
    """契约用 camelCase，Python 侧保持 snake_case。"""

    model_config = ConfigDict(populate_by_name=True)


# ---------- widget ----------
class HostContext(CamelModel):
    ticket_no: str | None = Field(default=None, alias="ticketNo")
    device_model: str | None = Field(default=None, alias="deviceModel")
    device_name: str | None = Field(default=None, alias="deviceName")
    batch: str | None = None

    def to_dict(self) -> dict:
        return self.model_dump(by_alias=True)


class IdentityDeclaration(CamelModel):
    source: Literal["gateway_sso", "host_injected", "password_login", "anonymous"] = "anonymous"
    claimed_engineer_id: str | None = Field(default=None, alias="claimedEngineerId")


class SessionRequest(CamelModel):
    identity: IdentityDeclaration
    host_context: HostContext | None = Field(default=None, alias="hostContext")
    # 演示站入口码：由宿主页从 URL 读取后透传；非演示环境忽略
    demo_code: str | None = Field(default=None, alias="demoCode", max_length=128)


class ClarifySelection(CamelModel):
    field: Literal["batch", "model", "alarm", "symptom"]
    value: str
    label: str | None = None


class ChatRequest(CamelModel):
    session_id: str = Field(alias="sessionId")
    question: str = Field(min_length=1, max_length=4000)
    context: HostContext | None = None
    clarify: ClarifySelection | None = None


class FeedbackRequest(CamelModel):
    """`POST /widget/feedback`：三个按钮的互斥规则由前端呈现，后端只落库。"""

    session_id: str = Field(alias="sessionId")
    message_id: str = Field(alias="messageId")
    type: Literal["resolved", "unresolved", "saved"]
    note: str | None = Field(default=None, max_length=2000)


class HandoffRequest(CamelModel):
    session_id: str = Field(alias="sessionId")
    message_id: str | None = Field(default=None, alias="messageId")


# ---------- admin ----------
class LoginRequest(CamelModel):
    account: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    captcha_id: str | None = Field(default=None, alias="captchaId")
    captcha_code: str | None = Field(default=None, alias="captchaCode")


class DocumentPatch(CamelModel):
    """`PATCH /admin/documents/{id}`：只改显式传入的字段（exclude_unset 语义）。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    doc_code: str | None = Field(default=None, alias="docCode", max_length=64)
    model: str | None = Field(default=None, max_length=64)
    batch: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=64)
    effective: date | None = None
    lifecycle: Literal["current", "superseded", "disabled", "archived"] | None = None


class DocumentEnableRequest(CamelModel):
    enabled: bool


class ChunkPatch(CamelModel):
    """`PATCH /admin/chunks/{id}`：M0 支持出处元数据与启停用；正文编辑需重算向量，暂不开放。"""

    text: str | None = Field(default=None, min_length=1, max_length=8000)
    status: Literal["active", "disabled"] | None = None
    section_no: str | None = Field(default=None, alias="sectionNo", max_length=64)
    model: str | None = Field(default=None, max_length=64)
    batch: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=64)


class JobActionRequest(CamelModel):
    action: Literal["pause", "resume", "cancel"]
