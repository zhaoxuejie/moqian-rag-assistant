"""evidence：回答前的证据门（拒答护栏）。S3 落地。

纪律 4：证据不足必须拒答并转人工，禁止拼凑作答。判定逻辑在 `gate`，
阈值与话术由 `app` 注入（app 层传 min_hits / min_score / min_coverage）。
"""

from .gate import GateResult, evaluate_gate

__all__ = ["GateResult", "evaluate_gate"]
