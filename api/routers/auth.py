from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import timedelta
from models.user import User
from models.company import Company
from models.tenant import Tenant
from schemas.auth import TokenResponse, UserCreate, RequestOtpSchema, LoginOtpSchema
from security import create_access_token, verify_access_token #, get_password_hash, verify_password, 
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
        token = verify_otp_and_login(payload.email, payload.otp_code, db)

        response = JSONResponse(content={"message": "Login successful"})
        response.set_cookie(
            key="session",
            value=token,
            httponly=True,
            secure=False,  # 🔥 Remember to set True in prod
            samesite="Strict",
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
    new_user = User(
        email=payload.email,
        company_id=company.id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    # Issue login token immediately after signup
    access_token = create_access_token({
        "sub": new_user.email,
        "id": new_user.id,
        "role": new_user.role,
        "company_id": new_user.company_id
    })

    response = JSONResponse(content={"message": "Signup and login successful"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=False,  # Set to True in prod
        samesite="Strict",
    )

    return response

@router.get("/status")
def auth_status(request: Request):
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    return JSONResponse(content={
        "message": "Authenticated", 
        "user": payload.get("sub"), 
        "id":payload.get("id"), 
        "role": payload.get("role"), 
        "company_id": payload.get("company_id"),
        "active_tenant_id": payload.get("active_tenant_id"), 
        "active_tenant_name": payload.get("active_tenant_name"),
        "secondary_tenant_id": payload.get("secondary_tenant_id"),
        "secondary_tenant_name": payload.get("secondary_tenant_name"),
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

    #hashed_password = get_password_hash(user_data.password)
    new_user = User(email=user_data.email) #, hashed_password=hashed_password)
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
        secure=False,  # Change to True in production
        samesite="Strict",
    )

    return response

@router.post("/logout")
def logout():
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("session", path="/", domain=None) 
    return response
