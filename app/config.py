"""应用配置：统一从环境变量 / .env 读取（pydantic-settings）。

约定：
- 所有模块自有前缀配置（RETRIEVAL_*、DOC_INGEST_* 等），行业词汇不进 modules；
- 旧变量名（BASE_URL / MODEL / API_KEY）继续兼容，便于与 .env 现状对接。
"""

import os
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DIST_NAME = "moqian-service-assistant"


def _detect_version() -> str:
    """版本号的唯一来源是 `pyproject.toml`（打包后读包元数据）。

    为什么不在配置里写死字符串：写死的版本号在第二次发版时就会变成假话，
    而且是那种"页面上看着很确定"的假话。发版时只改 `pyproject.toml`。

    优先读 `APP_VERSION` 环境变量：排障时可以用它临时验证"线上到底是哪一版"，
    不用重新构建镜像。读不到就返回 `0.0.0-dev` —— 宁可难看，不要编一个版本号。
    """

    override = os.getenv("APP_VERSION", "").strip()
    if override:
        return override
    try:
        from importlib.metadata import version

        return version(_DIST_NAME)
    except Exception:  # noqa: BLE001 - 元数据缺失不应阻塞启动
        return "0.0.0-dev"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ---- 基础 ----
    app_name: str = "moqian-service-assistant"
    app_env: str = "dev"
    # 顶栏环境标识：只用于**显示**，不参与任何判定（判定看 app_env / demo_enabled）。
    # 本地 .env 写「本地环境」，线上 .env 写「生产环境」——同一份代码两种面貌，不靠代码分支。
    app_env_label: str = "本地环境"
    baseline: str = "v0.4"
    version: str = Field(default_factory=_detect_version)
    # 构建信息：由部署脚本注入（Dockerfile ARG → ENV），用来回答「线上跑的是哪个提交」。
    # 空值表示不知道，管理台会照实显示，而不是猜。
    build_sha: str = Field("", validation_alias=AliasChoices("BUILD_SHA", "GIT_SHA"))
    build_time: str = ""
    log_level: str = "INFO"

    # ---- LLM（OpenAI 兼容三件套；兼容旧变量名）----
    llm_base_url: str = Field(
        "https://api.deepseek.com",
        validation_alias=AliasChoices("LLM_BASE_URL", "BASE_URL"),
    )
    llm_model: str = Field(
        "deepseek-flash",
        validation_alias=AliasChoices("LLM_MODEL", "MODEL"),
    )
    llm_api_key: str = Field("", validation_alias=AliasChoices("LLM_API_KEY", "API_KEY"))
    # 推理模型的 reasoning token 也占 max_tokens，预算给足（实测 16 会让 content 为空）
    llm_max_tokens: int = 1200
    # S3 带证据回答的预算：最多 8 个步骤 + 每步引用；空内容时会自动加倍重试一次
    llm_answer_max_tokens: int = 3200
    llm_timeout_seconds: float = 60.0

    # ---- 数据库 ----
    database_url: str = "postgresql://moqian:moqian@127.0.0.1:5433/moqian"

    # ---- 检索（S3）----
    retrieval_top_k_dense: int = 20
    retrieval_top_k_bm25: int = 20
    retrieval_rerank_candidates: int = 30
    # 进证据门/提示词的条数：12 条是实测折中——模板重复切片被去重后，像"钢丝绳"
    # 这种只被单路命中的稀有词才有机会进入证据集（8 条时会被双路命中的模板切片挤掉）。
    retrieval_final_top_n: int = 12
    retrieval_min_score: float = 0.35
    retrieval_rrf_k: int = 60
    # 过滤后命中少于该数量就回退全库（纪律 2 的延伸）
    retrieval_min_filtered_hits: int = 3
    # 重排 provider：none = 只用 RRF（D15）；tencent = 腾讯云 AI Search /v1/rerank；
    # llama_cpp = 本地 llama-server /v1/rerank（预留）
    reranker_provider: str = "none"
    reranker_base_url: str = "http://127.0.0.1:9997"

    # ---- 证据门 ----
    evidence_min_hits: int = 1
    evidence_min_coverage: float = 0.5

    # ---- 身份（D9）----
    identity_recognizable_threshold: float = 0.8
    identity_auto_provision: bool = False

    # ---- embedding（D14：走本机 Ollama 的 OpenAI 兼容端点，不再下载 2GB 模型）----
    embedding_provider: str = "openai_compatible"
    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_model: str = "bge-m3:latest"
    embedding_dim: int = 1024
    embedding_api_key: str = ""
    embedding_batch_size: int = 16
    embedding_timeout_seconds: float = 120.0
    # 重排模型与凭据（S3 用；D15 评估后接腾讯云 AI Search）
    reranker_model: str = "bge-reranker-v2-m3"
    reranker_api_key: str = Field(
        "",
        validation_alias=AliasChoices("RERANKER_API_KEY", "RERANk_KEY"),
    )
    reranker_timeout_seconds: float = 10.0

    # ---- 文档入库（S2）----
    data_dir: str = "data"
    doc_ingest_chunk_max_chars: int = 700
    doc_ingest_chunk_min_chars: int = 120
    # 扫描件（几乎抽不出文字）直接判失败并提示需 OCR，不硬入空切片
    doc_ingest_reject_scanned: bool = True
    # 通用数据表（参数 / 规格 / 台账）单文件行数上限：一行一条切片，超限请拆文件
    doc_ingest_table_max_rows: int = 5000
    # 通用数据表：连续 K 行合并成一条证据（台账类低信息行用；1 = 一行一条）
    doc_ingest_table_chunk_rows: int = 1
    # 通用数据表：首列作为该条证据的章节/分组（参数表通常是「型号」列）
    doc_ingest_table_first_column_section: bool = True

    # ---- 治理域认证（D12：JWT HS256）----
    jwt_secret: str = "dev-only-change-me-please-32bytes!"
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 480
    # 转人工热线与值班时间（P0 只做热线提示；S5 起后台可改）
    handoff_hotline: str = "400-800-6699"
    handoff_hours: str = "客服 8:30–20:00；困人急修 24 小时"
    # 登录验证码与后台知识自测入口（可被 app_config 覆盖）
    captcha_enabled: bool = False
    preview_enabled: bool = True

    # ---- 初始管理员（首次启动由 app.seed_admin 建档；密码只从环境变量读）----
    admin_initial_account: str = "admin"
    admin_initial_name: str = "系统管理员"
    admin_initial_password: str = ""

    # ---- /chat 模式（D13 相关；S1 只做骨架直连，S3/S4 换 pipeline）----
    chat_mode: str = "pipeline"  # pipeline | skeleton
    llm_max_retries: int = 2

    # ---- 任务 worker ----
    task_poll_seconds: float = 2.0
    task_heartbeat_seconds: float = 15.0
    task_stale_seconds: int = 120
    task_max_attempts: int = 3

    # ---- 线上演示站（门禁与配额；默认关闭，关闭时行为与本机开发完全一致）----
    # 演示码只做分流与归因，成本上限由配额保证；不要把它当安全边界。
    demo_enabled: bool = False
    demo_require_code: bool = True
    # 公开码：官网按钮自带，不单独计入「单码日额度」
    demo_public_code: str = "web"
    demo_session_turns: int = 6
    demo_code_daily: int = 60
    demo_ip_daily: int = 20
    demo_global_daily: int = 200
    # 只有部署在反代后面才可信 X-Forwarded-For（否则可被伪造绕过单 IP 限额）
    demo_trust_forwarded_for: bool = False
    # 公开演示的预约链接：留空时前端不显示「预约」按钮；公网演示部署时再填
    demo_booking_url: str = ""

    # ---- 安全规则词表（纪律 3；属行业词汇，只能留在 app 层）----
    # 逗号分隔；留空则用 app/domain.py 的默认词表。命中即拦截，不发模型
    safety_words: str = ""

    def safety_word_list(self) -> tuple[str, ...]:
        from .domain import DEFAULT_SAFETY_WORDS

        raw = self.safety_words.strip()
        if not raw:
            return DEFAULT_SAFETY_WORDS
        return tuple(word.strip() for word in raw.split(",") if word.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
