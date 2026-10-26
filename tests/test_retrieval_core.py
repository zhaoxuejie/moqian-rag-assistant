"""检索核心件单测（离线）：BM25、RRF、证据门、引用校验。

这些都是纯函数/纯内存实现，不依赖数据库与外部服务，适合钉住"算法行为"本身：
- BM25 命中相关文档、无关文档得 0；
- RRF 只看名次、两路都中的排前面；
- 证据门三条指标任一条不满足都必须拒答；
- 引用校验：非法 ID 整条剥离、合法 ID 保留、顺序去重。
"""

from __future__ import annotations

from modules.citation import extract_citations, validate_citations
from modules.evidence import evaluate_gate
from modules.retrieval import Bm25Index, rrf_fuse
from modules.retrieval.bm25 import tokenize_query
from modules.retrieval.fusion import dedupe_by_content
from modules.retrieval.types import ScoredChunk


def make_chunk(cid: int, text: str, **kwargs) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=cid,
        evidence_id=kwargs.pop("evidence_id", f"M{cid:05d}"),
        content=text,
        **kwargs,
    )


# ---------- BM25 ----------
def test_bm25_ranks_relevant_docs() -> None:
    docs = [
        (make_chunk(1, "制动器间隙标准值为 0.3 至 0.5 毫米"), "制动器 间隙 标准 值".split()),
        (make_chunk(2, "电梯机房应保持通风干燥"), "电梯 机房 通风 干燥".split()),
        (make_chunk(3, "制动器电磁铁每 80 万次维护一次"), "制动器 电磁铁 维护 周期".split()),
    ]
    index = Bm25Index(docs)
    results = index.search(["制动器", "间隙"], top_k=2)
    assert results, "应命中相关文档"
    assert "间隙" in results[0].content
    assert results[0].bm25_score > 0


def test_bm25_returns_empty_when_no_term_matches() -> None:
    index = Bm25Index([(make_chunk(1, "电梯机房通风"), "电梯 机房 通风".split())])
    assert index.search(["量子计算"], top_k=3) == []


# ---------- RRF ----------
def test_rrf_fusion_prefers_both_routes() -> None:
    dense = [
        make_chunk(1, "a", dense_score=0.9),
        make_chunk(2, "b", dense_score=0.8),
        make_chunk(3, "c", dense_score=0.7),
    ]
    bm25 = [
        make_chunk(2, "b", bm25_score=5.0),
        make_chunk(4, "d", bm25_score=4.0),
    ]
    fused = rrf_fuse(dense, bm25, limit=4)
    assert fused[0].chunk_id == 2, "两路都命中的应排第一"
    assert fused[0].sources == ["both"]
    scores = [item.rrf_score for item in fused]
    assert scores == sorted(scores, reverse=True)
    assert len({item.chunk_id for item in fused}) == 4


def test_tokenize_query_drops_stopwords_and_punctuation() -> None:
    tokens = tokenize_query("钢丝绳需要检查哪些项目？")
    assert "钢丝绳" in tokens
    assert "检查" in tokens
    for noisy in ("需要", "哪些", "？"):
        assert noisy not in tokens


def test_dedupe_keeps_content_diversity() -> None:
    """多份手册共用模板会产出完全相同的切片；去重后不能只剩模板片段。"""
    template = "制动器电磁铁的维护保养间隔为每工作80万次或12个月"
    items = [make_chunk(index, template) for index in range(1, 9)]
    items.append(make_chunk(99, "钢丝绳是由数根钢丝捻绕而成，应定期检查断丝与磨损"))
    deduped = dedupe_by_content(items, limit=3)
    assert len(deduped) == 2
    assert sum(1 for item in deduped if item.content == template) == 1
    assert any("钢丝绳" in item.content for item in deduped)


# ---------- 证据门 ----------
def test_gate_passes_with_good_coverage() -> None:
    items = [make_chunk(1, "制动器间隙标准值 0.3 至 0.5 毫米", dense_score=0.82)]
    gate = evaluate_gate(
        items,
        query_tokens=["制动器", "间隙", "标准值"],
        min_hits=1,
        min_score=0.35,
        min_coverage=0.5,
    )
    assert gate.passed is True
    assert gate.reason is None


