from __future__ import annotations

from dataclasses import dataclass

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
    for index, hit in enumerate(result.hits[:3], 1):
        label = f"S{index}"
        metadata = dict(hit.metadata)
        source_map[label] = Citation(label, str(metadata.get("document_id", "")), hit.chunk_id, metadata)
        blocks.append(
            f"[{label}]\nsource: {metadata.get('citation_label', metadata.get('source_filename', 'unknown'))}\n"
            f"document_id: {metadata.get('document_id', '')}\nchunk_id: {hit.chunk_id}\ncontent:\n{hit.content}"
        )
    return "\n\n".join(blocks), source_map
