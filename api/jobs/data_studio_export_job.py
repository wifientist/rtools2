"""
Scheduled job: Data Studio report export.

Runs hourly. For each enabled DataStudioExportConfig, launches a Playwright
browser, logs into ruckus.cloud, and exports a named report as CSV for each
configured tenant. Uploads results to S3 via the fileshare system.
"""
import logging
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

from database import SessionLocal
from models.data_studio_export import DataStudioExportConfig, DataStudioExportRun
from models.fileshare import FileFolder, FileSubfolder, SharedFile
from services.data_studio_scraper import ScraperSession
from services.s3_service import get_s3_service

logger = logging.getLogger(__name__)

JOB_ID = "data_studio_export"
TRIGGER_CONFIG = {"minutes": 60}


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def _get_company_folder(db, company_id: int, created_by_id: int) -> FileFolder:
    """Get or create the fileshare folder for a company."""
    from models.company import Company

    folder = db.query(FileFolder).filter(FileFolder.company_id == company_id).first()
    if not folder:
        company = db.query(Company).filter(Company.id == company_id).first()
        company_slug = _slugify(company.name) if company else f"company-{company_id}"
        folder = FileFolder(
            name=company.name if company else f"Company {company_id}",
            slug=company_slug,
            description=f"Shared files for {company.name}" if company else "",
            company_id=company_id,
            quota_bytes=50 * 1024 * 1024 * 1024,  # 50GB quota
            created_by_id=created_by_id,
        )
        db.add(folder)
        db.commit()
        db.refresh(folder)
        logger.info(f"Created company fileshare folder: {company_slug}")
    return folder


def _ensure_data_studio_parent(db, folder: FileFolder, created_by_id: int) -> FileSubfolder:
    """Get or create the 'Data Studio' parent subfolder within the company folder."""
    subfolder = (
        db.query(FileSubfolder)
        .filter(
            FileSubfolder.folder_id == folder.id,
            FileSubfolder.parent_subfolder_id == None,
            FileSubfolder.slug == "data-studio",
        )
        .first()
    )
    if not subfolder:
        subfolder = FileSubfolder(
            folder_id=folder.id,
            parent_subfolder_id=None,
            name="Data Studio",
            slug="data-studio",
            created_by_id=created_by_id,
        )
        db.add(subfolder)
        db.commit()
        db.refresh(subfolder)
        logger.info(f"Created Data Studio parent subfolder in {folder.slug}")
    return subfolder


def _ensure_tenant_subfolder(db, folder: FileFolder, parent: FileSubfolder, tenant_slug: str, tenant_name: str, created_by_id: int) -> FileSubfolder:
    """Get or create a tenant subfolder nested under the Data Studio parent."""
    subfolder = (
        db.query(FileSubfolder)
        .filter(
            FileSubfolder.folder_id == folder.id,
            FileSubfolder.parent_subfolder_id == parent.id,
            FileSubfolder.slug == tenant_slug,
        )
        .first()
    )
    if not subfolder:
        subfolder = FileSubfolder(
            folder_id=folder.id,
            parent_subfolder_id=parent.id,
            name=tenant_name,
            slug=tenant_slug,
            created_by_id=created_by_id,
        )
        db.add(subfolder)
        db.commit()
        db.refresh(subfolder)
        logger.info(f"Created tenant subfolder: data-studio/{tenant_slug}")
    return subfolder


def _cleanup_old_exports(
    db,
    s3,
    config_id: int,
    tenant_id: str,
    retention_count: int,
    folder: FileFolder,
):
    """Delete exports beyond the retention limit for a tenant."""
    runs = (
        db.query(DataStudioExportRun)
        .filter(
            DataStudioExportRun.config_id == config_id,
            DataStudioExportRun.tenant_id == tenant_id,
            DataStudioExportRun.status == "success",
        )
        .order_by(DataStudioExportRun.started_at.desc())
        .all()
    )

    if len(runs) <= retention_count:
        return

    old_runs = runs[retention_count:]
    for run in old_runs:
        # Delete S3 object
        if run.s3_key:
            s3.delete_object(run.s3_key)

        # Delete SharedFile record
        if run.shared_file_id:
            shared_file = db.query(SharedFile).filter(SharedFile.id == run.shared_file_id).first()
            if shared_file:
                folder.used_bytes = max(0, folder.used_bytes - (shared_file.size_bytes or 0))
                db.delete(shared_file)

        # Delete the run record
        db.delete(run)

    db.commit()
    logger.info(f"Cleaned up {len(old_runs)} old exports for tenant {tenant_id}")


