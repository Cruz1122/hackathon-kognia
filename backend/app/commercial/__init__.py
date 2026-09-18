"""Small business-domain mutations used by API integrations and recovery hooks."""

from .service import mark_won, start_recovery

__all__ = ["mark_won", "start_recovery"]
