"""
Scheduled job: Fileshare expired file cleanup.

Deletes files past their expires_at date from both S3 and the database,
and adjusts folder used_bytes accordingly.

Runs daily at 03:00 UTC.
"""

import logging
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)

JOB_ID = "fileshare_expired_cleanup"


async def run_cleanup() -> Dict[str, Any]:
    """Delete expired fileshare files from S3 and the database."""
    from database import SessionLocal
    from models.fileshare import SharedFile
    from services.s3_service import get_s3_service

    s3 = get_s3_service()

    # --- Phase 1: query expired files, then release the connection ---
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        expired_files = db.query(SharedFile).filter(SharedFile.expires_at < now).all()
        if not expired_files:
            logger.info("Fileshare cleanup: no expired files found")
            return {"status": "success", "deleted": 0}

        # Collect the info we need so the session can be closed during S3 I/O
        file_info = [
            {"id": f.id, "s3_key": f.s3_key, "size_bytes": f.size_bytes,
             "folder_id": f.folder_id, "upload_status": f.upload_status,
             "filename": f.filename}
            for f in expired_files
        ]
    finally:
        db.close()

    # --- Phase 2: delete from S3 (no DB connection held) ---
    s3_results = {}  # file_id -> success
    for info in file_info:
        try:
            if s3.is_configured:
                s3.delete_object(info["s3_key"])
            s3_results[info["id"]] = True
        except Exception as e:
            logger.error(f"Failed to delete S3 object for file {info['id']} ({info['filename']}): {e}")
            s3_results[info["id"]] = False

    # --- Phase 3: re-open session, delete DB records for successful S3 deletes ---
    db = SessionLocal()
    deleted = 0
    errors = 0
    try:
        for info in file_info:
            if not s3_results.get(info["id"], False):
                errors += 1
                continue
            try:
                f = db.query(SharedFile).get(info["id"])
                if not f:
                    continue
                folder = f.folder
                if folder and f.upload_status == "completed":
                    folder.used_bytes = max(0, folder.used_bytes - f.size_bytes)
                db.delete(f)
                deleted += 1
            except Exception as e:
                logger.error(f"Failed to delete DB record for file {info['id']}: {e}")
                errors += 1

        db.commit()
        logger.info(f"Fileshare cleanup: deleted {deleted} expired files, {errors} errors")
        return {"status": "success", "deleted": deleted, "errors": errors}
    except Exception as e:
        db.rollback()
        logger.error(f"Fileshare cleanup failed: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


async def ensure_registered(scheduler) -> None:
    """Register the daily cleanup job if it doesn't already exist."""
    existing = await scheduler.get_job(JOB_ID)
    if existing:
        logger.info(f"Fileshare cleanup job '{JOB_ID}' already registered")
        return

    await scheduler.register_job(
        job_id=JOB_ID,
        name="Fileshare Expired File Cleanup",
        callable_path="jobs.fileshare_cleanup_job:run_cleanup",
        trigger_type="cron",
        trigger_config={"hour": 3, "minute": 0},
        owner_type="system",
        description="Daily cleanup of expired fileshare files from S3 and database (03:00 UTC)",
    )
    logger.info(f"Registered fileshare cleanup job '{JOB_ID}' (daily at 03:00 UTC)")
