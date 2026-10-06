"""Local, OTel-shaped trace capture for model turns."""

from .recorder import TraceRecorder
from .store import save_trace

__all__ = ["TraceRecorder", "save_trace"]