async def run_data_studio_export() -> Dict[str, Any]:
    """Main job entry point — called by the scheduler hourly."""
    db = SessionLocal()
    s3 = get_s3_service()
    results = []

    try:
        configs = (
            db.query(DataStudioExportConfig)
            .filter(DataStudioExportConfig.enabled == True)
            .all()
        )

        if not configs:
            logger.info("No enabled Data Studio export configs, skipping")
            return {"status": "skipped", "reason": "no_enabled_configs", "exports": 0}

        for config in configs:
            config_result = await _process_config(db, s3, config)
            results.append(config_result)

        total_success = sum(r["succeeded"] for r in results)
        total_failed = sum(r["failed"] for r in results)
        logger.info(f"Data Studio export complete: {total_success} succeeded, {total_failed} failed")

        return {
            "status": "success",
            "configs_processed": len(configs),
            "total_succeeded": total_success,
            "total_failed": total_failed,
            "results": results,
        }
    except Exception as e:
        logger.error(f"Data Studio export job failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


async def _process_config(db, s3, config: DataStudioExportConfig) -> Dict[str, Any]:
    """Process a single export config — login once, export for all tenants."""
    username = config.get_web_username()
    password = config.get_web_password()
    report_name = config.report_name
    tenant_configs = config.tenant_configs or []

    config_result = {
        "config_id": config.id,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "tenant_results": [],
    }

    if not tenant_configs:
        logger.info(f"Config {config.id}: no tenants configured, skipping")
        return config_result

    # Ensure company fileshare folder and Data Studio parent subfolder exist
    folder = _get_company_folder(db, config.company_id, config.created_by_id)
    ds_parent = _ensure_data_studio_parent(db, folder, config.created_by_id)

    session = ScraperSession(username=username, password=password)
    try:
        await session.start()

        # Login once for all tenants
        login_success, login_error = await session.login()
        if not login_success:
            logger.error(f"Config {config.id}: login failed — {login_error}")
            # Record failure for all tenants
            for tc in tenant_configs:
                _record_run(db, config.id, tc, "failed", error=f"Login failed: {login_error}")
                config_result["failed"] += 1
            return config_result

        # Export for each tenant
        report_slug = _slugify(report_name)
        for tc in tenant_configs:
            tenant_id = tc.get("tenant_id", "")
            tenant_name = tc.get("tenant_name", tenant_id)
            tenant_slug = _slugify(tenant_name) or _slugify(tenant_id)

            try:
                # Ensure tenant subfolder exists before export (needed for debug screenshots on failure too)
                subfolder = _ensure_tenant_subfolder(db, folder, ds_parent, tenant_slug, tenant_name, config.created_by_id)

                result = await session.export_tenant_report(tenant_id, report_name)

                if result.success and result.csv_files:
                    now = datetime.utcnow()
                    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")

                    # Upload each extracted CSV individually
                    uploaded_keys = []
                    total_size = 0
                    csv_items = list(result.csv_files.items())
                    for idx, (csv_name, csv_bytes) in enumerate(csv_items):
                        # Filename: tenant + report slug + timestamp (+ index if multiple CSVs)
                        suffix = f"_{idx + 1}" if len(csv_items) > 1 else ""
                        filename = f"{tenant_slug}_{report_slug}_{timestamp}{suffix}.csv"
                        file_uuid = str(uuid.uuid4())

                        s3_key = s3.generate_s3_key(
                            folder_slug=folder.slug,
                            subfolder_slug=subfolder.subfolder_path,
                            file_uuid=file_uuid,
                            filename=filename,
                        )

                        upload_ok = s3.put_object(s3_key, csv_bytes, "text/csv")
                        if not upload_ok:
                            logger.warning(f"Failed to upload {filename} to S3")
                            continue

                        shared_file = SharedFile(
                            folder_id=folder.id,
                            subfolder_id=subfolder.id,
                            filename=filename,
                            s3_key=s3_key,
                            size_bytes=len(csv_bytes),
                            content_type="text/csv",
                            upload_status="completed",
                            uploaded_by_id=config.created_by_id,
                            uploaded_at=now,
                            expires_at=now + timedelta(days=30),
                        )
                        db.add(shared_file)
                        folder.used_bytes += len(csv_bytes)
                        uploaded_keys.append(s3_key)
                        total_size += len(csv_bytes)

                    db.commit()

                    if not uploaded_keys:
                        _record_run(db, config.id, tc, "failed", error="All S3 uploads failed")
                        config_result["failed"] += 1
                        continue

                    # Record run with first key as primary (for backward compat)
                    _record_run(
                        db, config.id, tc, "success",
                        s3_key=uploaded_keys[0],
                        file_size=total_size,
                        filename=f"{report_slug}_{timestamp} ({len(uploaded_keys)} files)",
                        duration=result.duration_seconds,
                        file_count=len(uploaded_keys),
                    )
                    config_result["succeeded"] += 1

                    # Cleanup old exports
                    _cleanup_old_exports(db, s3, config.id, tenant_id, config.retention_count, folder)

                else:
                    if result.screenshot_bytes:
                        logger.warning(
                            f"Export failed for tenant {tenant_id} with debug screenshot "
                            f"({len(result.screenshot_bytes)} bytes) — not persisted"
                        )

                    _record_run(
                        db, config.id, tc, "failed",
                        error=result.error,
                        duration=result.duration_seconds,
                    )
                    config_result["failed"] += 1

            except Exception as e:
                logger.error(f"Unexpected error exporting for tenant {tenant_id}: {e}", exc_info=True)
                _record_run(db, config.id, tc, "failed", error=str(e))
                config_result["failed"] += 1

    except Exception as e:
        logger.error(f"Config {config.id}: browser session error — {e}", exc_info=True)
        for tc in tenant_configs:
            _record_run(db, config.id, tc, "failed", error=f"Browser error: {str(e)}")
            config_result["failed"] += 1
    finally:
        await session.close()

    return config_result


async def export_single_config(config_id: int) -> Dict[str, Any]:
    """Run export for a single config (used by manual trigger endpoint)."""
    db = SessionLocal()
    s3 = get_s3_service()
    try:
        config = db.query(DataStudioExportConfig).filter(DataStudioExportConfig.id == config_id).first()
        if not config:
            return {"status": "error", "error": "Config not found"}
        return await _process_config(db, s3, config)
    finally:
        db.close()


def _record_run(
    db,
    config_id: int,
    tenant_config: dict,
    status: str,
    error: str = None,
    s3_key: str = None,
    shared_file_id: int = None,
    file_size: int = None,
    filename: str = None,
    duration: float = None,
    file_count: int = None,
):
    """Create a DataStudioExportRun record."""
    now = datetime.utcnow()
    run = DataStudioExportRun(
        config_id=config_id,
        tenant_id=tenant_config.get("tenant_id", ""),
        tenant_name=tenant_config.get("tenant_name"),
        status=status,
        error_message=error,
        s3_key=s3_key,
        shared_file_id=shared_file_id,
        file_size_bytes=file_size,
        filename=filename,
        started_at=now,
        completed_at=now,
        duration_seconds=duration,
    )
    db.add(run)
    db.commit()




async def ensure_registered(scheduler) -> None:
    """Register the Data Studio export job."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        if existing.trigger_config != TRIGGER_CONFIG:
            await scheduler.update_job(JOB_ID, trigger_config=TRIGGER_CONFIG)
            logger.info(f"Updated Data Studio export trigger to {TRIGGER_CONFIG}")
        else:
            logger.info(f"Data Studio export job '{JOB_ID}' already registered")
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="Data Studio Report Export",
        callable_path="jobs.data_studio_export_job:run_data_studio_export",
        trigger_type="interval",
        trigger_config=TRIGGER_CONFIG,
        owner_type="data_studio",
        description="Hourly: exports CSV reports from R1 Data Studio for configured tenants",
    )
    logger.info(f"Registered Data Studio export job '{JOB_ID}' (every 60 minutes)")
