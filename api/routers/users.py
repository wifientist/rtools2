from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import schemas.auth
from  crud.crud_users import get_user_by_email, get_user, create_user
from dependencies import get_db, get_current_user
from decorators import require_role
from models.user import User, RoleEnum

router = APIRouter(prefix="/users", tags=["Users"])

### 🚀 Create New User (Admin Only)
@router.post("/", response_model=schemas.auth.UserResponse)
@require_role(RoleEnum.admin)
def create_user_endpoint(
    user: schemas.auth.UserCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    ADMIN ONLY: Manual user creation endpoint.
    Normal users should use the /auth/signup-verify-otp flow instead.
    """
    if get_user_by_email(db, user.email):
        raise HTTPException(status_code=400, detail="Email already registered")

    return create_user(db, user)

### 🚀 Get User by ID
@router.get("/{user_id}", response_model=schemas.auth.UserResponse)
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return user
