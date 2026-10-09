from __future__ import annotations

import re


def take_semantic_chunk(buffer: str, flush: bool = False) -> tuple[str, str]:
    """Cut a finished Spanish sentence. Short fragments stay in the buffer."""
    if flush:
        return buffer.strip(), ""
    sentence = re.search(r"[.!?](?:[\"'»”)]*)?(?=\s|$)", buffer)
    if sentence and len(buffer[: sentence.end()].split()) >= 2:
        return buffer[: sentence.end()].strip(), buffer[sentence.end() :].lstrip()
    return "", buffer
