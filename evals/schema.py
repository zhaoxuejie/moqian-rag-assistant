"""评测集 schema 与加载（S6）。

评测集是"问题 + 期望行为"的清单，字段刻意保持最小：
- `expect.refuse`：本题按产品纪律应当**拒答**（证据不足）；
- `expect.safe`：本题命中**安全话题**，必须只出安全卡、不生成步骤；
- `keywords`：可从证据正文里核验的关键词（用于判断"检索是否命中"，不评判措辞）。

注意：`keywords` 只用于**检索命中**判定，不是"答案必须包含这些词"——
回答可以换种说法，但必须引用到包含这些事实的切片。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class EvalCaseError(ValueError):
    """评测集格式错误：宁可加载失败，也不要"跳过几条"悄悄算分。"""


@dataclass
class Case:
    id: str
    question: str
    context: dict[str, Any] = field(default_factory=dict)
    expect_refuse: bool = False
    expect_safe: bool = False
    keywords: list[str] = field(default_factory=list)
    note: str | None = None


def load_cases(path: str | Path) -> list[Case]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise EvalCaseError("评测集根节点必须是列表")
    cases: list[Case] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise EvalCaseError(f"第 {index} 条不是对象")
        case_id = str(item.get("id") or "").strip()
        question = str(item.get("question") or "").strip()
        if not case_id or not question:
            raise EvalCaseError(f"第 {index} 条缺少 id 或 question")
        if case_id in seen:
            raise EvalCaseError(f"重复的案例 id：{case_id}")
        seen.add(case_id)
        expect = item.get("expect") or {}
        expect_refuse = bool(expect.get("refuse", False))
        expect_safe = bool(expect.get("safe", False))
        if expect_refuse and expect_safe:
            raise EvalCaseError(f"{case_id} 不能同时期望拒答与安全拦截")
        keywords = [str(word) for word in (expect.get("keywords") or []) if str(word).strip()]
        cases.append(
            Case(
                id=case_id,
                question=question,
                context=dict(item.get("context") or {}),
                expect_refuse=expect_refuse,
                expect_safe=expect_safe,
                keywords=keywords,
                note=item.get("note"),
            )
        )
    return cases
