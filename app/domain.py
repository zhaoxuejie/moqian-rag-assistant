"""领域词汇与提示词：**行业相关的部分集中在这里**，不进 `modules`、不进 widget 代码。

架构 §4.1 的拆分线是"机制公共、词汇行业"：
- 切片保留"章节号"是机制 → 放 `modules`；
- 词表叫什么、系统提示词怎么写、安全话术怎么说 → 放 `app`，部署时可按客户替换。

S1 只用到安全词表与骨架模式的提示词；S3/S4 的检索提示词、引用格式约束也放这里。
"""

from __future__ import annotations

import re

# 安全规则词表（PRD FR-Q4 / 纪律 3）：命中即拦截，不生成任何操作步骤
DEFAULT_SAFETY_WORDS: tuple[str, ...] = (
    "带电检修",
    "带电作业",
    "短接安全回路",
    "安全回路短接",
    "封门锁",
    "撬门",
    "盘车",
    "救援操作",
    "不停电",
)

# 骨架模式（S1）的系统提示词：明确告知"没有检索、不要给操作步骤"，
# 避免演示时把无依据输出当成正式答案。S3 起换成带证据引用的正式提示词。
SKELETON_SYSTEM_PROMPT = (
    "你是设备售后知识助手的**接通性测试**通道，当前未接入任何资料检索。"
    "请用一句到两句话回应，说明链路已通，并提示用户正式版本会给出带出处的答案。"
    "不要编造故障原因、型号参数、维修步骤或安全建议。"
)

# 骨架模式给用户的固定前缀：让"这不是正式答案"在界面上可见
SKELETON_NOTICE = "（骨架模式：未接入资料检索，以下内容仅验证链路连通，不构成答案）"

# 安全拦截卡文案（widget spec §4.5）：只说明风险类别与联系厂家，不含任何操作步骤
SAFETY_TITLE = "涉及带电作业 / 安全回路，不提供操作步骤"
SAFETY_BODY = (
    "带电检修、短接安全回路属于高风险作业，助手不生成任何具体操作指引。\n"
    "此类作业必须先断电、挂牌、验电，并由持证人员按安全规程执行；"
    "现场不具备条件时，请立即停止作业并联系厂家技术支持。"
)

# 证据 ID 前缀（行业词汇）：手册 M、工单 T、FAQ F、图纸 D、表格 X
# 前缀进数据库 `chunks.evidence_id`，随格式走，保证"看到 ID 能猜出它来自哪类资料"
EVIDENCE_ID_PREFIX: dict[str, str] = {
    "manual": "M",
    "ticket": "T",
    "faq": "F",
    "drawing": "D",
    "excel": "X",
    "table": "X",  # 通用数据表（参数 / 规格 / 台账）沿用表格前缀
}
DEFAULT_EVIDENCE_PREFIX = "E"

# FAQ 表格的「批次」列别名：属行业词汇，留在 app 层，解析时作为参数注入
# `modules/doc_ingest.excel.parse_faq_xlsx`（解析器自身不认识任何行业词）。
# 行级批次优先于文档级批次；行内留空则继承文档批次，文档也没有则为「不限批次」。
FAQ_BATCH_COLUMN_ALIASES: frozenset[str] = frozenset({"适用批次", "批次", "batch"})

# 型号是人工录入的自由文本：`BK` / `BK系列` / `BK 型号` 应视为同一型号，
# 否则精确等值会让过滤直接 0 命中、触发全库回退（实测踩过：库里写 BK、问题写 BK系列）。
# 检索侧只认这里生成的"已知等价写法集合"，仍做等值匹配，不做模糊/猜测匹配。
MODEL_SERIES_SUFFIXES: tuple[str, ...] = ("系列", "型号")


def model_series_aliases(value: str | None) -> tuple[str, ...]:
    """系列级写法：`HY-G1000/1.6-JXW` → `HY-G` / `HY-G 系列` / `HY-G系列`。

    手册/合同这类资料按系列适用（"HY-G 系列"），具体型号的检索条件也要能命中它们，
    否则按型号过滤时会把系列手册排除掉。只对 `HY-字母` 这种已知命名生效，推不出就返回空。
    """
    text = (value or "").strip()
    if not text:
        return ()
    head = text.split("/")[0].strip()
    match = re.match(r"^(HY-[A-Za-z]+)", head)
    if not match:
        return ()
    series = match.group(1)
    if not series or series == text:
        return ()
    return tuple(
        dict.fromkeys([series, f"{series} 系列", f"{series}系列", f"{series}型号"])
    )


