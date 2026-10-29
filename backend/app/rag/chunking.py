"""Markdown-aware chunking: respects headings and groups by paragraphs."""

from __future__ import annotations

import re

from ..types import ChunkDraft


def _split_long(paragraph: str, limit: int) -> list[str]:
    """Splits a long paragraph by sentences (or hard-cuts it) so it never exceeds the limit."""
    if len(paragraph) <= limit:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    parts: list[str] = []
    cur = ""
    for s in sentences:
        while len(s) > limit:  # giant sentence: hard cut
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(s[:limit])
            s = s[limit:]
        if cur and len(cur) + len(s) + 1 > limit:
            parts.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        parts.append(cur)
    return parts


def _tail(text: str, overlap: int) -> str:
    if overlap <= 0 or len(text) <= overlap:
        return ""
    tail = text[-overlap:]
    cut = tail.find(" ")
    return tail[cut + 1 :] if cut != -1 else tail


def split_markdown(text: str, source: str, chunk_chars: int = 900, overlap: int = 120) -> list[ChunkDraft]:
    title = source
    sections: list[tuple[str, list[str]]] = []  # (heading, lines)
    current_heading = ""
    lines: list[str] = []

    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            level, heading = len(m.group(1)), m.group(2).strip()
            if level == 1:
                title = heading
                continue
            if lines:
                sections.append((current_heading, lines))
            current_heading, lines = heading, []
        else:
            lines.append(line)
    if lines:
        sections.append((current_heading, lines))

    drafts: list[ChunkDraft] = []
    for heading, sec_lines in sections:
        body = "\n".join(sec_lines).strip()
        if not body:
            continue
        paragraphs: list[str] = []
        for p in re.split(r"\n\s*\n", body):
            paragraphs.extend(_split_long(p.strip(), chunk_chars))

        prefix = f"{title} › {heading}\n" if heading else f"{title}\n"
        buf = ""
        pieces: list[str] = []
        for p in paragraphs:
            if buf and len(buf) + len(p) + 2 > chunk_chars:
                pieces.append(buf)
                buf = (_tail(buf, overlap) + "\n\n" + p).strip()
            else:
                buf = f"{buf}\n\n{p}".strip() if buf else p
        if buf:
            pieces.append(buf)

        for piece in pieces:
            drafts.append(ChunkDraft(source=source, heading=heading or title, chunk_index=len(drafts), content=prefix + piece))
    return drafts
