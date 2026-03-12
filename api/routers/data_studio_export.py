"""
Data Studio Export Router — super admin only.

CRUD for export configurations, manual trigger, run history,
and credential testing.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from dependencies import get_db, get_current_user
from decorators import require_role
from models.user import User, RoleEnum
from models.company import Company
from models.data_studio_export import DataStudioExportConfig, DataStudioExportRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data-studio-export", tags=["Data Studio Export"])


# ── Schemas ──────────────────────────────────────────────────


class TenantConfigItem(BaseModel):
    tenant_id: str
    tenant_name: str


class ExportConfigCreate(BaseModel):
    company_id: int
    web_username: str
    web_password: str
    report_name: str
    tenant_configs: list[TenantConfigItem]
    interval_minutes: int = 60
    retention_count: int = 24


class ExportConfigUpdate(BaseModel):
    web_username: Optional[str] = None
    web_password: Optional[str] = None
    report_name: Optional[str] = None
    tenant_configs: Optional[list[TenantConfigItem]] = None
    interval_minutes: Optional[int] = None
    retention_count: Optional[int] = None
    enabled: Optional[bool] = None


from email_validator import validate_email, EmailNotValidError


class EmailExportRequest(BaseModel):
    recipients: list[str]

    @classmethod
    def __get_validators__(cls):
        yield from super().__get_validators__()

    def validate_recipients(self):
        invalid = []
        for addr in self.recipients:
            try:
                validate_email(addr, check_deliverability=False)
            except EmailNotValidError:
                invalid.append(addr)
        return invalid


class TestLoginRequest(BaseModel):
    web_username: str
    web_password: str


# ── Helpers ──────────────────────────────────────────────────


def _get_config_or_404(config_id: int, db: Session) -> DataStudioExportConfig:
    config = db.query(DataStudioExportConfig).filter(DataStudioExportConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="Export config not found")
    return config


def _config_to_dict(config: DataStudioExportConfig) -> dict:
    return {
        "id": config.id,
        "company_id": config.company_id,
        "company_name": config.company.name if config.company else "Unknown",
        "report_name": config.report_name,
        "tenant_configs": config.tenant_configs or [],
        "interval_minutes": config.interval_minutes,
        "retention_count": config.retention_count,
        "enabled": config.enabled,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


def _run_to_dict(run: DataStudioExportRun) -> dict:
    return {
        "id": run.id,
        "config_id": run.config_id,
        "tenant_id": run.tenant_id,
        "tenant_name": run.tenant_name,
        "status": run.status,
        "error_message": run.error_message,
        "screenshot_s3_key": run.screenshot_s3_key,
        "s3_key": run.s3_key,
        "shared_file_id": run.shared_file_id,
        "file_size_bytes": run.file_size_bytes,
        "filename": run.filename,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_seconds": run.duration_seconds,
    }


# ── Endpoints ────────────────────────────────────────────────


@router.get("/configs")
@require_role(RoleEnum.super)
async def list_configs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all Data Studio export configurations."""
    configs = db.query(DataStudioExportConfig).all()
    return [_config_to_dict(c) for c in configs]


