"""后台「知识自测」抽测接口（PRD FR-P3 / admin spec §4.13）。

设计口径：
- 只做检索 + 证据门 + 结构化，不生成答案、不写 messages、不建 session；
  `channel=admin_preview` 的隔离在这里体现为"根本不进问答日志与指标"。
- 结果用于治理动作后的即时核对：命中了哪些证据、得分多少、门为什么没过；
  与 Widget 的现场问答共用同一套检索/证据门代码（ADR9）。
"""

from __future__ import annotations

from typing import Any

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from modules.evidence import evaluate_gate
from modules.retrieval import RetrievalPipeline

from ..config import Settings
from ..domain import GAP_BODY, SAFETY_BODY, model_match_aliases
from ..rag import build_retrieval_config
from ..rag_runner import build_structured, coverage_tokens, match_safety_word
from ..runtime_config import load_config
from ..schemas import HostContext
from .deps import get_conn, get_embedder, get_reranker, get_settings, require_governance_admin

router = APIRouter(
    prefix="/admin",
    tags=["admin-preview"],
    dependencies=[Depends(require_governance_admin)],
)


class PreviewAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=200)
    model: str | None = None
    batch: str | None = None


def _chunk_doc_ids(conn: psycopg.Connection, evidence_ids: list[str]) -> dict[str, int]:
    if not evidence_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "select evidence_id, document_id from chunks where evidence_id = any(%s)",
            (evidence_ids,),
        )
        return {str(row[0]): int(row[1]) for row in cur.fetchall()}


def _hits(conn: psycopg.Connection, items: list[Any], limit: int = 4) -> list[dict[str, Any]]:
    selected = items[:limit]
    doc_ids = _chunk_doc_ids(conn, [item.evidence_id for item in selected])
    out: list[dict[str, Any]] = []
    for item in selected:
        citation = item.to_contract()
        out.append(
            {
                "evidenceId": item.evidence_id,
                "docId": doc_ids.get(item.evidence_id),
                "loc": citation.get("loc"),
                "title": citation.get("title"),
                "score": item.dense_score if item.dense_score is not None else item.rrf_score,
                "text": item.content[:220],
                "batch": item.batch,
                "version": item.version,
                "model": item.model,
                "sources": item.sources,
            }
        )
    return out


@router.post("/preview/ask")
def admin_preview_ask(
    payload: PreviewAskRequest,
    conn: psycopg.Connection = Depends(get_conn),
    settings: Settings = Depends(get_settings),
    embedder=Depends(get_embedder),
    reranker=Depends(get_reranker),
) -> dict[str, Any]:
    """管理员抽测：返回结构化条件、证据门与命中证据（不生成答案）。"""
    context = None
    if payload.model or payload.batch:
        context = HostContext(device_model=payload.model or None, batch=payload.batch or None)
    structured = build_structured(payload.question, context=context)
    runtime = load_config(conn, ("retrieval", "safety"))
    retrieval_overrides = runtime.get("retrieval", {})
    safety_words = tuple(
        runtime.get("safety", {}).get("words") or settings.safety_word_list()
    )

    safety_word = match_safety_word(payload.question, safety_words)
    if safety_word:
        return {
            "kind": "safe",
            "structured": structured,
            "safetyWord": safety_word,
            "title": "命中安全红线，不提供操作步骤",
            "body": SAFETY_BODY,
            "hits": [],
        }

    pipeline = RetrievalPipeline(
        settings=build_retrieval_config(settings, retrieval_overrides),
        embedder=embedder,
        reranker=reranker,
    )
    retrieval = pipeline.retrieve(
        conn,
        question=payload.question,
        model=structured.get("model"),
        batch=structured.get("batch"),
        model_aliases=model_match_aliases(structured.get("model")),
    )
    try:
        min_score = float(
            retrieval_overrides.get("evidenceThreshold", settings.retrieval_min_score)
        )
        min_coverage = float(
            retrieval_overrides.get(
                "evidenceMinCoverage", settings.evidence_min_coverage
            )
        )
    except (TypeError, ValueError):
        min_score = settings.retrieval_min_score
        min_coverage = settings.evidence_min_coverage
    gate = evaluate_gate(
        retrieval.items,
        query_tokens=coverage_tokens(retrieval.query_tokens),
        min_hits=settings.evidence_min_hits,
        min_score=min_score,
        min_coverage=min_coverage,
    )
    base: dict[str, Any] = {
        "structured": structured,
        "filterNote": retrieval.filter_note,
        "gate": {
            "passed": gate.passed,
            "hits": gate.hits,
            "topScore": gate.top_score,
            "coverage": gate.coverage,
            "missingTerms": gate.missing_terms,
        },
        "hits": _hits(conn, retrieval.items),
    }
    if not gate.passed:
        return {
            "kind": "gap",
            "title": "未查到可靠依据",
            "body": GAP_BODY,
            **base,
        }
    return {"kind": "answer", **base}
