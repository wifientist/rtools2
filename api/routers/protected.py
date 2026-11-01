from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.user import User, RoleEnum
from security import require_admin_company, create_user_token, is_production
from dependencies import get_current_user, get_db
from decorators import require_role
from utils.slack import is_slack_webhook_url, send_slack_message

router = APIRouter() #dependencies=[Depends(get_current_user)])


class BetaToggleRequest(BaseModel):
    beta_enabled: bool


# 🛡️ Protected profile endpoint
@router.get("/user_profile")
def user_profile(user: User = Depends(get_current_user)):
    return {
        "email": user.email,
        "role": user.role,
        "beta_enabled": user.beta_enabled,
        "alpha_enabled": user.alpha_enabled,
        "danger_enabled": user.danger_enabled,
    }


# 🔬 Toggle beta features for current user
@router.post("/toggle_beta")
def toggle_beta(
    request: BetaToggleRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user.beta_enabled = request.beta_enabled
    db.commit()
    db.refresh(user)

    # Regenerate JWT token with updated beta_enabled value
    new_token = create_user_token(user)

    response = JSONResponse(content={
        "message": "Beta features updated",
        "beta_enabled": user.beta_enabled
    })
    response.set_cookie(
        key="session",
        value=new_token,
        httponly=True,
        secure=is_production(),
        samesite="Strict",
    )

    return response


class AlphaToggleRequest(BaseModel):
    alpha_enabled: bool


@router.post("/toggle_alpha")
@require_role(RoleEnum.super)
def toggle_alpha(
    request: AlphaToggleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    current_user.alpha_enabled = request.alpha_enabled
    db.commit()
    db.refresh(current_user)

    new_token = create_user_token(current_user)

    response = JSONResponse(content={
        "message": "Alpha features updated",
        "alpha_enabled": current_user.alpha_enabled
    })
    response.set_cookie(
        key="session",
        value=new_token,
        httponly=True,
        secure=is_production(),
        samesite="Strict",
    )

    return response


class SlackSettingsRequest(BaseModel):
    # Omitted keeps the saved webhook, which the page can never read back.
    webhook_url: Optional[str] = None
    notify_completed: bool = True
    notify_failed: bool = True
    notify_confirmation: bool = True


def _slack_settings(user: User) -> dict:
    return {
        "configured": bool(user.encrypted_slack_webhook),
        "notify_completed": user.slack_notify_completed,
        "notify_failed": user.slack_notify_failed,
        "notify_confirmation": user.slack_notify_confirmation,
    }


@router.get("/slack_settings")
def get_slack_settings(user: User = Depends(get_current_user)):
    return _slack_settings(user)


@router.put("/slack_settings")
def update_slack_settings(
    request: SlackSettingsRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    webhook_url = (request.webhook_url or "").strip()
    if webhook_url:
        if not is_slack_webhook_url(webhook_url):
            raise HTTPException(
                status_code=400,
                detail="Enter a Slack incoming webhook URL (https://hooks.slack.com/services/...)",
            )
        user.set_slack_webhook(webhook_url)
    user.slack_notify_completed = request.notify_completed
    user.slack_notify_failed = request.notify_failed
    user.slack_notify_confirmation = request.notify_confirmation
    db.commit()
    db.refresh(user)
    return _slack_settings(user)


@router.delete("/slack_settings")
def delete_slack_webhook(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user.encrypted_slack_webhook = None
    db.commit()
    db.refresh(user)
    return _slack_settings(user)


@router.post("/slack_settings/test")
async def test_slack_webhook(user: User = Depends(get_current_user)):
    webhook_url = user.get_slack_webhook()
    if not webhook_url:
        raise HTTPException(status_code=400, detail="No Slack webhook saved")
    sent = await send_slack_message(webhook_url, ":wave: Test message from RUCKUS Tools job notifications")
    if not sent:
        raise HTTPException(status_code=502, detail="Slack did not accept the message. Check the webhook URL.")
    return {"sent": True}


@router.get("/admin")
@require_role(RoleEnum.admin)
def admin_dashboard(
    current_user: User = Depends(get_current_user),
    company: dict = Depends(require_admin_company(1))  #TODO i feel like this should be hard coded here
    ):
    return {"message": "Approved Admin!"}

# 🔥 Admin-only endpoint
# @router.get("/admin")
# def get_admin_data(user: User = Depends(require_role("admin"))):
#     return {"message": "Welcome, Admin!"}

# @protected_router.get("/loggedin")
# def dashboard(user: str = Depends(get_current_user)):
#     return {"message": f"Welcome {user}, this is a protected page"}