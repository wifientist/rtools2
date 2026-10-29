"""
Migration Tracker — one shared board for the last few site migrations. Alpha-gated.

Replaces the spreadsheet that gets copied and pasted back and forth: each site
is a row with its three step dates (config pushed, validation, HW migrated).

A failed validation starts a retry loop, recorded as an event on the site:
re-config on the next workday, re-validation the same day, and HW migrated
kept at least one workday after that. A site rarely loops more than once or
twice, so the loops live in the site's `events` list, not a table of their own.

A missed config day is the other event kind: config moves to a new date and,
by default, validation and HW move by the same number of workdays. A missed
migration is the third: HW moves to a new date; nothing comes after it.

Who sees it: alpha users whose company domain is in TRACKER_DOMAINS, plus
super users. Who edits it: super users only. Nothing here talks to SZ or R1.

  GET    /tracker/sites                      the board
  POST   /tracker/sites                      add one site
  POST   /tracker/sites/bulk                 add many (pasted from a spreadsheet)
  PATCH  /tracker/sites/{id}                 edit any fields
  POST   /tracker/sites/{id}/fail-validation record a failed validation, schedule the retry
  POST   /tracker/sites/{id}/miss-config     record a missed config day, reschedule
  POST   /tracker/sites/{id}/miss-migration  record a missed HW migration, reschedule
  PATCH  /tracker/sites/{id}/events/{index}  move a re-config date or tick it done
  DELETE /tracker/sites/{id}/events/{index}  remove a mis-entered event
  DELETE /tracker/sites/{id}
"""

from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from constants.access import has_tracker_access
from dependencies import get_current_user, get_db
from models.migration_tracker import TrackerSite
from models.user import User

router = APIRouter(prefix="/tracker", tags=["Migration Tracker"])


# ---------- Pydantic schemas ----------

class SiteFields(BaseModel):
    site_name: Optional[str] = None
    vsz: Optional[str] = None
    tenant: Optional[str] = None
    sz_ap_count: Optional[int] = None
    sz_wlan_count: Optional[int] = None
    category: Optional[str] = None
    config_pushed: Optional[date] = None
    config_pushed_done: Optional[bool] = None
    validation: Optional[date] = None
    validation_done: Optional[bool] = None
    hw_migrated: Optional[date] = None
    hw_migrated_done: Optional[bool] = None
    notes: Optional[str] = None


class SiteCreate(SiteFields):
    site_name: str = Field(min_length=1)


class BulkCreate(BaseModel):
    sites: List[SiteCreate]


class FailValidation(BaseModel):
    failed_on: Optional[date] = None  # defaults to the current validation date, else today
    note: Optional[str] = None


class MissConfig(BaseModel):
    missed_on: Optional[date] = None   # defaults to the current config date, else today
    new_config: Optional[date] = None  # defaults to the next workday after the miss
    push_later: bool = True            # move validation + HW by the same number of workdays
    note: Optional[str] = None


class MissMigration(BaseModel):
    missed_on: Optional[date] = None  # defaults to the current HW date, else today
    new_hw: Optional[date] = None     # defaults to the next workday after the miss
    note: Optional[str] = None


class EventUpdate(BaseModel):
    reconfig_on: Optional[date] = None
    reconfig_done: Optional[bool] = None
    note: Optional[str] = None


# ---------- Helpers ----------

def _require_view(user: User) -> None:
    if not user.alpha_enabled or not has_tracker_access(user):
        raise HTTPException(status_code=403, detail="Tracker access required")


def _require_edit(user: User) -> None:
    _require_view(user)
    if user.role.value != "super":
        raise HTTPException(status_code=403, detail="Only admins can edit the tracker")


def next_workday(d: date) -> date:
    """The next Monday–Friday after d. Holidays aren't modelled."""
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def add_workdays(d: date, n: int) -> date:
    for _ in range(n):
        d = next_workday(d)
    return d


