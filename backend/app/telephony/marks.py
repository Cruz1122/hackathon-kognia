from __future__ import annotations


class MarkTracker:
    def __init__(self) -> None:
        self._states: dict[str, str] = {}
        self._sequence = 0

    def generated(self) -> str:
        self._sequence += 1
        name = f"mark-{self._sequence}"
        self._states[name] = "generated"
        return name

    def sent(self, name: str) -> None:
        if self._states.get(name) == "generated":
            self._states[name] = "sent"

    def played(self, name: str) -> str | None:
        state = self._states.get(name)
        if state is None or state == "cancelled":
            return state
        self._states[name] = "played"
        return "played"

    def clear_unplayed(self) -> list[str]:
        cancelled: list[str] = []
        for name, state in list(self._states.items()):
            if state in {"generated", "sent"}:
                self._states[name] = "cancelled"
                cancelled.append(name)
        return cancelled

    def state(self, name: str) -> str | None:
        return self._states.get(name)

    def snapshot(self) -> dict[str, str]:
        return dict(self._states)
