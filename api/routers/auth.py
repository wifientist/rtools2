from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import timedelta
from models.user import User
from models.company import Company
from models.controller import Controller
from schemas.auth import TokenResponse, UserCreate, RequestOtpSchema, LoginOtpSchema
from security import create_access_token, verify_access_token, create_user_token, is_production
from dependencies import get_db, get_current_user
from services.auth_service import generate_and_send_otp, verify_otp_and_login
from services.signup_service import generate_and_store_signup_otp, verify_signup_otp
from utils.email import send_otp_email_via_api
from constants.roles import role_hierarchy

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.get("/roles")
def get_role_hierarchy():
    """ Statically serve the role hierarchy """
    return {
        'hierarchy': role_hierarchy
    }

# request OTP for existing users
@router.post("/request-otp")
async def request_otp(payload: RequestOtpSchema, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()

    if not user:
        raise HTTPException(status_code=400, detail="Email not registered. Please sign up.")

    otp = generate_and_send_otp(payload.email, db)
    return {"message": "OTP sent"}

# login OTP for existing users
@router.post("/login-otp")
async def login_otp(payload: LoginOtpSchema, db: Session = Depends(get_db)):
    try:
        # Get user from OTP verification
        user = db.query(User).filter(User.email == payload.email).first()
        if not user:
            raise HTTPException(status_code=400, detail="User not found")

        # Verify OTP (this updates last_authenticated_at)
        token = verify_otp_and_login(payload.email, payload.otp_code, db)

        # Generate both access and refresh tokens
        from security import create_user_tokens
        tokens = create_user_tokens(user)

        response = JSONResponse(content={"message": "Login successful"})

        # Set access token (12 hours)
        response.set_cookie(
            key="session",
            value=tokens["access_token"],
            httponly=True,
            secure=is_production(),
            samesite="Strict",
            max_age=720 * 60  # 12 hours
        )

        # Set refresh token (7 days)
        response.set_cookie(
            key="refresh_token",
            value=tokens["refresh_token"],
            httponly=True,
            secure=is_production(),
            samesite="Strict",
            max_age=7 * 24 * 60 * 60  # 7 days
        )

        return response
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))


# new user signup OTP
@router.post("/signup-request-otp")
async def signup_request_otp(payload: RequestOtpSchema, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == payload.email).first()

    if existing_user:
        raise HTTPException(status_code=400, detail="User already exists.")

    otp = generate_and_store_signup_otp(payload.email, db)

    # Send OTP to the user's email
    send_otp_email_via_api(payload.email, otp)

    return {"message": "Signup OTP sent"}

