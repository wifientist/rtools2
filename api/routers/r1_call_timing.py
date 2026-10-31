from fastapi import APIRouter, Depends, Query

from decorators import require_role
from dependencies import get_current_user
from models.user import RoleEnum, User
from r1api import call_metrics

router = APIRouter(prefix="/r1-call-timing", tags=["R1 Call Timing"])


@router.get("/summary")
@require_role(RoleEnum.super)
def call_timing_summary(
    hours: int = Query(24, ge=1, le=720),
    current_user: User = Depends(get_current_user),
):
    """Timing totals for R1 calls over the last N hours, from Redis only."""
    return {
        "hours": hours,
        "rows": call_metrics.read_summary(hours),
    }


@router.get("/hourly")
@require_role(RoleEnum.super)
def call_timing_hourly(
    kind: str,
    label: str,
    status: str,
    hours: int = Query(168, ge=1, le=720),
    current_user: User = Depends(get_current_user),
):
    """One row per UTC hour, to compare the same call at different times of day."""
    return {
        "hours": hours,
        "rows": call_metrics.read_hourly(kind, label, status, hours),
    }
