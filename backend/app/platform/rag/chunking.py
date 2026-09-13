from __future__ import annotations

import re


def chunk_text(text: str, *, target_size: int = 900, overlap: int = 140, hard_max: int = 1800) -> list[tuple[str, int, int]]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    chunks: list[tuple[str, int, int]] = []
    cursor = 0
    current = ""
    start = 0
    for block in blocks:
        candidate = f"{current}\n\n{block}".strip() if current else block
        if current and len(candidate) > target_size:
            chunks.append((current, start, start + len(current)))
            tail = current[-overlap:]
            start = cursor - len(tail)
            current = f"{tail}\n\n{block}".strip()
        else:
            current = candidate
        cursor += len(block) + 2
        while len(current) > hard_max:
            piece = current[:hard_max]
            chunks.append((piece, start, start + len(piece)))
            current = current[hard_max - overlap :]
            start += hard_max - overlap
    if current:
        chunks.append((current, start, start + len(current)))
    return chunks