def workdays_between(a: date, b: date) -> int:
    """Workdays in (a, b] — how far b is past a, counting only Monday–Friday."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def _get_site(site_id: int, db: Session) -> TrackerSite:
    site = db.query(TrackerSite).filter(TrackerSite.id == site_id).first()
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


def _serialize(site: TrackerSite) -> dict:
    def d(v):
        return v.isoformat() if v else None
    return {
        "id": site.id,
        "site_name": site.site_name,
        "vsz": site.vsz,
        "tenant": site.tenant,
        "sz_ap_count": site.sz_ap_count,
        "sz_wlan_count": site.sz_wlan_count,
        "category": site.category,
        "config_pushed": d(site.config_pushed),
        "config_pushed_done": site.config_pushed_done,
        "validation": d(site.validation),
        "validation_done": site.validation_done,
        "hw_migrated": d(site.hw_migrated),
        "hw_migrated_done": site.hw_migrated_done,
        "notes": site.notes,
        "events": site.events or [],
        "updated_at": d(site.updated_at),
        "updated_by": site.updated_by,
    }


def _apply(site: TrackerSite, fields: SiteFields) -> None:
    changes = fields.model_dump(exclude_unset=True)
    # A passed validation means the re-config before it happened too.
    if changes.get("validation_done") and any(not e.get("reconfig_done", True) for e in site.events or []):
        site.events = [{**e, "reconfig_done": True} for e in site.events]
    for key, value in changes.items():
        if key == "site_name" and not (value or "").strip():
            raise HTTPException(status_code=400, detail="Site name cannot be empty")
        if key.endswith("_done") and value is None:
            value = False
        setattr(site, key, value.strip() if isinstance(value, str) else value)


def _new_site(fields: SiteCreate, user: User) -> TrackerSite:
    site = TrackerSite(events=[], updated_by=user.email)
    _apply(site, fields)
    return site


# ---------- Endpoints ----------

@router.get("/sites")
def list_sites(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_view(current_user)
    sites = db.query(TrackerSite).order_by(TrackerSite.id).all()
    return {
        "sites": [_serialize(s) for s in sites],
        "can_edit": current_user.role.value == "super",
    }


@router.post("/sites")
def create_site(
    body: SiteCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_edit(current_user)
    site = _new_site(body, current_user)
    db.add(site)
    db.commit()
    db.refresh(site)
    return _serialize(site)


@router.post("/sites/bulk")
def create_sites_bulk(
    body: BulkCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_edit(current_user)
    sites = [_new_site(s, current_user) for s in body.sites]
    db.add_all(sites)
    db.commit()
    for s in sites:
        db.refresh(s)
    return {"sites": [_serialize(s) for s in sites]}


@router.patch("/sites/{site_id}")
def update_site(
    site_id: int,
    body: SiteFields,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_edit(current_user)
    site = _get_site(site_id, db)
    _apply(site, body)
    site.updated_by = current_user.email
    db.commit()
    db.refresh(site)
    return _serialize(site)


@router.post("/sites/{site_id}/fail-validation")
def fail_validation(
    site_id: int,
    body: FailValidation,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log a failed validation and schedule the retry: re-config and re-validate
    on the next workday, HW migrated no sooner than the workday after that."""
    _require_edit(current_user)
    site = _get_site(site_id, db)
    failed_on = body.failed_on or site.validation or date.today()
    retry_on = next_workday(failed_on)
    original_validation = site.validation

    site.validation = retry_on
    site.validation_done = False
    if site.hw_migrated:
        site.hw_migrated = max(site.hw_migrated, next_workday(retry_on))
        site.hw_migrated_done = False

    # Reassign rather than mutate: a plain JSON column doesn't track in-place edits.
    site.events = [*(site.events or []), {
        "date": failed_on.isoformat(),
        "kind": "validation_failed",
        "note": (body.note or "").strip() or None,
        "original_validation": original_validation.isoformat() if original_validation else None,
        "reconfig_on": retry_on.isoformat(),
        "reconfig_done": False,
        "by": current_user.email,
        "at": datetime.utcnow().isoformat(timespec="seconds"),
    }]
    site.updated_by = current_user.email
    db.commit()
    db.refresh(site)
    return _serialize(site)


