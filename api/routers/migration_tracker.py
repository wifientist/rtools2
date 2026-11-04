"""
Migration Tracker — one shared board for the last few site migrations. Alpha-gated.

Replaces the spreadsheet that gets copied and pasted back and forth: each site
is a row with its three step dates (config pushed, validation, HW migrated).

A step that didn't happen on its day is recorded as an event in the site's
`events` list, with a free-text reason:

- missed config: config moves to a new date and, by default, validation and HW
  move by the same number of workdays.
- missed validation (it failed, or simply didn't happen): validation moves to a
  new date and, by default, HW moves by the same number of workdays.
- missed migration: HW moves to a new date; nothing comes after it.

Whatever is pushed or not, HW always stays at least a workday after validation.

Who sees it: alpha users whose company domain is in TRACKER_DOMAINS, plus
super users. Who edits it: super users only. Nothing here talks to SZ or R1.

  GET    /tracker/sites                      the board
  POST   /tracker/sites                      add one site
  POST   /tracker/sites/bulk                 add many (pasted from a spreadsheet)
  PATCH  /tracker/sites/{id}                 edit any fields
  POST   /tracker/sites/{id}/miss-config     record a missed config day, reschedule
  POST   /tracker/sites/{id}/miss-validation record a missed or failed validation, reschedule
  POST   /tracker/sites/{id}/miss-migration  record a missed HW migration, reschedule
  DELETE /tracker/sites/{id}/events/{index}  remove a mis-entered event
  DELETE /tracker/sites/{id}
  GET    /tracker/export                     the whole board as JSON (to carry dev -> prod)
  POST   /tracker/import                     load an export: replace the board, or add to it
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


class MissValidation(BaseModel):
    missed_on: Optional[date] = None       # defaults to the current validation date, else today
    new_validation: Optional[date] = None  # defaults to the next workday after the miss
    push_later: bool = True                # move HW by the same number of workdays
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


class ImportSite(SiteFields):
    """One site from an export. `id` in the file is ignored; everything else is kept as-is."""
    site_name: str = Field(min_length=1)
    events: List[dict] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None


class ImportBody(BaseModel):
    format: Optional[str] = None
    sites: List[ImportSite]
    mode: str = Field(default="replace", pattern="^(replace|add)$")


EXPORT_FORMAT = "rtools-tracker"
EXPORT_VERSION = 1


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


def _normalize_event(e: dict) -> dict:
    """Older boards logged a failed validation as a re-config retry loop
    (kind "validation_failed", reconfig_on/reconfig_done). That's now just a
    missed validation, with the retry date as the new validation date."""
    if e.get("kind") == "validation_failed":
        e = {
            "date": e.get("date"),
            "kind": "validation_missed",
            "note": e.get("note"),
            "original_validation": e.get("original_validation") or e.get("date"),
            "new_validation": e.get("reconfig_on"),
            "pushed_later": False,
            "by": e.get("by"),
            "at": e.get("at"),
        }
    return {k: v for k, v in e.items() if k not in ("reconfig_on", "reconfig_done", "slip_days")}


def _events(site: TrackerSite) -> list:
    return [_normalize_event(e) for e in site.events or [] if isinstance(e, dict)]


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
        "events": _events(site),
        "updated_at": d(site.updated_at),
        "updated_by": site.updated_by,
    }


def _export_row(site: TrackerSite) -> dict:
    row = _serialize(site)
    row["created_at"] = site.created_at.isoformat() if site.created_at else None
    return row


def _apply(site: TrackerSite, fields: SiteFields) -> None:
    changes = fields.model_dump(exclude_unset=True)
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
    site.events = [*_events(site), {
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


@router.post("/sites/{site_id}/miss-validation")
def miss_validation(
    site_id: int,
    body: MissValidation,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Log a validation that didn't pass on its day (failed or just didn't happen;
    the note says which) and move it to a new date. With push_later, HW keeps
    its workday gap; without it, HW stays put unless validation would overtake it."""
    _require_edit(current_user)
    site = _get_site(site_id, db)
    missed_on = body.missed_on or site.validation or date.today()
    new_validation = body.new_validation or next_workday(missed_on)
    if new_validation <= missed_on:
        raise HTTPException(status_code=400, detail="The new validation date must be after the missed one")
    original_validation = site.validation or missed_on

    if body.push_later and site.hw_migrated:
        site.hw_migrated = add_workdays(site.hw_migrated, workdays_between(original_validation, new_validation))
    if site.hw_migrated and site.hw_migrated < next_workday(new_validation):
        site.hw_migrated = next_workday(new_validation)

    site.validation = new_validation
    site.validation_done = False
    site.hw_migrated_done = False

    site.events = [*_events(site), {
        "date": missed_on.isoformat(),
        "kind": "validation_missed",
        "note": (body.note or "").strip() or None,
        "original_validation": original_validation.isoformat(),
        "new_validation": new_validation.isoformat(),
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

    site.events = [*_events(site), {
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
    events = _events(site)
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


@router.get("/export")
def export_board(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every site with its dates, done flags, notes and event history, for /tracker/import."""
    _require_view(current_user)
    sites = db.query(TrackerSite).order_by(TrackerSite.id).all()
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "exported_at": datetime.utcnow().isoformat(timespec="seconds"),
        "exported_by": current_user.email,
        "sites": [_export_row(s) for s in sites],
    }


@router.post("/import")
def import_board(
    body: ImportBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Load an export. `replace` empties the board first; `add` appends. One transaction
    either way, so a bad file leaves the board untouched. Who last edited each site and
    when are kept from the file, so the history carries over."""
    _require_edit(current_user)
    if body.format and body.format != EXPORT_FORMAT:
        raise HTTPException(status_code=400, detail=f"Not a tracker export (format {body.format!r})")

    now = datetime.utcnow()
    new_sites = []
    for row in body.sites:
        site = TrackerSite(
            events=[_normalize_event(e) for e in row.events if isinstance(e, dict)],
            created_at=row.created_at or now,
            updated_at=row.updated_at or now,
            updated_by=row.updated_by or current_user.email,
        )
        _apply(site, SiteFields(**row.model_dump(include=set(SiteFields.model_fields), exclude_unset=True)))
        new_sites.append(site)

    removed = 0
    if body.mode == "replace":
        removed = db.query(TrackerSite).delete()
    db.add_all(new_sites)
    db.commit()
    return {"imported": len(new_sites), "removed": removed, "mode": body.mode}
