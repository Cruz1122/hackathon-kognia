from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.dependencies import get_current_user
from ..db.models import User
from ..db.session import get_db
from .schemas import DashboardResponse
from .service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])
analytics_service = AnalyticsService()


def _parse_bound(value: str, *, end: bool) -> datetime:
    try:
        if len(value) == 10:
            parsed_date = date.fromisoformat(value)
            return datetime.combine(parsed_date + (timedelta(days=1) if end else timedelta()), time.min, tzinfo=UTC)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid analytics date.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _organization_id(user: User):
    if user.organization_id is None:
        raise HTTPException(status_code=403, detail="An organization is required for this operation.")
    return user.organization_id


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> DashboardResponse:
    today = datetime.now(UTC).date()
    from_value = from_ or (today - timedelta(days=30)).isoformat()
    to_value = to or today.isoformat()
    date_from = _parse_bound(from_value, end=False)
    date_to = _parse_bound(to_value, end=True)
    if date_to <= date_from:
        raise HTTPException(status_code=422, detail="Analytics range must be ordered.")
    try:
        return await analytics_service.dashboard(
            session,
            organization_id=_organization_id(user),
            date_from=date_from,
            date_to=date_to,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Analytics service unavailable.") from exc


__all__ = ["router"]