def test_gate_rejects_no_hits() -> None:
    gate = evaluate_gate([], query_tokens=["x"], min_hits=1, min_score=0.3, min_coverage=0.5)
    assert gate.passed is False
    assert gate.reason == "gap"


def test_gate_rejects_low_score() -> None:
    items = [make_chunk(1, "制动器间隙", dense_score=0.12)]
    gate = evaluate_gate(
        items, query_tokens=["制动器"], min_hits=1, min_score=0.35, min_coverage=0.5
    )
    assert gate.passed is False and gate.top_score == 0.12


def test_gate_rejects_low_coverage() -> None:
    items = [make_chunk(1, "电梯机房通风", dense_score=0.9)]
    gate = evaluate_gate(
        items,
        query_tokens=["制动器", "间隙", "调整"],
        min_hits=1,
        min_score=0.35,
        min_coverage=0.5,
    )
    assert gate.passed is False
    assert gate.coverage == 0.0
    assert set(gate.missing_terms) == {"制动器", "间隙", "调整"}


# ---------- 引用校验 ----------
def test_extract_citations_orders_and_dedupes() -> None:
    text = "先查 [M23002]，再看【T1187】，最后再看 [M23002]。"
    assert extract_citations(text) == ["M23002", "T1187"]


def test_validate_strips_invalid_citation() -> None:
    check = validate_citations("步骤一 [M23002]，步骤二 [M99999]。", {"M23002"})
    assert check.kept == ["M23002"]
    assert check.stripped == ["M99999"]
    assert "M99999" not in check.text
    assert "M23002" in check.text


def test_validate_keeps_plain_text_intact() -> None:
    check = validate_citations("没有引用的说明。", {"M23002"})
    assert check.text == "没有引用的说明。"
    assert check.kept == [] and check.stripped == []
    assert check.has_citation is False


def test_validate_cleans_punctuation_after_strip() -> None:
    check = validate_citations("检查间隙 [M99999]。", {"M23002"})
    assert check.text == "检查间隙。"


# ---------- 型号等价写法（过滤前置工具） ----------
def test_model_match_aliases_covers_common_writings() -> None:
    from app.domain import model_match_aliases

    assert set(model_match_aliases("BK系列")) == {"BK系列", "BK", "BK型号"}
    assert set(model_match_aliases("BK")) == {"BK", "BK系列", "BK型号"}
    assert model_match_aliases(None) == ()
    assert model_match_aliases("   ") == ()
    assert "BK系列" in model_match_aliases(" BK系列 ")


def test_model_match_aliases_include_series_level_writings() -> None:
    """具体型号要能命中「按系列适用」的手册/合同（HY-G1000 → HY-G 系列）。"""
    from app.domain import model_match_aliases

    aliases = model_match_aliases("HY-G1000/1.6-JXW")
    assert "HY-G1000/1.6-JXW" in aliases
    assert {"HY-G", "HY-G 系列", "HY-G系列"} <= set(aliases)

    assert "HY-H 系列" in model_match_aliases("HY-H2000/0.5-JXW")
    assert "HY-F 系列" in model_match_aliases("HY-F30-1000")
    assert "HY-W 系列" in model_match_aliases("HY-W300/0.4-JXW")
    # 系列名本身就是输入时不重复扩展
    assert model_match_aliases("HY-G 系列") == ("HY-G 系列", "HY-G", "HY-G系列", "HY-G型号")


def test_to_contract_prefers_explicit_loc() -> None:
    """有出处原文（工作表行号）时引用卡直接用它，不再拼成 §章节。"""
    table_chunk = make_chunk(1, "参数表正文", loc="参数表 第2行", batch="B22")
    assert table_chunk.to_contract()["loc"] == "参数表 第2行"
    pdf_chunk = make_chunk(2, "手册正文", section_no="3.1", page_from=5, page_to=5)
    assert pdf_chunk.to_contract()["loc"] == "§3.1 · P5"
