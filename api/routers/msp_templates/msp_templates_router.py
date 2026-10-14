"""
Template Tool API -- RUCKUS ONE MSP configuration templates.

READ-ONLY for now: list, dependency tree, full body, per-EC drift, and a
dry-run preview of copying a venue bundle with a new country. Nothing here
writes to R1 yet. See services/msp_templates.py for what the live API does
that the spec does not say.

Templates are an MSP construct, so every endpoint requires an MSP controller
and runs as the MSP tenant (no x-rks-tenantid override).
"""

import asyncio
import logging
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller, validate_controller_access
from decorators import require_alpha
from dependencies import get_current_user, get_db
from models.user import User
from services import msp_template_copy, msp_templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/msp-templates", tags=["MSP Templates"])

Context = Literal["MSP", "REC"]


def _msp_client(controller_id: int, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(400, "Templates are a RUCKUS ONE feature; this controller is "
                                 f"{controller.controller_type}.")
    if controller.controller_subtype != "MSP":
        raise HTTPException(400, "Templates live on the MSP tenant -- select an MSP controller.")
    return create_r1_client_from_controller(controller.id, db)


@router.get("/{controller_id}/templates")
@require_alpha()
async def list_templates(controller_id: int, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    """MSP and REC templates in one list, with applied-EC ids resolved to names."""
    r1 = _msp_client(controller_id, current_user, db)
    msp, rec, names = await asyncio.gather(
        asyncio.to_thread(msp_templates.list_templates, r1, "MSP"),
        asyncio.to_thread(msp_templates.list_templates, r1, "REC"),
        asyncio.to_thread(msp_templates.ec_names, r1),
    )
    return {
        "templates": msp["templates"] + rec["templates"],
        "ecNames": names,
        "errors": [e for e in (msp["error"], rec["error"]) if e],
        "readableTypes": sorted(msp_templates.BODY_PATHS),
    }


@router.get("/{controller_id}/templates/{template_id}/children")
@require_alpha()
async def template_children(controller_id: int, template_id: str,
                            context: Context = Query("MSP"),
                            db: Session = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    r1 = _msp_client(controller_id, current_user, db)
    return await asyncio.to_thread(msp_templates.children, r1, template_id, context)


@router.get("/{controller_id}/templates/{template_id}/body")
@require_alpha()
async def template_body(controller_id: int, template_id: str,
                        type: str = Query(..., description="Template type, e.g. NETWORK"),
                        context: Context = Query("MSP"),
                        db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    r1 = _msp_client(controller_id, current_user, db)
    return await asyncio.to_thread(msp_templates.body, r1, template_id, type, context)


@router.get("/{controller_id}/templates/{template_id}/drift/{tenant_id}")
@require_alpha()
async def template_drift(controller_id: int, template_id: str, tenant_id: str,
                         context: Context = Query("MSP"),
                         db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    r1 = _msp_client(controller_id, current_user, db)
    return await asyncio.to_thread(msp_templates.drift, r1, template_id, tenant_id, context)


@router.get("/{controller_id}/templates/{template_id}/bundle")
@require_alpha()
async def template_bundle(controller_id: int, template_id: str,
                          context: Context = Query("MSP"),
                          db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    """A venue template and every template under it, with full bodies."""
    r1 = _msp_client(controller_id, current_user, db)
    return await asyncio.to_thread(msp_template_copy.bundle, r1, template_id, context)


@router.get("/{controller_id}/venue-countries")
@require_alpha()
async def venue_countries(controller_id: int, context: Context = Query("MSP"),
                          db: Session = Depends(get_db),
                          current_user: User = Depends(get_current_user)):
    """Venue templates and their countries -- the countries a channel preview can borrow from."""
    r1 = _msp_client(controller_id, current_user, db)
    return {"venues": await asyncio.to_thread(msp_template_copy.venue_templates, r1, context)}


class CopyPlan(BaseModel):
    name: Optional[str] = None
    address: Dict[str, object] = Field(default_factory=dict)
    excludeSettings: List[str] = Field(default_factory=list)
    modes: Dict[str, Literal["reuse", "copy", "skip"]] = Field(default_factory=dict)
    renames: Dict[str, str] = Field(default_factory=dict)


@router.post("/{controller_id}/templates/{template_id}/copy-preview")
@require_alpha()
async def copy_preview(controller_id: int, template_id: str, plan: CopyPlan,
                       context: Context = Query("MSP"),
                       db: Session = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    """Dry run: the steps a copy would take. Writes nothing."""
    r1 = _msp_client(controller_id, current_user, db)
    return await asyncio.to_thread(msp_template_copy.preview, r1, template_id, plan.model_dump(), context)
