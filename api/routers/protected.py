from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.user import User, RoleEnum
from security import require_admin_company, create_user_token, is_production
from dependencies import get_current_user, get_db
from decorators import require_role

router = APIRouter() #dependencies=[Depends(get_current_user)])


class BetaToggleRequest(BaseModel):
    beta_enabled: bool


# 🛡️ Protected profile endpoint
@router.get("/user_profile")
def user_profile(user: User = Depends(get_current_user)):
    return {
        "email": user.email,
        "role": user.role,
        "beta_enabled": user.beta_enabled
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