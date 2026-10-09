from __future__ import annotations

from typing import Any


def project_state(events: list[dict[str, Any]], timeline_ms: int) -> dict[str, Any]:
    """Fold events at or before ``timeline_ms``. The same input always yields the same state."""
    state: dict[str, Any] = {
        "timeline_ms": timeline_ms,
        "lifecycle": "STARTING",
        "agent_state": "listening",
        "transcript": [],
        "tools": [],
        "rag": [],
        "marks": [],
    }
    open_tools: dict[str, dict[str, Any]] = {}
    for event in events:
        offset = int(event.get("offset_ms") or 0)
        if offset > timeline_ms:
            break
        kind = str(event.get("type") or "")
        payload = event.get("payload") or {}
        if kind == "lifecycle":
            state["lifecycle"] = payload.get("state") or state["lifecycle"]
        elif kind == "agent.state":
            state["agent_state"] = payload.get("state") or state["agent_state"]
        elif kind == "transcript.final":
            state["transcript"].append(
                {
                    "speaker": payload.get("speaker") or "customer",
                    "text": payload.get("text") or "",
                    "offset_ms": offset,
                }
            )
        elif kind == "tool.started":
            tool_id = str(payload.get("tool_call_id") or payload.get("tool") or len(open_tools))
            open_tools[tool_id] = {"id": tool_id, "tool": payload.get("tool"), "status": "started", "offset_ms": offset}
        elif kind == "tool.completed":
            tool_id = str(payload.get("tool_call_id") or payload.get("tool") or "")
            current = open_tools.get(tool_id, {"id": tool_id, "tool": payload.get("tool"), "offset_ms": offset})
            current["status"] = payload.get("status") or "completed"
            current["result"] = payload.get("result")
            open_tools[tool_id] = current
        elif kind in {"rag.started", "rag.completed"}:
            state["rag"].append({"type": kind, "offset_ms": offset, "topic": payload.get("topic") or payload.get("title")})
        elif kind in {"audio.generated", "audio.sent", "audio.played", "audio.cancelled"}:
            state["marks"].append({"type": kind, "name": payload.get("name"), "offset_ms": offset})
    state["tools"] = list(open_tools.values())
    return state
