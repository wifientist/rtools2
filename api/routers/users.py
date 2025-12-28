import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from typing import List
import schemas.auth
from crud.crud_users import get_user_by_email, get_user, create_user
from dependencies import get_db, get_current_user
from decorators import require_role
from models.user import User, RoleEnum
from pydantic import BaseModel, EmailStr
from utils.audit import log_audit_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

# Schemas for user management
class UserUpdateSchema(BaseModel):
    email: EmailStr | None = None
    role: str | None = None
    beta_enabled: bool | None = None
    company_id: int | None = None

### 🚀 Create New User (Admin Only)
@router.post("/", response_model=schemas.auth.UserResponse)
@require_role(RoleEnum.admin)
def create_user_endpoint(
    user: schemas.auth.UserCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new user manually (admin only).
    - Super admins can create users in any company with any role
    - Regular admins can only create users in their own company, cannot create super admins
    """
    if get_user_by_email(db, user.email):
        raise HTTPException(status_code=400, detail="Email already registered")

    # Validate role permissions
    if user.role:
        # Regular admins cannot create super admins
        if current_user.role != RoleEnum.super and user.role == "super":
            raise HTTPException(
                status_code=403,
                detail="Only super admins can create super admin accounts"
            )

        # Validate role exists
        try:
            RoleEnum(user.role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role: {user.role}")

    # Create the user (company will be auto-assigned from email domain)
    new_user = create_user(db, user)

    # Regular admins can only create users in their own company
    if current_user.role != RoleEnum.super:
        if new_user.company_id != current_user.company_id:
            # Rollback the creation
            db.delete(new_user)
            db.commit()
            raise HTTPException(
                status_code=403,
                detail=f"You can only create users in your own company. User email domain does not match your company."
            )

    return new_user

### 🚀 Get User by ID
@router.get("/{user_id}", response_model=schemas.auth.UserResponse)
def get_user_endpoint(user_id: int, db: Session = Depends(get_db)):
    user = get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user

### 🚀 Get All Users (Admin Only)
@router.get("/", response_model=List[schemas.auth.UserResponse])
@require_role(RoleEnum.admin)
def get_all_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get users based on role:
    - Super admins see ALL users across all companies
    - Regular admins see ONLY users in their own company
    """
    if current_user.role == RoleEnum.super:
        # Super admins can see all users
        users = db.query(User).all()
    else:
        # Regular admins can only see users in their company
        if not current_user.company_id:
            raise HTTPException(
                status_code=403,
                detail="Admin users must be assigned to a company"
            )
        users = db.query(User).filter(User.company_id == current_user.company_id).all()

    return users

### 🚀 Update User (Admin Only)
@router.put("/{user_id}", response_model=schemas.auth.UserResponse)
@require_role(RoleEnum.admin)
def update_user_endpoint(
    user_id: int,
    user_update: UserUpdateSchema,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update user details including role and beta access.
    - Super admins can update any user across all companies
    - Regular admins can only update users in their own company
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Regular admins can only update users in their company
    if current_user.role != RoleEnum.super and user.company_id != current_user.company_id:
        raise HTTPException(
            status_code=403,
            detail="You can only update users in your own company"
        )

    # Track changes for audit log
    changes = {}

    if user_update.email is not None and user_update.email != user.email:
        # Check if email is already taken
        existing = get_user_by_email(db, user_update.email)
        if existing and existing.id != user_id:
            raise HTTPException(status_code=400, detail="Email already registered")
        changes["email"] = {"from": user.email, "to": user_update.email}
        user.email = user_update.email

    if user_update.role is not None and user_update.role != user.role:
        # Validate role
        try:
            RoleEnum(user_update.role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid role: {user_update.role}")

        # Regular admins cannot create or modify super admins
        if current_user.role != RoleEnum.super:
            if user_update.role == "super" or user.role == "super":
                raise HTTPException(
                    status_code=403,
                    detail="Only super admins can create or modify super admin accounts"
                )

        changes["role"] = {"from": user.role, "to": user_update.role}
        user.role = user_update.role

    if user_update.beta_enabled is not None and user_update.beta_enabled != user.beta_enabled:
        changes["beta_enabled"] = {"from": user.beta_enabled, "to": user_update.beta_enabled}
        user.beta_enabled = user_update.beta_enabled

    if user_update.company_id is not None and user_update.company_id != user.company_id:
        # Only super admins can change company assignments
        if current_user.role != RoleEnum.super:
            raise HTTPException(
                status_code=403,
                detail="Only super admins can change user company assignments"
            )
        changes["company_id"] = {"from": user.company_id, "to": user_update.company_id}
        user.company_id = user_update.company_id

    db.commit()
    db.refresh(user)

    # Log the update
    if changes:
        log_audit_event(
            db=db,
            action="user_updated",
            actor=current_user,
            target_user_id=user_id,
            details={"changes": changes},
            request=request
        )

    return user

### 🚀 Delete User (Admin Only)
@router.delete("/{user_id}")
@require_role(RoleEnum.admin)
def delete_user_endpoint(
    user_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a user.
    - Super admins can delete any user across all companies
    - Regular admins can only delete users in their own company
    """
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Regular admins can only delete users in their company
    if current_user.role != RoleEnum.super and user.company_id != current_user.company_id:
        raise HTTPException(
            status_code=403,
            detail="You can only delete users in your own company"
        )

    # Prevent deletion of super admins by regular admins
    if current_user.role != RoleEnum.super and user.role == RoleEnum.super:
        raise HTTPException(
            status_code=403,
            detail="Only super admins can delete super admin accounts"
        )

    user_email = user.email
    db.delete(user)
    db.commit()

    # Log the deletion
    log_audit_event(
        db=db,
        action="user_deleted",
        actor=current_user,
        target_user_id=user_id,
        details={"email": user_email},
        request=request
    )

    return {"message": f"User {user_email} deleted successfully"}
