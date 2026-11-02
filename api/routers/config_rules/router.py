"""
Config Rules: a company's shared rules, checked across the venues of one tenant.

Rulesets belong to the company and any of its users may edit them; there is no
per-company role to narrow that by (admin and super are system-wide). A run
belongs to the user who started it, like the controller it used.

Works on a direct EC key or an MSP key delegating to one EC: resolve_tenant_id
refuses an MSP with no EC chosen. Reads only; see workflow/phases/config_rules.py.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from clients.r1_client import create_r1_client_from_controller
from clients.sz_client_deps import validate_controller_access
from decorators import require_beta
from dependencies import get_current_user, get_db
from models.config_ruleset import ConfigRuleset
from models.user import User
from redis_client import get_redis_client
from routers.tenant_scope import resolve_tenant_id
from services.config_rules import (
    CATALOG, OP_LABELS, STARTER_RULES, build_report, clean_rules, sections_needed,
)
from workflow.events import WorkflowEventPublisher
from workflow.phases.config_rules import CAPTURE_KEY, decode_capture
from workflow.v2.activity_tracker import ActivityTracker
from workflow.v2.brain import WorkflowBrain
from workflow.v2.failsafe import fail_job
from workflow.v2.models import JobStatus
from workflow.v2.state_manager import RedisStateManagerV2
from workflow.workflows.config_rules import ConfigRulesWorkflow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/config-rules", tags=["Config Rules"])

RUN_MAX_AGE = timedelta(hours=1)


class RulesetBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    # The updated_at this edit started from: a colleague's newer save is not overwritten.
    expected_updated_at: Optional[str] = None


class RunRequest(BaseModel):
    controller_id: int
    tenant_id: Optional[str] = None
    ruleset_id: int
    venue_ids: List[str] = Field(min_length=1)


def _view(ruleset: ConfigRuleset) -> Dict[str, Any]:
    return {"id": ruleset.id, "name": ruleset.name, "rules": ruleset.rules or [],
            "updated_at": ruleset.updated_at.isoformat() if ruleset.updated_at else None}


def _owned(ruleset_id: int, user: User, db: Session) -> ConfigRuleset:
    ruleset = db.query(ConfigRuleset).filter(
        ConfigRuleset.id == ruleset_id, ConfigRuleset.company_id == user.company_id).first()
    if not ruleset:
        raise HTTPException(status_code=404, detail="Ruleset not found")
    return ruleset


def _clean(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    try:
        return clean_rules(rules)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/catalog")
@require_beta()
async def catalog(current_user: User = Depends(get_current_user)):
    """The settings a rule can test, and a starting set of rules."""
    return {"fields": CATALOG, "op_labels": OP_LABELS, "starter_rules": STARTER_RULES}


@router.get("/rulesets")
@require_beta()
async def list_rulesets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(ConfigRuleset).filter(ConfigRuleset.company_id == current_user.company_id).order_by(
        ConfigRuleset.name).all()
    return {"rulesets": [_view(r) for r in rows]}


@router.post("/rulesets")
@require_beta()
async def create_ruleset(body: RulesetBody, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    ruleset = ConfigRuleset(company_id=current_user.company_id, name=body.name.strip(),
                            rules=_clean(body.rules), updated_by=current_user.id)
    db.add(ruleset)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Your company already has a ruleset with that name")
    db.refresh(ruleset)
    return _view(ruleset)


@router.put("/rulesets/{ruleset_id}")
@require_beta()
async def update_ruleset(ruleset_id: int, body: RulesetBody, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    ruleset = _owned(ruleset_id, current_user, db)
    if body.expected_updated_at and ruleset.updated_at and body.expected_updated_at != ruleset.updated_at.isoformat():
        raise HTTPException(status_code=409,
                            detail="Someone else saved this ruleset while you were editing. Reload it first.")
    ruleset.name = body.name.strip()
    ruleset.rules = _clean(body.rules)
    ruleset.updated_by = current_user.id
    ruleset.updated_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Your company already has a ruleset with that name")
    db.refresh(ruleset)
    return _view(ruleset)


@router.delete("/rulesets/{ruleset_id}")
@require_beta()
async def delete_ruleset(ruleset_id: int, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    db.delete(_owned(ruleset_id, current_user, db))
    db.commit()
    return {"deleted": ruleset_id}


async def _run_job(job_id: str, controller_id: int):
    """Validate and carry straight on: a read-only job has nothing to confirm."""
    from database import SessionLocal
    db = SessionLocal()
    try:
        redis_client = await get_redis_client()
        state_manager = RedisStateManagerV2(redis_client)
        job = await state_manager.get_job(job_id)
        if not job:
            logger.error(f"[Config Rules] Job {job_id} not found in Redis")
            return
        r1_client = create_r1_client_from_controller(controller_id, db)
        brain = WorkflowBrain(
            state_manager=state_manager,
            activity_tracker=ActivityTracker(r1_client, state_manager, tenant_id=job.tenant_id),
            event_publisher=WorkflowEventPublisher(redis_client),
            r1_client=r1_client,
        )
        await brain.run_validation(job)
        job = await state_manager.get_job(job_id)
        if job.status != JobStatus.AWAITING_CONFIRMATION or (job.validation_result and not job.validation_result.valid):
            logger.warning(f"[Config Rules] Job {job_id} did not validate (status={job.status.value})")
            if job.status == JobStatus.AWAITING_CONFIRMATION:
                job.status = JobStatus.FAILED
                job.errors.append("None of the chosen venues are in this tenant.")
                await state_manager.save_job(job)
            return
        await brain.tracker.start()
        await brain.execute_workflow(job)
        await brain.tracker.stop()
        logger.info(f"[Config Rules] Job {job_id} finished (status={job.status.value})")
    except Exception as e:
        logger.exception(f"[Config Rules] Job {job_id} failed: {e}")
        await fail_job(None, job_id, e, source="Config Rules")
    finally:
        db.close()


async def _running_job(redis_client, state_manager, user_id: int, tenant_id: str) -> Optional[str]:
    """
    This user's run already going on this tenant. Runs on a tenant share its
    pacer, so a second one only slows both. A run older than RUN_MAX_AGE is
    taken for dead (a restart leaves its job RUNNING) and does not block a new one.
    """
    for job_id in await redis_client.smembers("workflow:v2:jobs:active"):
        job = await state_manager.get_job(job_id)
        if (job and job.workflow_name == ConfigRulesWorkflow.name and job.user_id == user_id
                and job.tenant_id == tenant_id and datetime.utcnow() - job.created_at < RUN_MAX_AGE):
            return job.id
    return None


@router.post("/run")
@require_beta()
async def run(body: RunRequest, background_tasks: BackgroundTasks = BackgroundTasks(),
              current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    controller = validate_controller_access(body.controller_id, current_user, db)
    if controller.controller_type != "RuckusONE":
        raise HTTPException(status_code=400, detail="Choose a RuckusONE controller")
    tenant_id = resolve_tenant_id(controller, body.tenant_id)
    ruleset = _owned(body.ruleset_id, current_user, db)
    rules = [r for r in (ruleset.rules or []) if r.get("enabled", True)]
    if not rules:
        raise HTTPException(status_code=400, detail="This ruleset has no enabled rules")
    venue_ids = list(dict.fromkeys(body.venue_ids))

    redis_client = await get_redis_client()
    state_manager = RedisStateManagerV2(redis_client)
    running = await _running_job(redis_client, state_manager, current_user.id, tenant_id)
    if running:
        return {"job_id": running, "status": "RUNNING", "already_running": True}
    brain = WorkflowBrain(
        state_manager=state_manager,
        activity_tracker=ActivityTracker(None, state_manager, tenant_id=tenant_id),
    )
    job = await brain.create_job(
        workflow=ConfigRulesWorkflow,
        venue_id="",
        tenant_id=tenant_id,
        controller_id=body.controller_id,
        user_id=current_user.id,
        options={},
        input_data={
            "venue_ids": venue_ids,
            "sections": sorted(sections_needed(rules)),
            "ruleset_id": ruleset.id,
            "ruleset_name": ruleset.name,
            "rules": rules,
        },
    )
    background_tasks.add_task(_run_job, job.id, body.controller_id)
    logger.info(f"[Config Rules] Job {job.id}: {len(rules)} rules on {len(venue_ids)} venues")
    return {"job_id": job.id, "status": "VALIDATING"}


@router.get("/runs/{job_id}/report")
@require_beta()
async def report(job_id: str, ruleset_id: Optional[int] = None, current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """
    Evaluate a run's capture. No R1 calls. Uses the rules as they were when it
    ran, or with ruleset_id the company's current ones; a setting the run did
    not read then shows as unavailable.
    """
    redis_client = await get_redis_client()
    job = await RedisStateManagerV2(redis_client).get_job(job_id)
    if not job or job.workflow_name != ConfigRulesWorkflow.name or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Run not found")
    rules = _owned(ruleset_id, current_user, db).rules if ruleset_id else job.input_data.get("rules") or []
    stored = await redis_client.hgetall(CAPTURE_KEY.format(job_id=job_id))
    if not stored and job.status not in (JobStatus.RUNNING, JobStatus.VALIDATING):
        raise HTTPException(status_code=410, detail="This run's capture has expired. Run it again.")
    captures, wlans = [], None
    for name, raw in stored.items():
        if name == "wlans":
            wlans = decode_capture(raw)
        else:
            captures.append(decode_capture(raw))
    return {
        "job_id": job_id,
        "status": job.status.value,
        "ruleset_name": job.input_data.get("ruleset_name"),
        "venues_requested": len(job.input_data.get("venue_ids") or []),
        **build_report(rules, captures, wlans),
    }
