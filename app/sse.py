"""SSE 帧构造。

契约要求：每帧 `event: <name>` + `data: <JSON>`，事件后立即 flush。
这里只负责"格式正确"，不负责"发什么事件"（那是 chat 服务的事）。
"""

from __future__ import annotations

import json


def sse_event(name: str, payload: dict) -> str:
    """构造一个 SSE 帧。中文不转义，方便抓包与日志直接读。"""
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {name}\ndata: {data}\n\n"