@router.post("/sites/{site_id}/miss-config")
def miss_config(
    site_id: int,
    body: MissConfig,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log a missed config day and move config to a new date. With push_later,
    validation and HW keep their workday gaps; without it they stay put unless
    the new config would overtake them."""
    _require_edit(current_user)
    site = _get_site(site_id, db)
    missed_on = body.missed_on or site.config_pushed or date.today()
    new_config = body.new_config or next_workday(missed_on)
    if new_config <= missed_on:
        raise HTTPException(status_code=400, detail="The new config date must be after the missed one")
    original_config = site.config_pushed or missed_on

    if body.push_later:
        shift = workdays_between(original_config, new_config)
        if site.validation:
            site.validation = add_workdays(site.validation, shift)
        if site.hw_migrated:
            site.hw_migrated = add_workdays(site.hw_migrated, shift)
    # Either way, keep the phases in order: validate no earlier than config, swap after validating.
    if site.validation and site.validation < new_config:
        site.validation = new_config
    if site.hw_migrated and site.hw_migrated < next_workday(site.validation or new_config):
        site.hw_migrated = next_workday(site.validation or new_config)

    site.config_pushed = new_config
    site.config_pushed_done = False
    site.validation_done = False
    site.hw_migrated_done = False

    # Reassign rather than mutate: a plain JSON column doesn't track in-place edits.
    site.events = [*(site.events or []), {
        "date": missed_on.isoformat(),
        "kind": "config_missed",
        "note": (body.note or "").strip() or None,
        "original_config": original_config.isoformat(),
        "new_config": new_config.isoformat(),
        "pushed_later": body.push_later,
        "by": current_user.email,
        "at": datetime.utcnow().isoformat(timespec="seconds"),
    }]
    site.updated_by = current_user.email
    db.commit()
    db.refresh(site)
    return _serialize(site)


@router.post("/sites/{site_id}/miss-migration")
def miss_migration(
    site_id: int,
    body: MissMigration,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log a missed HW migration and move it to a new date. Config and
    validation stay as they are."""
    _require_edit(current_user)
    site = _get_site(site_id, db)
    missed_on = body.missed_on or site.hw_migrated or date.today()
    new_hw = body.new_hw or next_workday(missed_on)
    if new_hw <= missed_on:
        raise HTTPException(status_code=400, detail="The new migration date must be after the missed one")

    original_hw = site.hw_migrated or missed_on
    site.hw_migrated = new_hw
    site.hw_migrated_done = False

    site.events = [*(site.events or []), {
        "date": missed_on.isoformat(),
        "kind": "migration_missed",
        "note": (body.note or "").strip() or None,
        "original_hw": original_hw.isoformat(),
        "new_hw": new_hw.isoformat(),
        "by": current_user.email,
        "at": datetime.utcnow().isoformat(timespec="seconds"),
    }]
    site.updated_by = current_user.email
    db.commit()
    db.refresh(site)
    return _serialize(site)


@router.patch("/sites/{site_id}/events/{index}")
def update_event(
    site_id: int,
    index: int,
    body: EventUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit one retry loop. Moving the re-config date doesn't move validation — do that by hand."""
    _require_edit(current_user)
    site = _get_site(site_id, db)
    events = list(site.events or [])
    if not 0 <= index < len(events):
        raise HTTPException(status_code=404, detail="Event not found")
    event = dict(events[index])
    changes = body.model_dump(exclude_unset=True)
    if "reconfig_on" in changes:
        event["reconfig_on"] = changes["reconfig_on"].isoformat() if changes["reconfig_on"] else None
    if "reconfig_done" in changes:
        event["reconfig_done"] = bool(changes["reconfig_done"])
    if "note" in changes:
        event["note"] = (changes["note"] or "").strip() or None
    events[index] = event
    site.events = events
    site.updated_by = current_user.email
    db.commit()
    db.refresh(site)
    return _serialize(site)


@router.delete("/sites/{site_id}/events/{index}")
def delete_event(
    site_id: int,
    index: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove one event. Dates it slipped are left alone — fix those by hand."""
    _require_edit(current_user)
    site = _get_site(site_id, db)
    events = list(site.events or [])
    if not 0 <= index < len(events):
        raise HTTPException(status_code=404, detail="Event not found")
    del events[index]
    site.events = events
    site.updated_by = current_user.email
    db.commit()
    db.refresh(site)
    return _serialize(site)


@router.delete("/sites/{site_id}")
def delete_site(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_edit(current_user)
    site = _get_site(site_id, db)
    db.delete(site)
    db.commit()
    return {"deleted": site_id}
