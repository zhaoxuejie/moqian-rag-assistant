"""评测体系离线单测：评测集加载、指标计算、失败清单（不调模型、不连库）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.metrics import CaseResult, failures, summarize
from evals.schema import EvalCaseError, load_cases

_CASES = Path(__file__).resolve().parent.parent / "evals" / "cases.yaml"


def make_result(**kwargs) -> CaseResult:
    defaults = dict(
        id="ans-x",
        question="q",
        expect_refuse=False,
        expect_safe=False,
        refused=False,
        gate_reason=None,
        citations=1,
        steps=2,
        steps_with_citations=2,
        stripped=[],
        latency_ms=1000,
        keywords=["间隙"],
        keyword_hits=["间隙"],
    )
    defaults.update(kwargs)
    return CaseResult(**defaults)


def test_cases_file_loads_and_covers_all_buckets() -> None:
    cases = load_cases(_CASES)
    assert len(cases) >= 20
    assert {case.id for case in cases} == {case.id for case in cases}  # 无重复（loader 已保证）
    assert any(case.expect_refuse for case in cases)
    assert any(case.expect_safe for case in cases)
    assert all(case.question for case in cases)


def test_loader_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "- id: dup\n  question: a\n- id: dup\n  question: b\n", encoding="utf-8"
    )
    with pytest.raises(EvalCaseError):
        load_cases(path)


def test_loader_rejects_conflicting_expectations(tmp_path) -> None:
    path = tmp_path / "bad2.yaml"
    path.write_text(
        "- id: x\n  question: a\n  expect: {refuse: true, safe: true}\n", encoding="utf-8"
    )
    with pytest.raises(EvalCaseError):
        load_cases(path)


def test_summary_metrics() -> None:
    results = [
        make_result(id="ans-1"),
        make_result(
            id="ans-2",
            refused=True,
            gate_reason="gap",
            steps=0,
            steps_with_citations=0,
            keyword_hits=[],
        ),
        make_result(
            id="gap-1",
            expect_refuse=True,
            refused=True,
            gate_reason="gap",
            steps=0,
            steps_with_citations=0,
            keywords=[],
            keyword_hits=[],
        ),
        make_result(
            id="gap-2",
            expect_refuse=True,
            refused=False,
            steps=2,
            steps_with_citations=2,
            keywords=[],
            keyword_hits=[],
        ),
        make_result(
            id="safe-1",
            expect_safe=True,
            refused=True,
            gate_reason="safe",
            steps=0,
            steps_with_citations=0,
            keywords=[],
            keyword_hits=[],
        ),
    ]
    summary = summarize(results)
    assert summary["total"] == 5
    assert summary["answerable"] == 2
    assert summary["refusal_cases"] == 2
    assert summary["safety_cases"] == 1
    assert summary["retrieval_hit_rate"] == 0.5  # 2 条里 1 条命中
    assert summary["answer_rate"] == 0.5
    assert summary["citation_coverage"] == 1.0  # 已作答的步骤都带引用
    assert summary["refusal_true_positive"] == 1
    assert summary["refusal_false_answer"] == 1
    assert summary["refusal_false_refuse"] == 1
    assert summary["safety_intercept_rate"] == 1.0
    assert summary["latency_p50_ms"] == 1000


def test_disclosed_gap_is_not_a_failure() -> None:
    """应拒答题若明说"资料未覆盖"，算合格（不编造），不计入失败清单。"""
    results = [
        make_result(id="gap-disclosed", expect_refuse=True, refused=False, disclosed=True),
        make_result(id="gap-fabricated", expect_refuse=True, refused=False),
    ]
    summary = summarize(results)
    assert summary["refusal_true_positive"] == 0
    assert summary["refusal_disclosed"] == 1
    assert summary["refusal_false_answer"] == 1
    failed = {item["id"]: item for item in failures(results)}
    assert "gap-disclosed" not in failed
    assert "gap-fabricated" in failed


def test_failures_lists_problems() -> None:
    results = [
        make_result(id="gap-bad", expect_refuse=True, refused=False),
        make_result(id="safe-bad", expect_safe=True, refused=True, gate_reason="gap"),
        make_result(id="ans-bad", keyword_hits=[]),
    ]
    failed = {item["id"]: item for item in failures(results)}
    assert any("应拒答却编造作答" in item for item in failed["gap-bad"]["problems"])
    assert "未被安全拦截" in failed["safe-bad"]["problems"]
    assert "检索未命中期望关键词" in failed["ans-bad"]["problems"]
