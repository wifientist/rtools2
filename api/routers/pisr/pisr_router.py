"""
PISR — Property Install Status Report. Alpha-gated.

A read-only poll of one venue: what was installed, what is online, how it is
addressed, what VLANs and PoE it uses, which SSIDs are activated, and which of
those are demonstrably carrying traffic.

READ-ONLY. Every endpoint issues GETs and `*/query` POSTs against RUCKUS ONE and
nothing else. PISR creates nothing, changes nothing, activates nothing, and
stores nothing — no snapshot files, no database rows. A report exists for the
length of one HTTP response.

HUMAN-TRIGGERED ONLY. Every endpoint runs once per request. PISR registers no
scheduled job, starts no background task, and has no recurring-poll entry point
for a scheduler to call. The page refreshes when someone clicks refresh.

  GET /pisr/{cid}/scope    what tenant this controller acts on
  GET /pisr/{cid}/venues   venues for the picker, with the counts R1 aggregates
  GET /pisr/{cid}/report   one venue's full report
  GET /pisr/{cid}/checks   the check catalogue — what a report verifies
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller, validate_controller_access
from decorators import require_alpha
from dependencies import get_current_user, get_db
from models.controller import Controller
from models.user import User
from functools import lru_cache
from pathlib import Path

from fastapi import Response
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML as WeasyHTML

from reports.pisr import build_context as build_pdf_context
from services.pisr import checks as check_registry
from services.pisr.collect import build_report, list_venues

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pisr", tags=["PISR"])


@lru_cache(maxsize=1)
def _jinja() -> Environment:
    """Report templates live alongside the other PDF reports in api/templates."""
    return Environment(loader=FileSystemLoader(
        str(Path(__file__).resolve().parent.parent.parent / "templates")))


def _export_name(venue_name: str, extension: str) -> str:
    """A filename that survives a download folder: no spaces, no punctuation."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in str(venue_name))
    safe = "-".join(part for part in safe.split("-") if part)[:60] or "venue"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"site-review-{safe}-{stamp}.{extension}"


def _controller(controller_id: int, user: User, db: Session) -> Controller:
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(400, f"PISR needs a RuckusONE controller; "
                                 f"'{controller.name}' is {controller.controller_type}")
    return controller


def _resolve_tenant(controller: Controller, tenant_id: Optional[str]) -> Optional[str]:
    """
    An MSP controller must be told which MSP-EC to report on — its own tenant id
    addresses the MSP account, which owns no venues. An EC controller addresses
    itself and takes no override.
    """
    if controller.controller_subtype == "MSP":
        if not tenant_id:
            raise HTTPException(400, "This is an MSP controller — select an MSP-EC first.")
        return tenant_id
    return None


@router.get("/{controller_id}/scope")
@require_alpha()
async def get_scope(controller_id: int,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Tells the UI whether it has to ask for an MSP-EC before anything else."""
    controller = _controller(controller_id, current_user, db)
    return {
        "controllerId": controller.id,
        "controllerName": controller.name,
        "subtype": controller.controller_subtype,
        "needsEcSelection": controller.controller_subtype == "MSP",
        "tenantId": controller.r1_tenant_id,
        "region": controller.r1_region or "NA",
    }


@router.get("/{controller_id}/venues")
@require_alpha()
async def get_venues(controller_id: int,
                     tenant_id: Optional[str] = Query(None),
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    """Every venue on the EC, for the venue picker. One query, no per-venue fan-out."""
    controller = _controller(controller_id, current_user, db)
    override = _resolve_tenant(controller, tenant_id)
    r1 = create_r1_client_from_controller(controller.id, db)
    venues = await list_venues(r1, override)
    return {"tenantId": override or controller.r1_tenant_id, "venues": venues}


@router.get("/{controller_id}/report")
@require_alpha()
async def get_report(controller_id: int,
                     venue_id: str = Query(..., description="Venue to report on"),
                     tenant_id: Optional[str] = Query(None),
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    """
    One venue, polled once, right now. Roughly a dozen concurrent reads; expect
    a few seconds on a large venue, most of it the switch-port query.
    """
    controller = _controller(controller_id, current_user, db)
    override = _resolve_tenant(controller, tenant_id)
    r1 = create_r1_client_from_controller(controller.id, db)
    logger.info("pisr: user=%s controller=%s tenant=%s venue=%s",
                current_user.email, controller.id, override, venue_id)
    return await build_report(r1, override, venue_id)


@router.get("/{controller_id}/report.pdf")
@require_alpha()
async def get_report_pdf(controller_id: int,
                         venue_id: str = Query(..., description="Venue to report on"),
                         tenant_id: Optional[str] = Query(None),
                         label: Optional[str] = Query(
                             None, description="Human name for the tenant, used in the "
                                               "report header. A tenant id is a hex string "
                                               "and reads badly on a shared document."),
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """
    The whole review as a PDF.

    PISR stores nothing, so this re-polls the venue rather than rendering a
    saved run — the PDF is its own snapshot and may differ by a few clients
    from a page left open for a while. It is built from exactly the same
    report the UI renders, so the two cannot disagree about what was found.

    Narrative pages are portrait; the device inventory is a landscape named
    page with each table split in two, because sixteen columns do not fit a
    portrait page at a readable size.
    """
    controller = _controller(controller_id, current_user, db)
    override = _resolve_tenant(controller, tenant_id)
    r1 = create_r1_client_from_controller(controller.id, db)

    report = await build_report(r1, override, venue_id)
    context = build_pdf_context(report, controller.name, label or tenant_id)

    template = _jinja().get_template("reports/pisr.html")
    pdf = WeasyHTML(string=template.render(**context)).write_pdf()

    venue_name = (report.get("venue") or {}).get("name") or venue_id
    filename = _export_name(venue_name, "pdf")
    logger.info("pisr: PDF for venue=%s (%d findings, %d bytes) by user=%s",
                venue_id, context["findings_total"], len(pdf), current_user.email)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/{controller_id}/checks")
@require_alpha()
async def get_checks(controller_id: int,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    """The check catalogue — what a report verifies, without running one."""
    _controller(controller_id, current_user, db)
    return {
        "checks": [{"id": fn.__name__.replace("check_", "").replace("_", "-"),
                    "description": (fn.__doc__ or "").strip()}
                   for fn in check_registry.CHECKS],
        "thresholds": {
            "apGroupSsidLimit": check_registry.AP_GROUP_SSID_LIMIT,
            "poeWarnPct": check_registry.POE_WARN_PCT,
            "poeCriticalPct": check_registry.POE_CRIT_PCT,
            "dhcpWarnPct": check_registry.DHCP_WARN_PCT,
        },
    }
