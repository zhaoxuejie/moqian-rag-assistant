"""引用校验：从模型输出里抽 `[M23002]` 这类标记，逐个对照本轮证据集。

引用 ID 规则（与入库时的 evidence_id 一致）：
    前缀（1–2 个大写字母）+ 数字，例如 M23002 / T1187 / M01001。
只认方括号包裹的形态，避免把正文里的编号误当引用。

校验是**纯函数**：不读数据库、不看模型，只做"这个 ID 是否在本轮证据集里"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CITATION_PATTERN = re.compile(r"[\[【]\s*([A-Z]{1,2}\d{3,10})\s*[\]】]")


@dataclass
class CitationCheck:
    text: str
    """剥离非法引用后的文本（合法引用原样保留）。"""
    kept: list[str] = field(default_factory=list)
    stripped: list[str] = field(default_factory=list)
    """被剥离的非法引用 ID（含重复，按出现顺序）。"""

    @property
    def has_citation(self) -> bool:
        return bool(self.kept)


def extract_citations(text: str) -> list[str]:
    """按出现顺序抽取引用 ID（去重）。"""
    return list(dict.fromkeys(CITATION_PATTERN.findall(text or "")))


def validate_citations(text: str, allowed_ids: set[str] | list[str]) -> CitationCheck:
    """剥离非法引用。`allowed` 必须传本轮证据集，不能传全库。"""
    allowed = set(allowed_ids)
    kept: list[str] = []
    stripped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        evidence_id = match.group(1)
        if evidence_id in allowed:
            if evidence_id not in kept:
                kept.append(evidence_id)
            return match.group(0)
        stripped.append(evidence_id)
        return ""

    cleaned = CITATION_PATTERN.sub(replace, text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([，。；、！？])", r"\1", cleaned)
    return CitationCheck(text=cleaned.strip(), kept=kept, stripped=stripped)


def strip_invalid_citations(text: str, allowed_ids: set[str] | list[str]) -> tuple[str, list[str]]:
    """便捷函数：返回 (清洗后文本, 被剥离的 ID)。"""
    check = validate_citations(text, allowed_ids)
    return check.text, check.stripped
