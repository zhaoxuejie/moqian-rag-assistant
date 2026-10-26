"""citation：引用的提取与**程序级**校验（纪律 1）。S3 落地。

为什么不能靠提示词：模型会写出"看起来像引用"的 ID（甚至编造 M99999）。
这里用代码从回答文本里抽出所有引用，逐个比对"本轮命中证据集"：
- 合法引用保留；
- 非法引用**整条剥离**（连同标记一起从文本里删掉）；
- 剥离后若某步骤不再有任何引用，该步骤降级为"过渡说明"或整体拒答，由 app 裁决。
"""

from .validator import (
    CitationCheck,
    extract_citations,
    strip_invalid_citations,
    validate_citations,
)

__all__ = [
    "CitationCheck",
    "extract_citations",
    "strip_invalid_citations",
    "validate_citations",
]
