"""测试公共设施。

两类用例：
- **离线用例**（LLM/embedding 客户端、解析切片、安全件、契约）用假 transport 或
  现场生成样本，CI 无外网也能跑；
- **需要数据库的用例**（任务队列、会话与 SSE、入库落库、后台接口）在没有可用 PG 时
  **跳过**，本地起 `docker compose up -d db` 即会参与执行；CI 用 services 起同一个镜像。
"""

from __future__ import annotations

import os

import pytest

from app.config import get_settings
from app.db import connect


def _db_available() -> bool:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("select 1")
            return cur.fetchone() == (1,)
    except Exception:  # noqa: BLE001 - 探测失败即视为不可用
        return False


_DB_AVAILABLE = _db_available()
requires_db = pytest.mark.skipif(
    not _DB_AVAILABLE, reason="需要可用的 PostgreSQL（docker compose up -d db）"
)


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture()
def conn():
    """每个用例一条独立连接；用完关闭。"""
    if not _DB_AVAILABLE:
        pytest.skip("需要可用的 PostgreSQL")
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def _isolate_env():
    """`CHAT_MODE` 等配置在用例里可临时改环境变量，用完还原。"""
    snapshot = dict(os.environ)
    # 本地 .env 为演示预演打开 DEMO_ENABLED 时，测试必须不受影响（演示门禁默认关闭）；
    # 需要打开门禁的用例显式传 Settings(demo_enabled=True)，优先级高于环境变量。
    os.environ["DEMO_ENABLED"] = "false"
    yield
    os.environ.clear()
    os.environ.update(snapshot)
    get_settings.cache_clear()


# 需要库的用例会写 sessions / messages / gaps 等运行期数据；跑完要清掉新建行，
# 否则本地开发库的「问答日志 / 知识缺口 / 驾驶舱」会被测试数据弄脏（CI 用一次性库）。
_CLEANUP_TABLES = (
    # (表名, 外键依赖顺序：先删子表)
    "settlements",
    "gaps",
    "sessions",
    "jobs",
    "documents",
    "admin_audit_log",
)


def _max_id(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"select coalesce(max(id), 0) from {table}")
        return int(cur.fetchone()[0])


def _business_baseline() -> tuple[dict[str, int], set[str]]:
    conn = connect()
    try:
        baseline = {table: _max_id(conn, table) for table in _CLEANUP_TABLES}
        with conn.cursor() as cur:
            cur.execute("select account from identity_pending")
            accounts = {row[0] for row in cur.fetchall()}
        # 必须提交再关闭：`select max(id)` 会拿 ACCESS SHARE 锁，留着事务不提交会挡住
        # 用例里的 `truncate jobs`（队列用例），整个测试进程就挂在这里。
        conn.commit()
        return baseline, accounts
    finally:
        conn.close()


def _cleanup_business_rows(baseline: dict[str, int], baseline_accounts: set[str]) -> None:
    conn = connect()
    try:
        with conn.cursor() as cur:
            for table in _CLEANUP_TABLES:
                cur.execute(f"delete from {table} where id > %s", (baseline[table],))
            cur.execute(
                "delete from identity_pending where account <> all(%s)",
                (list(baseline_accounts) or [""],),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _isolate_business_rows():
    """记录测试开始前的行边界，结束后删除本次用例新建的业务行（含级联）。"""
    if not _DB_AVAILABLE:
        yield
        return
    baseline, baseline_accounts = _business_baseline()
    yield
    _cleanup_business_rows(baseline, baseline_accounts)


# ---------- 文档入库夹具 ----------
@pytest.fixture()
def sample_pdf(tmp_path):
    """生成一份带书签目录的 3 页中文 PDF（用 PyMuPDF 内置 CJK 字体）。

    结构：第 1 页封面；第 2 页「6.4.2 主机轴承异响排查」；第 3 页「6.4.3 制动器间隙调整」。
    TOC 指向第 2/3 页，用来验证"页码出处"取自目录而不是猜。
    """
    import pymupdf

    def add_text(page, y: float, text: str, size: int = 11) -> None:
        page.insert_text((72, y), text, fontname="china-s", fontsize=size)

    path = tmp_path / "BK-series-brake-manual.pdf"
    document = pymupdf.open()

    first = document.new_page()
    add_text(first, 90, "BK 系列制动器维护保养手册 Ver.3", size=16)
    add_text(first, 130, "本手册适用于 BK 系列制动器的日常维护与故障排查。")

    second = document.new_page()
    add_text(second, 90, "6.4.2 主机轴承异响排查", size=14)
    add_text(second, 130, "将电梯置于检修模式，确认首层围挡与警示牌到位。")
    add_text(second, 160, "以听音杆在主机承重梁、导向轮、钢丝绳三处分别听诊。")

    third = document.new_page()
    add_text(third, 90, "6.4.3 制动器间隙调整", size=14)
    add_text(third, 130, "测量制动衬与制动轮的间隙，标准值为 0.3 至 0.5 毫米。")

    document.set_toc(
        [
            [1, "第6章 主机与轴承", 2],
            [2, "6.4.2 主机轴承异响排查", 2],
            [2, "6.4.3 制动器间隙调整", 3],
        ]
    )
    document.save(path)
    document.close()
    return path


class FakeEmbedder:
    """假 embedding 客户端：不联网，返回固定维度的确定性向量。

    向量内容不重要（S2 只验证"能落库、能取回"），但维度必须等于 1024，
    否则会触发 `vector(1024)` 的类型错误——这正是要防的配置事故。
    """

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.last = None
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        from modules.llm import EmbeddingResult

        self.calls.append(list(texts))
        vectors = [[((index + 1) % 7) / 10.0] * self.dim for index, _ in enumerate(texts)]
        self.last = EmbeddingResult(
            vectors=vectors,
            model="fake-embedder",
            dim=self.dim,
            elapsed_ms=1,
            prompt_tokens=len(texts),
        )
        return vectors

    def embed_one(self, text: str) -> list[float]:
        """检索协议用：返回与 `embed` 同源的确定性向量。"""
        return self.embed([text])[0]


@pytest.fixture()
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
