from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .contracts import RetrievalHit, RetrievalResult


@dataclass(frozen=True)
class Citation:
    label: str
    document_id: str
    chunk_id: str
    metadata: dict


def build_knowledge_context(result: RetrievalResult) -> tuple[str, dict[str, Citation]]:
    """Assign labels only after final ranking and return an immutable provenance map."""
    source_map: dict[str, Citation] = {}
    blocks: list[str] = []
    for hit in result.hits[:3]:
        label = f"S{len(source_map) + 1}"
        metadata = dict(hit.metadata)
        document_id = str(metadata.get("document_id", ""))
        content_hash = metadata.get("content_hash")
        if content_hash and content_hash != hashlib.sha256(hit.content.encode()).hexdigest():
            continue
        if document_id and not hit.chunk_id.startswith(f"{document_id}:"):
            continue
        source_map[label] = Citation(label, document_id, hit.chunk_id, metadata)
        blocks.append(
            f"[{label}]\nsource: {metadata.get('citation_label', metadata.get('source_filename', 'unknown'))}\n"
            f"document_id: {metadata.get('document_id', '')}\nchunk_id: {hit.chunk_id}\ncontent:\n{hit.content}"
        )
    return "\n\n".join(blocks), source_map
