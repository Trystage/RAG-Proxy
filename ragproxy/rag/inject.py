"""Prompt 注入：将检索结果按配置模板与模式融入请求。"""
from __future__ import annotations

from typing import Any

from ..utils.token_estimate import truncate_by_tokens


def build_context_text(
    chunks: list[dict[str, Any]],
    template: str,
    query: str,
    max_context_tokens: int,
) -> tuple[str, str]:
    """构造注入文本。

    返回 (注入到 system 的文本, 原始上下文正文)。
    检索内容显式标记为"参考资料"，与指令区分（Prompt 注入防护）。
    """
    if not chunks:
        return "", ""
    parts = []
    for i, c in enumerate(chunks, 1):
        src = c.get("file_name") or c.get("metadata", {}).get("source") or "未知来源"
        text = (c.get("text") or "").strip()
        parts.append(f"\n{text}")
    context = "\n\n".join(parts)
    injected = template.replace("{context}", context).replace("{query}", query)
    injected = truncate_by_tokens(injected, max_context_tokens) if max_context_tokens else injected
    return injected, context


def apply_inject_mode(
    messages: list[dict[str, Any]],
    injected: str,
    mode: str,
) -> list[dict[str, Any]]:
    """按注入模式把检索内容写入消息列表。

    - system_prepend：插入到第一条 system 之前（无 system 则新建一条置顶）
    - system_append：追加到现有 system（无 system 则新建一条置顶）
    - new_message：新增一条 role=system 的 message（放在最后）
    """
    if not injected:
        return messages
    mode = (mode or "system_prepend").lower()
    out = [dict(m) for m in messages]

    if mode == "new_message":
        out.append({"role": "system", "content": injected})
        return out

    sys_idx = next((i for i, m in enumerate(out) if m.get("role") == "system"), None)
    if sys_idx is None:
        if mode == "system_append":
            out.insert(0, {"role": "system", "content": injected})
        else:  # system_prepend
            out.insert(0, {"role": "system", "content": injected})
        return out

    if mode == "system_append":
        old = out[sys_idx].get("content", "")
        if isinstance(old, list):
            old = "".join(
                p.get("text", "") for p in old if isinstance(p, dict)
            )
        out[sys_idx] = {"role": "system", "content": str(old) + "\n\n" + injected}
    else:  # system_prepend
        old = out[sys_idx].get("content", "")
        if isinstance(old, list):
            old = "".join(p.get("text", "") for p in old if isinstance(p, dict))
        out.insert(sys_idx, {"role": "system", "content": injected})
        # 原有 system 保留在注入之后
    return out
