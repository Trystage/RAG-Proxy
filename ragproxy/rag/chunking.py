"""分块策略：token（默认）/ 段落 / 标题层级。"""
from __future__ import annotations

import re
from typing import Callable

from ..utils.token_estimate import estimate_tokens  # noqa: F401


def chunk_by_token(
    text: str, chunk_size: int, overlap: int, token_fn: Callable[[str], int] | None = None
) -> list[str]:
    token_fn = token_fn or estimate_tokens
    if chunk_size <= 0:
        chunk_size = 800
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 5)
    if token_fn(text) <= chunk_size:
        return [text.strip()] if text.strip() else []

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        # 从 start 开始向后取到接近 chunk_size
        lo, hi = start, n
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if token_fn(text[start:mid]) <= chunk_size:
                lo = mid
            else:
                hi = mid - 1
        end = lo
        if end <= start:
            end = min(n, start + chunk_size * 4)  # 兜底
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_by_paragraph(text: str, chunk_size: int, overlap: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if not buf:
            buf = p
        elif estimate_tokens(buf) + estimate_tokens(p) <= chunk_size:
            buf += "\n\n" + p
        else:
            chunks.append(buf)
            buf = p
    if buf:
        chunks.append(buf)
    # 超长段再按 token 切
    final: list[str] = []
    for c in chunks:
        if estimate_tokens(c) > chunk_size:
            final.extend(chunk_by_token(c, chunk_size, overlap))
        else:
            final.append(c)
    return final


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def chunk_by_heading(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按 Markdown 标题层级分块：一级标题为分块边界，块内超长再按 token 切。"""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return chunk_by_token(text, chunk_size, overlap)
    chunks: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        piece = text[start:end].strip()
        if not piece:
            continue
        if estimate_tokens(piece) > chunk_size:
            # 二级标题继续细分
            sub = re.split(r"(?m)^(##\s+.+)$", piece)
            buf = ""
            for s in sub:
                if not s.strip():
                    continue
                if estimate_tokens(buf) + estimate_tokens(s) <= chunk_size:
                    buf = buf + "\n" + s if buf else s
                else:
                    if buf.strip():
                        chunks.append(buf.strip())
                    buf = s
            if buf.strip():
                chunks.append(buf.strip())
        else:
            chunks.append(piece)
    return chunks


def chunk_text(text: str, strategy: str, chunk_size: int, overlap: int) -> list[str]:
    strategy = (strategy or "token").lower()
    if strategy == "paragraph":
        return chunk_by_paragraph(text, chunk_size, overlap)
    if strategy == "heading":
        return chunk_by_heading(text, chunk_size, overlap)
    return chunk_by_token(text, chunk_size, overlap)
