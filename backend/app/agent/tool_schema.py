from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CanonicalTool:
    name: str
    description: str
    parameters: dict[str, Any]
