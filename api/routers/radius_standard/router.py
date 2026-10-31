"""
RADIUS Standard Router

Read every tenant's RADIUS attribute groups through an MSP and save one as the
customer's template (rarely), then compare a tenant against it and push through
that tenant's own controller (routinely):

- POST /radius-standards/tenants    -> the MSP's customer list
- POST /radius-standards/attributes -> every attribute R1 knows
- POST /radius-standards/read       -> one tenant's groups. Read-only.
- POST /radius-standards/analyze    -> consistency ranking, and each tenant against
                                       the template. Works on what the page read;
                                       makes no R1 calls.
- POST /radius-standards/apply      -> create/replace groups in one tenant
- GET/POST/PUT/DELETE /radius-standards/profiles -> templates saved per customer

The page reads one tenant per call, so a slow or unreadable tenant shows up as
that tenant's row rather than failing the whole MSP.

The template travels with the request even though it is saved: what the page
shows is exactly what apply writes.

Apply only takes a direct tenant controller, never an MSP key. An MSP key may
well be read-only, and even a writable one would make "which tenant did I just
change" a matter of which row was clicked. Groups in a tenant that aren't in the
template are shown and never touched.
"""

import asyncio
import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import validate_controller_access
from decorators import require_beta
from dependencies import get_current_user, get_db
from models.radius_standard import RadiusStandard
from models.user import User
from routers.tenant_scope import resolve_tenant_id
from services.radius_standard import (
    analyze_consistency, compare_tenant, read_attribute_catalogue, read_tenant_groups,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/radius-standards", tags=["RADIUS Standard"])


# ==================== Models ====================

class Attribute(BaseModel):
    attributeName: str = Field(..., min_length=1, max_length=255)
    operator: Literal["ADD", "ADD_REPLACE", "DOES_NOT_EXIST"] = "ADD"
    attributeValue: str = Field(..., min_length=1, max_length=4096)
    dataType: str = Field(..., min_length=1)

    @field_validator("attributeValue")
    @classmethod
    def trimmed(cls, value: str) -> str:
        return value.strip()


class Group(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    attributes: List[Attribute] = Field(..., min_length=1)


def _unique_names(groups: List[Group]) -> List[Group]:
    names = [g.name for g in groups]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"Duplicate group name: {', '.join(dupes)}")
    return groups


class ProfileBody(BaseModel):
    name: str = Field(..., min_length=1)
    groups: List[Group] = Field(default_factory=list)
    source: Optional[str] = None
    source_tenant_id: Optional[str] = None

    _unique = field_validator("groups")(_unique_names)


class TenantsRequest(BaseModel):
    r1_controller_id: int


class ScopedRequest(BaseModel):
    r1_controller_id: int
    tenant_id: Optional[str] = None


class TenantRead(BaseModel):
    id: str
    name: str
    groups: List[Dict[str, Any]]


class AnalyzeRequest(BaseModel):
    tenants: List[TenantRead]
    template: Optional[List[Group]] = None

    _unique = field_validator("template")(lambda groups: _unique_names(groups) if groups else groups)


class ApplyRequest(ScopedRequest):
    template: List[Group] = Field(..., min_length=1)
    group_names: List[str] = Field(..., min_length=1)

    _unique = field_validator("template")(_unique_names)


# ==================== Helpers ====================

def _check_r1(controller_id: int, user: User, db: Session):
    controller = validate_controller_access(controller_id, user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail=f"Controller {controller_id} must be RuckusONE, "
                                                    f"got {controller.controller_type}")
    return controller


def _tenant(controller, requested: Optional[str]) -> Optional[str]:
    """resolve_tenant_id, plus: a direct key can only ever answer for its own
    tenant, so asking it about another one must not come back as that one's data."""
    tenant_id = resolve_tenant_id(controller, requested)
    if controller.controller_subtype != "MSP" and tenant_id != controller.r1_tenant_id:
        raise HTTPException(status_code=400, detail=f"{controller.name} is not a controller for tenant {tenant_id}")
    return tenant_id


def _profile(profile_id: int, user: User, db: Session) -> RadiusStandard:
    profile = db.query(RadiusStandard).filter(
        RadiusStandard.id == profile_id, RadiusStandard.user_id == user.id,
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Standard not found")
    return profile


def _profile_json(profile: RadiusStandard) -> Dict[str, Any]:
    return {
        "id": profile.id, "name": profile.name, "groups": profile.groups,
        "source": profile.source, "source_tenant_id": profile.source_tenant_id,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def _slim(group: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("id", "name", "attributeAssignments", "externalAssignmentsCount")
    return {k: group.get(k) for k in keys}


# ==================== Standards ====================

@router.get("/profiles")
@require_beta()
async def list_profiles(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.query(RadiusStandard).filter(RadiusStandard.user_id == current_user.id).order_by(RadiusStandard.name).all()
    return {"profiles": [_profile_json(p) for p in rows]}


@router.post("/profiles")
@require_beta()
async def create_profile(
    body: ProfileBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = RadiusStandard(
        user_id=current_user.id, name=body.name.strip(), groups=[g.model_dump() for g in body.groups],
        source=body.source, source_tenant_id=body.source_tenant_id,
    )
    db.add(profile)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f'A standard named "{body.name}" already exists')
    db.refresh(profile)
    return _profile_json(profile)


@router.put("/profiles/{profile_id}")
@require_beta()
async def update_profile(
    profile_id: int,
    body: ProfileBody,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    profile = _profile(profile_id, current_user, db)
    profile.name = body.name.strip()
    profile.groups = [g.model_dump() for g in body.groups]
    profile.source = body.source
    profile.source_tenant_id = body.source_tenant_id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f'A standard named "{body.name}" already exists')
    db.refresh(profile)
    return _profile_json(profile)


@router.delete("/profiles/{profile_id}")
@require_beta()
async def delete_profile(
    profile_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.delete(_profile(profile_id, current_user, db))
    db.commit()
    return {"deleted": profile_id}


# ==================== R1 reads ====================

def _read_roster(r1_client) -> List[Dict[str, str]]:
    tenants: List[Dict[str, str]] = []
    page = 1  # /mspecs/query is 1-indexed
    while True:
        response = r1_client.post("/mspecs/query", payload={
            "fields": ["id", "name", "tenantType"], "filters": {"tenantType": ["MSP_EC"]},
            "sortField": "name", "sortOrder": "ASC", "page": page, "pageSize": 500,
        })
        if not response.ok:
            raise Exception(f"Reading the MSP customer list failed ({response.status_code})")
        body = response.json()
        rows = body.get("data") or []
        tenants.extend({"id": r["id"], "name": r.get("name") or r["id"]} for r in rows)
        if not rows or len(tenants) >= (body.get("totalCount") or 0):
            return tenants
        page += 1


@router.post("/tenants")
@require_beta()
async def tenants(
    request: TenantsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    controller = _check_r1(request.r1_controller_id, current_user, db)
    if controller.controller_subtype != "MSP":
        raise HTTPException(status_code=400, detail=f"{controller.name} is not an MSP controller")
    r1_client = create_r1_client_from_controller(controller.id, db)
    try:
        return {"tenants": await asyncio.to_thread(_read_roster, r1_client)}
    except Exception as e:
        logger.exception("[RADIUS Standard] roster read failed")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/attributes")
@require_beta()
async def attributes(
    request: ScopedRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    controller = _check_r1(request.r1_controller_id, current_user, db)
    # The catalogue is R1's, not the tenant's: an MSP key can read it without picking a customer.
    tenant_id = _tenant(controller, request.tenant_id) if controller.controller_subtype != "MSP" else request.tenant_id
    r1_client = create_r1_client_from_controller(controller.id, db)
    try:
        return {"attributes": await asyncio.to_thread(read_attribute_catalogue, r1_client, tenant_id)}
    except Exception as e:
        logger.exception("[RADIUS Standard] attribute catalogue read failed")
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/read")
@require_beta()
async def read(
    request: ScopedRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One tenant's groups. Read-only."""
    controller = _check_r1(request.r1_controller_id, current_user, db)
    tenant_id = _tenant(controller, request.tenant_id)
    r1_client = create_r1_client_from_controller(controller.id, db)
    try:
        groups = await asyncio.to_thread(read_tenant_groups, r1_client, tenant_id)
    except Exception as e:
        logger.exception("[RADIUS Standard] group read failed")
        raise HTTPException(status_code=502, detail=str(e))
    return {"groups": [_slim(g) for g in groups]}


@router.post("/analyze")
@require_beta()
async def analyze(
    request: AnalyzeRequest,
    current_user: User = Depends(get_current_user),
):
    """Consistency across the tenants read, and each one against the template."""
    tenants = [t.model_dump() for t in request.tenants]
    template = [g.model_dump() for g in request.template] if request.template is not None else None
    return {
        "consistency": analyze_consistency(tenants),
        "comparisons": {t["id"]: compare_tenant(template, t["groups"]) for t in tenants} if template is not None else {},
    }


# ==================== Apply ====================

@router.post("/apply")
@require_beta()
async def apply(
    request: ApplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Make the named groups in one tenant match the template.

    Works from a fresh read, not what the page last saw, and reads back after:
    R1 answers 200 to writes that change nothing, so a group only counts as
    done once the re-read matches.
    """
    controller = _check_r1(request.r1_controller_id, current_user, db)
    if controller.controller_subtype == "MSP":
        raise HTTPException(status_code=400, detail="Writes go through the tenant's own controller, not an MSP.")
    tenant_id = _tenant(controller, request.tenant_id)

    template = [g.model_dump() for g in request.template]
    standard = {g["name"]: g for g in template}
    unknown = [n for n in request.group_names if n not in standard]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Not in this template: {', '.join(unknown)}")

    r1_client = create_r1_client_from_controller(controller.id, db)
    try:
        catalogue = await asyncio.to_thread(read_attribute_catalogue, r1_client, tenant_id)
        before = await asyncio.to_thread(read_tenant_groups, r1_client, tenant_id)
    except Exception as e:
        logger.exception("[RADIUS Standard] pre-apply read failed")
        raise HTTPException(status_code=502, detail=str(e))

    known = {a["name"] for a in catalogue}
    current = {g["name"]: g for g in compare_tenant(template, before)["groups"]}
    results: List[Dict[str, Any]] = []
    attempted = set()
    for name in request.group_names:
        attributes = standard[name]["attributes"]
        row = current[name]
        bad = sorted({a["attributeName"] for a in attributes} - known)
        if bad:
            # R1 would take these with a 200 and return nothing at auth time.
            results.append({"name": name, "status": "failed", "error": f"Unknown attribute: {', '.join(bad)}"})
            continue
        if row["status"] == "match":
            results.append({"name": name, "status": "unchanged"})
            continue
        attempted.add(name)
        try:
            if row["status"] == "missing":
                await asyncio.to_thread(
                    _run, r1_client.radius_attributes.create_radius_attribute_group(
                        name=name, attributes=attributes, tenant_id=tenant_id),
                )
                results.append({"name": name, "status": "created"})
            else:
                await asyncio.to_thread(
                    _run, r1_client.radius_attributes.update_radius_attribute_group(
                        group_id=row["group_id"], attributes=attributes, tenant_id=tenant_id),
                )
                results.append({"name": name, "status": "updated"})
        except Exception as e:
            logger.exception("[RADIUS Standard] write failed for %s", name)
            results.append({"name": name, "status": "failed", "error": str(e)})

    try:
        fresh = await asyncio.to_thread(read_tenant_groups, r1_client, tenant_id)
    except Exception as e:
        logger.exception("[RADIUS Standard] post-apply read failed")
        for result in results:
            if result["name"] in attempted and result["status"] != "failed":
                result["status"] = "unverified"
                result["error"] = f"Write sent, but the re-read failed: {e}"
        return {"results": results, "groups": None}

    final = {g["name"]: g for g in compare_tenant(template, fresh)["groups"]}
    for result in results:
        if result["name"] in attempted and result["status"] != "failed" and final[result["name"]]["status"] != "match":
            result["status"] = "failed"
            result["error"] = "R1 accepted the write, but the group still differs from the template"
    return {"results": results, "groups": [_slim(g) for g in fresh]}


def _run(coroutine):
    # The service methods are async in name only; their requests call blocks.
    return asyncio.run(coroutine)