@router.post("/configs", status_code=201)
@require_role(RoleEnum.super)
async def create_config(
    data: ExportConfigCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new Data Studio export configuration."""
    company = db.query(Company).filter(Company.id == data.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    config = DataStudioExportConfig(
        company_id=data.company_id,
        report_name=data.report_name,
        tenant_configs=[tc.model_dump() for tc in data.tenant_configs],
        interval_minutes=data.interval_minutes,
        retention_count=data.retention_count,
        created_by_id=current_user.id,
    )
    config.set_web_username(data.web_username)
    config.set_web_password(data.web_password)

    db.add(config)
    db.commit()
    db.refresh(config)

    logger.info(f"Created Data Studio export config {config.id}")
    return _config_to_dict(config)


@router.get("/configs/{config_id}")
@require_role(RoleEnum.super)
async def get_config(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a Data Studio export configuration."""
    config = _get_config_or_404(config_id, db)
    return _config_to_dict(config)


@router.put("/configs/{config_id}")
@require_role(RoleEnum.super)
async def update_config(
    config_id: int,
    data: ExportConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a Data Studio export configuration."""
    config = _get_config_or_404(config_id, db)

    if data.web_username is not None:
        config.set_web_username(data.web_username)
    if data.web_password is not None:
        config.set_web_password(data.web_password)
    if data.report_name is not None:
        config.report_name = data.report_name
    if data.tenant_configs is not None:
        config.tenant_configs = [tc.model_dump() for tc in data.tenant_configs]
    if data.interval_minutes is not None:
        config.interval_minutes = data.interval_minutes
    if data.retention_count is not None:
        config.retention_count = data.retention_count
    if data.enabled is not None:
        config.enabled = data.enabled

    db.commit()
    db.refresh(config)

    logger.info(f"Updated Data Studio export config {config_id}")
    return _config_to_dict(config)


@router.delete("/configs/{config_id}", status_code=204)
@require_role(RoleEnum.super)
async def delete_config(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a Data Studio export configuration and its run history."""
    config = _get_config_or_404(config_id, db)
    db.delete(config)
    db.commit()
    logger.info(f"Deleted Data Studio export config {config_id}")


@router.post("/configs/{config_id}/trigger")
@require_role(RoleEnum.super)
async def trigger_export(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger an export for a specific config."""
    config = _get_config_or_404(config_id, db)
    if not config.enabled:
        raise HTTPException(status_code=400, detail="Config is disabled. Enable it first.")

    from jobs.data_studio_export_job import export_single_config
    # Run in background so the endpoint returns immediately
    asyncio.create_task(export_single_config(config_id))

    return {"status": "triggered", "config_id": config_id, "message": "Export started in background"}


@router.get("/configs/{config_id}/runs")
@require_role(RoleEnum.super)
async def list_runs(
    config_id: int,
    limit: int = 50,
    offset: int = 0,
    tenant_id: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get export run history for a config."""
    _get_config_or_404(config_id, db)

    query = (
        db.query(DataStudioExportRun)
        .filter(DataStudioExportRun.config_id == config_id)
    )
    if tenant_id:
        query = query.filter(DataStudioExportRun.tenant_id == tenant_id)

    total = query.count()
    runs = (
        query
        .order_by(DataStudioExportRun.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "runs": [_run_to_dict(r) for r in runs],
    }


@router.get("/configs/{config_id}/runs/latest")
@require_role(RoleEnum.super)
async def latest_runs(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the most recent run for each tenant in a config."""
    config = _get_config_or_404(config_id, db)
    tenant_configs = config.tenant_configs or []

    results = []
    for tc in tenant_configs:
        tid = tc.get("tenant_id", "")
        latest = (
            db.query(DataStudioExportRun)
            .filter(
                DataStudioExportRun.config_id == config_id,
                DataStudioExportRun.tenant_id == tid,
            )
            .order_by(DataStudioExportRun.started_at.desc())
            .first()
        )
        results.append({
            "tenant_id": tid,
            "tenant_name": tc.get("tenant_name", tid),
            "latest_run": _run_to_dict(latest) if latest else None,
        })

    return results


@router.post("/configs/{config_id}/test-login")
@require_role(RoleEnum.super)
async def test_login_endpoint(
    config_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Test the web credentials for an existing config by attempting login."""
    config = _get_config_or_404(config_id, db)

    from services.data_studio_scraper import test_login
    username = config.get_web_username()
    password = config.get_web_password()

    success, error = await test_login(username, password)

    return {
        "success": success,
        "error": error if not success else None,
    }


@router.post("/test-login")
@require_role(RoleEnum.super)
async def test_login_raw(
    data: TestLoginRequest,
    current_user: User = Depends(get_current_user),
):
    """Test web credentials before creating a config."""
    from services.data_studio_scraper import test_login

    success, error = await test_login(data.web_username, data.web_password)

    return {
        "success": success,
        "error": error if not success else None,
    }


@router.post("/{config_id}/email")
@require_role(RoleEnum.super)
async def email_latest_exports(
    config_id: int,
    data: EmailExportRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Email the latest CSV export per tenant to the specified recipients."""
    if not data.recipients:
        raise HTTPException(status_code=400, detail="At least one recipient is required")

    invalid = data.validate_recipients()
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid email address(es): {', '.join(invalid)}")

    config = _get_config_or_404(config_id, db)
    tenant_configs = config.tenant_configs or []

    if not tenant_configs:
        raise HTTPException(status_code=400, detail="No tenants configured")

    from services.s3_service import get_s3_service
    from utils.email import send_email_with_attachments

    s3 = get_s3_service()
    if not s3.is_configured:
        raise HTTPException(status_code=503, detail="S3 not configured")

    # Collect latest successful CSV per tenant
    attachments = []  # list of (bytes, filename)
    tenant_summary = []

    for tc in tenant_configs:
        tid = tc.get("tenant_id", "")
        tname = tc.get("tenant_name", tid)

        latest = (
            db.query(DataStudioExportRun)
            .filter(
                DataStudioExportRun.config_id == config_id,
                DataStudioExportRun.tenant_id == tid,
                DataStudioExportRun.status == "success",
                DataStudioExportRun.s3_key != None,
            )
            .order_by(DataStudioExportRun.started_at.desc())
            .first()
        )

        if not latest or not latest.s3_key:
            tenant_summary.append(f"  - {tname}: no successful export found")
            continue

        try:
            stream = s3.get_object_stream(latest.s3_key)
            csv_bytes = stream.read()
            # Use the actual S3 filename (already includes tenant slug + .csv extension)
            fname = latest.s3_key.split("/")[-1]
            attachments.append((csv_bytes, fname))
            tenant_summary.append(f"  - {tname}: {fname} ({len(csv_bytes):,} bytes)")
        except Exception as e:
            logger.warning(f"Failed to download {latest.s3_key}: {e}")
            tenant_summary.append(f"  - {tname}: download failed")

    if not attachments:
        raise HTTPException(status_code=404, detail="No CSV exports available to email")

    company_name = config.company.name if config.company else "Unknown"
    subject = f"Data Studio Export: {config.report_name} - {company_name}"

    summary_text = "\n".join(tenant_summary)
    text_body = (
        f"Data Studio report export for {company_name}.\n\n"
        f"Report: {config.report_name}\n"
        f"Tenants ({len(attachments)} file{'s' if len(attachments) != 1 else ''} attached):\n"
        f"{summary_text}\n\n"
        f"— RUCKUS.Tools"
    )

    html_body = f"""
<h2>Data Studio Export: {config.report_name}</h2>
<p>Company: <strong>{company_name}</strong></p>
<p>{len(attachments)} CSV file{"s" if len(attachments) != 1 else ""} attached:</p>
<ul>
{"".join(f"<li>{line.strip().lstrip('- ')}</li>" for line in tenant_summary)}
</ul>
<hr>
<p style="color: #666; font-size: 12px;">RUCKUS.Tools — Data Studio Export</p>
"""

    success = send_email_with_attachments(
        to_email=data.recipients,
        subject=subject,
        text_body=text_body,
        html_body=html_body,
        attachments=attachments,
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to send email via SES")

    logger.info(
        f"Data Studio export emailed by {current_user.email}: "
        f"config={config_id}, {len(attachments)} files to {data.recipients}"
    )

    return {
        "status": "sent",
        "recipients": data.recipients,
        "attachments": len(attachments),
    }
