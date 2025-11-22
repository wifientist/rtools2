from sqlalchemy.orm import Session
from models.audit_log import AuditLog
from models.user import User
from fastapi import Request
from datetime import datetime
from typing import Optional, Dict, Any


def log_audit_event(
    db: Session,
    action: str,
    actor: Optional[User] = None,
    target_user_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
    request: Optional[Request] = None
):
    """
    Log an audit event to the database.

    Args:
        db: Database session
        action: Type of action (e.g., 'role_change', 'token_revoked', 'user_created')
        actor: User who performed the action
        target_user_id: ID of user affected by the action (if applicable)
        details: Additional context as a dictionary
        request: FastAPI request object (to extract IP and user agent)
    """
    ip_address = None
    user_agent = None

    if request:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

    audit_log = AuditLog(
        action=action,
        actor_id=actor.id if actor else None,
        target_user_id=target_user_id,
        details=details,
        ip_address=ip_address,
        user_agent=user_agent,
        timestamp=datetime.utcnow()
    )

    db.add(audit_log)
    db.commit()


def log_role_change(
    db: Session,
    admin: User,
    target_user_id: int,
    old_role: str,
    new_role: str,
    request: Optional[Request] = None
):
    """Log when an admin changes a user's role."""
    log_audit_event(
        db=db,
        action="role_change",
        actor=admin,
        target_user_id=target_user_id,
        details={
            "old_role": old_role,
            "new_role": new_role
        },
        request=request
    )


def log_token_revocation(
    db: Session,
    admin: User,
    target_user_id: int,
    reason: Optional[str] = None,
    jti: Optional[str] = None,
    request: Optional[Request] = None
):
    """Log when a token is revoked."""
    log_audit_event(
        db=db,
        action="token_revoked",
        actor=admin,
        target_user_id=target_user_id,
        details={
            "reason": reason,
            "jti": jti
        },
        request=request
    )


def log_user_creation(
    db: Session,
    admin: Optional[User],
    new_user_id: int,
    creation_method: str,  # 'otp_signup', 'admin_created', etc.
    request: Optional[Request] = None
):
    """Log when a new user is created."""
    log_audit_event(
        db=db,
        action="user_created",
        actor=admin,
        target_user_id=new_user_id,
        details={
            "method": creation_method
        },
        request=request
    )


def log_login(
    db: Session,
    user_id: int,
    success: bool,
    method: str,  # 'otp', 'refresh_token', etc.
    request: Optional[Request] = None
):
    """Log login attempts."""
    log_audit_event(
        db=db,
        action="login_attempt",
        target_user_id=user_id,
        details={
            "success": success,
            "method": method
        },
        request=request
    )


def log_beta_toggle(
    db: Session,
    user: User,
    enabled: bool,
    request: Optional[Request] = None
):
    """Log when beta features are toggled."""
    log_audit_event(
        db=db,
        action="beta_toggle",
        actor=user,
        target_user_id=user.id,
        details={
            "enabled": enabled
        },
        request=request
    )
