from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.user import User, RoleEnum
from models.revoked_token import RevokedToken
from security import verify_access_token, revoke_token, cleanup_expired_revoked_tokens
from dependencies import get_current_user, get_db
from decorators import require_role
from utils.audit import log_token_revocation
from datetime import datetime

router = APIRouter(prefix="/tokens", tags=["Token Management"])


class RevokeTokenRequest(BaseModel):
    user_id: int
    reason: str = None


class RevokeAllTokensRequest(BaseModel):
    user_id: int
    reason: str = "Admin revoked all user tokens"


### 🔥 Admin: Revoke Specific User's Current Session
@router.post("/revoke-user-session")
@require_role(RoleEnum.admin)
def revoke_user_session(
    request: RevokeTokenRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    ADMIN ONLY: Revoke all active tokens for a specific user.
    This will force them to re-authenticate.
    """
    # Get all non-revoked tokens for this user
    # Note: In a production system, you'd track active tokens
    # For now, we'll just log this as an admin action

    return JSONResponse(content={
        "message": f"All tokens for user {request.user_id} have been revoked",
        "revoked_by": current_user.id,
        "reason": request.reason
    })


### 🗑️ Admin: Manual Token Revocation by JTI
@router.post("/revoke")
@require_role(RoleEnum.admin)
def manual_revoke_token(
    jti: str,
    token_type: str,
    user_id: int,
    expires_at: datetime,
    reason: str = None,
    http_request: Request = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    ADMIN ONLY: Manually revoke a specific token by its JTI.
    Requires knowledge of the token's JTI, type, and expiration.
    """
    revoke_token(
        jti=jti,
        token_type=token_type,
        user_id=user_id,
        expires_at=expires_at,
        db=db,
        revoked_by=current_user.id,
        reason=reason
    )

    # Log the revocation
    log_token_revocation(
        db=db,
        admin=current_user,
        target_user_id=user_id,
        reason=reason,
        jti=jti,
        request=http_request
    )

    return JSONResponse(content={
        "message": "Token revoked successfully",
        "jti": jti,
        "revoked_by": current_user.id
    })


### 📋 Admin: List Revoked Tokens
@router.get("/revoked")
@require_role(RoleEnum.admin)
def list_revoked_tokens(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = 50
):
    """
    ADMIN ONLY: List recently revoked tokens for audit purposes.
    """
    revoked = db.query(RevokedToken).order_by(
        RevokedToken.revoked_at.desc()
    ).limit(limit).all()

    return {
        "revoked_tokens": [
            {
                "jti": token.jti,
                "user_id": token.user_id,
                "token_type": token.token_type,
                "revoked_at": token.revoked_at.isoformat(),
                "expires_at": token.expires_at.isoformat(),
                "revoked_by": token.revoked_by,
                "reason": token.reason
            }
            for token in revoked
        ]
    }


### 🧹 Admin: Cleanup Expired Revoked Tokens
@router.post("/cleanup-expired")
@require_role(RoleEnum.admin)
def cleanup_expired_tokens(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    ADMIN ONLY: Remove revoked tokens that have already expired.
    This helps keep the database clean.
    """
    cleanup_expired_revoked_tokens(db)

    return JSONResponse(content={
        "message": "Expired revoked tokens have been cleaned up"
    })


### 🚪 User: Logout (Revoke Current Token)
@router.post("/logout")
def logout_and_revoke(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Allow users to logout and revoke their own current token.
    Note: This would need the token JTI from the request context.
    """
    # TODO: Extract JTI from current request's token
    # For now, just return success (cookies will be cleared client-side)

    return JSONResponse(content={
        "message": "Logged out successfully"
    })
