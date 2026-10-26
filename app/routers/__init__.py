"""路由包：按契约的 tag 分组（ops / widget / chat / admin-*）。

刻意不做成一个大 `main.py`：路由分组就是**链路边界**，也是权限边界。
"""

from __future__ import annotations