def model_match_aliases(value: str | None) -> tuple[str, ...]:
    """型号的等价写法：原值 + 去后缀的短名 + 短名补后缀的长名 + 系列级写法。"""
    text = (value or "").strip()
    if not text:
        return ()
    base = text
    for suffix in MODEL_SERIES_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix):
            base = base[: -len(suffix)].strip()
            break
    aliases = [text, base, *(f"{base}{suffix}" for suffix in MODEL_SERIES_SUFFIXES)]
    aliases.extend(model_series_aliases(base))
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


# 兜底转人工文案（S5 由后台配置覆盖；这里给 app 层默认值）
DEFAULT_HANDOFF_TEXT = "转接售后值班工程师（客服 8:30–20:00；困人急修 400-800-6699 全年无休）"

# 单轮追问（widget spec §4.3A）：只有"批次显著影响做法"的行业问题才追问；
# 宿主已给批次、或知识库只有一个批次时不追问。词表属行业词汇，放 app 层。
#
# 只保留**确实按批次变化**的主题词：平层调整方式（电位器 → 编码器自学习）、
# 制动器间隙（B22 0.30–0.50 / B23 0.20–0.40）、门刀/编码器参数。
# 不把「HY-E」「调整」「参数」这类泛词放进来——故障码问题几乎都带 HY-E，
# 会让每一次查故障码都先弹批次追问，体验很差（故障码处置手册本身已按批次写明参数）。
BATCH_SENSITIVE_HINTS: tuple[str, ...] = (
    "平层",
    "门刀",
    "间隙",
    "编码器",
    "自学习",
)
ASK_BATCH_TITLE = "先确认一下适用批次"
ASK_BATCH_BODY = (
    "同一型号在不同批次可能对应不同的结构或调整方法，混用会把 A 批次的步骤用到 B 批次上。"
    "请确认这台设备的批次；如果不确定，系统会并列展示不同批次的方案，不替你选。"
)

# ---------- S3：带证据回答的提示词与话术（行业词汇，只留在 app 层） ----------
RAG_SYSTEM_PROMPT = (
    "你是设备售后知识助手，服务对象是一线维保工程师。必须遵守以下规则：\n"
    "1. 只能依据【证据】里的内容回答，不得使用证据之外的知识，也不得编造型号、参数、数值；\n"
    "2. 每个步骤的末尾必须用方括号标注它依据的证据 ID，例如 [M23002]；"
    "一步引多条证据就写 [M23002][M23003]；\n"
    "3. 只要证据里出现了与问题相关的参数、步骤或判据，就必须据此回答，不能因为"
    "“证据没有明确写某个型号”就拒答；\n"
    "4. 不同资料给出的数值或做法不一致时，**并列列出并分别标注出处**，"
    "说明各自适用条件；不要替工程师选一个，也不要因此拒答；\n"
    "5. 只有证据确实与问题无关、完全没有可用内容时，才回复一句："
    "现有资料不足以回答这个问题。不要猜测；\n"
    "6. 涉及带电作业、短接安全回路、救援、撬门、盘车等高风险操作时，"
    "不给出任何操作步骤，只提示联系厂家；\n"
    "7. 输出格式：每步一行，以「1. 」开头连续编号，最多 8 步；"
    "不要输出标题、总结或“以下是步骤”之类的引导语。"
)

GAP_TITLE = "未查到可靠依据，本次不作答"
GAP_BODY = (
    "现有手册、FAQ 与历史工单中没有与该问题直接相关的资料。"
    "为避免给出无法核验的操作建议，本次不生成回答。\n"
    "本次查询已记入后台「知识缺口清单」，售后主管会据此补充资料。"
)

CONFLICT_MESSAGE = (
    "检索到多个批次/版本的资料，方法或参数可能不同。系统不替你选择，"
    "请先确认本机出厂批次，再按对应条目执行；现场无法确认时请联系厂家。"
)

