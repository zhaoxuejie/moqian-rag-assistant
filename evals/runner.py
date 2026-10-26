"""评测 runner：`python -m evals.runner --cases evals/cases.yaml`

复用线上同一套管线（`app.rag_runner.run_turn`，ADR9），不另写检索/证据门/引用校验。
输出：`evals/reports/<时间戳>.json` 与同名 `.md`（人可读的指标表 + 失败清单）。
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import fields
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.db import connect
from app.embedding import get_embedding_client
from app.rag_runner import run_turn
from app.reranker import build_reranker
from app.schemas import HostContext
from evals.metrics import CaseResult, failures, summarize
from evals.schema import Case, load_cases
from modules.llm import LlmClient, config_from_settings

DEFAULT_CASES = Path(__file__).resolve().parent / "cases.yaml"
DEFAULT_OUT = Path(__file__).resolve().parent / "reports"

# "明示缺口"的措辞特征：应拒答题若这样回答，不算编造（口径见 metrics.summarize）。
# 这是启发式判定，报告里仍建议人工复核；不要把 100% 当成人工核验过。
_DISCLOSURE_MARKERS = (
    "未给出",
    "未另行给出",
    "没有给出",
    "未覆盖",
    "未收录",
    "未查到",
    "不能凭",
    "无法确认",
    "无法直接给出",
    "未明确",
    "未说明",
    "没有出现",
    "不足以",
    "不能据此推定",
    "需向",
    "请向",
    "以设备随机技术文件",
)


def _discloses_gap(content: str | None) -> bool:
    text = content or ""
    return any(marker in text for marker in _DISCLOSURE_MARKERS)


def evaluate_case(
    conn, settings, llm, embedder, case: Case, reranker=None
) -> CaseResult:
    context = HostContext.model_validate(case.context) if case.context else None
    outcome = None
    for attempt in (1, 2):
        outcome = run_turn(
            settings=settings,
            conn=conn,
            llm=llm,
            embedder=embedder,
            reranker=reranker,
            question=case.question,
            context=context,
        )
        if outcome.error is None or attempt == 2:
            break
        # 网络抖动（SSL 断开等）在评测里重试一次，避免把基础设施波动算成产品的错
        time.sleep(3)
    retrieval = outcome.retrieval
    evidence_text = "\n".join(item.content for item in retrieval.items) if retrieval else ""
    keyword_hits = [word for word in case.keywords if word in evidence_text]
    steps = outcome.steps
    gate = outcome.gate
    disclosed = (not outcome.refused) and _discloses_gap(outcome.content)
    return CaseResult(
        id=case.id,
        question=case.question,
        expect_refuse=case.expect_refuse,
        expect_safe=case.expect_safe,
        refused=outcome.refused,
        gate_reason=outcome.gate_reason,
        citations=len(outcome.citations),
        steps=len(steps),
        steps_with_citations=sum(1 for step in steps if step.citations),
        stripped=outcome.stripped,
        latency_ms=outcome.elapsed_ms,
        keywords=case.keywords,
        keyword_hits=keyword_hits,
        evidence=[item.evidence_id for item in (retrieval.items if retrieval else [])],
        gate_hits=gate.hits if gate else None,
        gate_top=round(gate.top_score, 4) if gate and gate.top_score is not None else None,
        gate_coverage=round(gate.coverage, 4) if gate else None,
        error=repr(outcome.error) if outcome.error else None,
        disclosed=disclosed,
    )


def _write_markdown(
    path: Path, summary: dict, results: list[CaseResult], failed: list[dict]
) -> None:
    lines = [
        "# 评测报告",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "- 管线：`app.rag_runner.run_turn`（与 `/chat` 同一套代码）",
        "",
        "## 指标",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    labels = {
        "total": "案例数",
        "answerable": "可回答题",
        "refusal_cases": "应拒答题",
        "safety_cases": "安全题",
        "retrieval_hit_rate": "检索命中率（关键词出现在 topN 证据）",
        "answer_rate": "可回答题作答率",
        "citation_coverage": "引用覆盖率（带引用步骤 / 全部步骤）",
        "refusal_accuracy": "拒答判断准确率",
        "refusal_true_positive": "应拒答且拒答",
        "refusal_disclosed": "应拒答但明示缺口（部分回答）",
        "refusal_false_answer": "应拒答却编造作答",
        "refusal_false_refuse": "应作答却拒答",
        "safety_intercept_rate": "安全拦截率",
        "stripped_citations": "被剥离的非法引用数",
        "latency_p50_ms": "时延 p50（ms）",
        "latency_p90_ms": "时延 p90（ms）",
        "errors": "执行错误数",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | {summary.get(key)} |")
    lines += ["", "## 失败清单", ""]
    if failed:
        lines.append("| 案例 | 问题 | 实际 | 原因 |")
        lines.append("|---|---|---|---|")
        for item in failed:
            lines.append(
                f"| {item['id']} | {item['question']} | {item['gateReason'] or 'answered'} | "
                f"{'；'.join(item['problems'])} |"
            )
    else:
        lines.append("（无）")
    lines += [
        "",
        "## 逐题明细",
        "",
        "| 案例 | 期望 | 实际 | 步骤/带引用 | 命中关键词 | 时延ms |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        expect = "安全" if result.expect_safe else ("拒答" if result.expect_refuse else "作答")
        actual = result.gate_reason or "answered"
        lines.append(
            f"| {result.id} | {expect} | {actual} | {result.steps}/{result.steps_with_citations} | "
            f"{','.join(result.keyword_hits) or '—'} | {result.latency_ms} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑评测集（复用线上管线）")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    parser.add_argument("--only", default="", help="只跑指定 id（逗号分隔，调试用）")
    parser.add_argument(
        "--recompute",
        default="",
        help="从已有报告 JSON 重算指标与 markdown（不调模型；改口径时用）",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.llm_api_key and not args.recompute:
        raise SystemExit("缺少 LLM_API_KEY：评测要真调模型，请在 .env 配好再跑")

    if args.recompute:
        raw = json.loads(Path(args.recompute).read_text(encoding="utf-8"))
        results = [_result_from_json(item) for item in raw["results"]]
        return _write_reports(results, args.out_dir)

    cases = load_cases(args.cases)
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        cases = [case for case in cases if case.id in wanted]
    if args.limit:
        cases = cases[: args.limit]

    conn = connect()
    embedder = get_embedding_client(settings)
    llm = LlmClient(config_from_settings(settings))
    reranker = build_reranker(settings)
    results: list[CaseResult] = []
    try:
        for index, case in enumerate(cases, start=1):
            started = time.perf_counter()
            print(f"[{index}/{len(cases)}] {case.id} …", flush=True)
            try:
                result = evaluate_case(conn, settings, llm, embedder, case, reranker)
            except Exception as exc:  # noqa: BLE001 - 单题失败要记录，不中断全量
                result = CaseResult(
                    id=case.id,
                    question=case.question,
                    expect_refuse=case.expect_refuse,
                    expect_safe=case.expect_safe,
                    refused=False,
                    gate_reason=None,
                    citations=0,
                    steps=0,
                    steps_with_citations=0,
                    stripped=[],
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    keywords=case.keywords,
                    keyword_hits=[],
                    error=repr(exc),
                )
            results.append(result)
            print(
                f"    → refused={result.refused} gate={result.gate_reason} "
                f"steps={result.steps} cites={result.citations} hits={result.keyword_hits}",
                flush=True,
            )
    finally:
        conn.close()

    return _write_reports(results, args.out_dir)


def _result_from_json(item: dict) -> CaseResult:
    """把历史报告里的逐题结果还原成对象（改口径后重算用，不重跑模型）。"""
    known = {field.name for field in fields(CaseResult)}
    data = {key: value for key, value in item.items() if key in known}
    data.setdefault("id", "")
    data.setdefault("question", "")
    data.setdefault("expect_refuse", False)
    data.setdefault("expect_safe", False)
    data.setdefault("refused", False)
    data.setdefault("gate_reason", None)
    data.setdefault("citations", 0)
    data.setdefault("steps", 0)
    data.setdefault("steps_with_citations", 0)
    data.setdefault("stripped", [])
    data.setdefault("latency_ms", 0)
    data.setdefault("keywords", [])
    data.setdefault("keyword_hits", [])
    return CaseResult(**data)


def _write_reports(results: list[CaseResult], out_dir_raw: str) -> int:
    summary = summarize(results)
    failed = failures(results)
    out_dir = Path(out_dir_raw)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"eval-{stamp}.json"
    md_path = out_dir / f"eval-{stamp}.md"
    json_path.write_text(
        json.dumps(
            {
                "createdAt": datetime.now().isoformat(timespec="seconds"),
                "summary": summary,
                "failures": failed,
                "results": [result.__dict__ for result in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_markdown(md_path, summary, results, failed)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告：{json_path}\n      {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
