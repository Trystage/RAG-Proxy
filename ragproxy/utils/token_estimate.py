"""轻量 token 估算器（不依赖 tiktoken / 本地模型）。

近似规则：
- 中文字符 / 日文假名等 CJK 字符：约 1 字符 ≈ 1 token
- 其他字符（英文、数字、符号）：约 4 字符 ≈ 1 token
- 空白连续折叠后按比例计入
"""
from __future__ import annotations

import re

_CJK_RE = re.compile(
    r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    other = len(text) - cjk
    return max(1, cjk + int(other / 4) + 1)


def truncate_by_tokens(text: str, max_tokens: int) -> str:
    """按估算 token 数截断文本（保持近似边界，不切碎 CJK 字符）。"""
    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return text
    # 二分查找最长前缀
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    cut = text[:lo]
    # 尽量在换行/句号处切断
    for sep in ("\n\n", "\n", "。", ". "):
        idx = cut.rfind(sep)
        if idx > int(len(cut) * 0.6):
            cut = cut[: idx + len(sep)]
            break
    return cut.rstrip() + "\n…[截断]"