# Verify OTP for new user signup
@router.post("/signup-verify-otp")
async def signup_verify_otp(payload: LoginOtpSchema, db: Session = Depends(get_db)):
    if not verify_signup_otp(payload.email, payload.otp_code, db):
        raise HTTPException(status_code=401, detail="Invalid or expired OTP")

    # Extract domain from email
    email_domain = payload.email.split('@')[1]

    # Find or create company based on email domain
    company = db.query(Company).filter(Company.domain == email_domain).first()
    if not company:
        # Auto-create company from email domain (unapproved by default)
        company = Company(
            name=email_domain.split('.')[0].capitalize(),  # e.g., "aylic.com" -> "Aylic"
            domain=email_domain,
            is_approved=False  # SECURITY: New domains are NOT approved by default
        )
        db.add(company)
        db.commit()
        db.refresh(company)

        # Block signup - company needs admin approval
        raise HTTPException(
            status_code=403,
            detail=f"Domain '{email_domain}' is not approved for signup. Please contact an administrator."
        )

    # Check if company is approved
    if not company.is_approved:
        raise HTTPException(
            status_code=403,
            detail=f"Domain '{email_domain}' is pending approval. Please contact an administrator."
        )

    # Company is approved - proceed with user creation
    # SECURITY: Always create new signups with user role
    new_user = User(
        email=payload.email,
        company_id=company.id,
        role=RoleEnum.user  # Explicitly set to user role for security
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Issue both access and refresh tokens immediately after signup
    from security import create_user_tokens
    tokens = create_user_tokens(new_user)

    response = JSONResponse(content={"message": "Signup and login successful"})

    # Set access token (12 hours)
    response.set_cookie(
        key="session",
        value=tokens["access_token"],
        httponly=True,
        secure=is_production(),
        samesite="Strict",
        max_age=720 * 60  # 12 hours
    )

    # Set refresh token (7 days)
    response.set_cookie(
        key="refresh_token",
        value=tokens["refresh_token"],
        httponly=True,
        secure=is_production(),
        samesite="Strict",
        max_age=7 * 24 * 60 * 60  # 7 days
    )

    return response

@router.get("/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Get user to fetch controller information
    user_id = payload.get("id")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Fetch controller names
    active_controller_name = None
    secondary_controller_name = None

    if user.active_controller_id:
        active_controller = db.query(Controller).filter(Controller.id == user.active_controller_id).first()
        if active_controller:
            active_controller_name = active_controller.name

    if user.secondary_controller_id:
        secondary_controller = db.query(Controller).filter(Controller.id == user.secondary_controller_id).first()
        if secondary_controller:
            secondary_controller_name = secondary_controller.name

    return JSONResponse(content={
        "message": "Authenticated",
        "user": payload.get("sub"),
        "id": payload.get("id"),
        "role": payload.get("role"),
        "company_id": payload.get("company_id"),
        "beta_enabled": payload.get("beta_enabled", False),
        "active_controller_id": user.active_controller_id,
        "active_controller_name": active_controller_name,
        "secondary_controller_id": user.secondary_controller_id,
        "secondary_controller_name": secondary_controller_name,
    })


### 🚀 Signup Route
@router.post("/signup", response_model=TokenResponse)
def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already exists")

    unassigned_company_id = -1  #default Unassigned company
    company = db.query(Company).filter(Company.id == unassigned_company_id).first()
    #company = db.query(Company).filter(Company.id == user_data.company_id).first()
    #if not company:
    #    raise HTTPException(status_code=404, detail="Invalid company ID")

    # SECURITY: Public signup always creates regular users, never admin/super
    # Explicitly ignore any role field from the request payload
    # Explicitly set role to "user" to prevent privilege escalation
    #hashed_password = get_password_hash(user_data.password)
    new_user = User(
        email=user_data.email,
        role=RoleEnum.user  # Explicitly set to user role for security
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    #access_token = create_access_token({"sub": new_user.email})
    #return {"access_token": access_token, "token_type": "bearer"}
    
    access_token = create_access_token({
        "sub": new_user.email, 
        "id": new_user.id, 
        "role": new_user.role, 
        "company_id": new_user.company_id, 
        # "active_tenant_id": new_user.active_tenant_id, 
        # "active_tenant_instance_name": new_user.active_tenant.instance_name if new_user.active_tenant_id else None,
        # "secondary_tenant_id": new_user.secondary_tenant_id, 
        # "secondary_tenant_instance_name": new_user.secondary_tenant.instance_name if new_user.secondary_tenant_id else None,
    })
    response = JSONResponse(content={"message": "Signup successful"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=is_production(),
        samesite="Strict",
    )

    return response

@router.post("/refresh")
def refresh_access_token(request: Request, db: Session = Depends(get_db)):
    """
    Use a valid refresh token to obtain a new access token.
    Refresh token lasts 7 days, access token lasts 12 hours.
    """
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token provided")

    # Verify refresh token
    payload = verify_access_token(refresh_token, db)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    # Get user from database
    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    # Generate new access token (refresh token stays the same)
    from security import create_user_token
    new_access_token = create_user_token(user)

    response = JSONResponse(content={"message": "Access token refreshed"})
    response.set_cookie(
        key="session",
        value=new_access_token,
        httponly=True,
        secure=is_production(),
        samesite="Strict",
        max_age=720 * 60  # 12 hours in seconds
    )

    return response


@router.post("/logout")
def logout():
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("session", path="/", domain=None)
    response.delete_cookie("refresh_token", path="/", domain=None)
    return response
