from fastapi import APIRouter, Request, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from datetime import timedelta
from models import User, Company
from schemas import TokenResponse, UserCreate
from security import create_access_token, get_password_hash, verify_password, verify_access_token
from dependencies import get_db, get_current_user

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.get("/status")
def auth_status(request: Request):
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    return JSONResponse(content={"message": "Authenticated", "user": payload.get("sub"), "id":payload.get("id"), "role": payload.get("role"), "company_id": payload.get("company_id")})


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

    hashed_password = get_password_hash(user_data.password)
    new_user = User(email=user_data.email, hashed_password=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    #access_token = create_access_token({"sub": new_user.email})
    #return {"access_token": access_token, "token_type": "bearer"}
    
    access_token = create_access_token({"sub": new_user.email, "id": new_user.id, "role": new_user.role, "company_id": new_user.company_id})
    response = JSONResponse(content={"message": "Signup successful"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,
        secure=False,  # Change to True in production
        samesite="Strict",
    )

    return response


@router.post("/token", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form_data.username).first()

    if not user or not verify_password(form_data.password, user.hashed_password):
        print(f'NO USER, OR NO Verify_Password')
        raise HTTPException(status_code=400, detail="Invalid email or password")
    
    access_token = create_access_token({"sub": user.email, "id": user.id, "role": user.role, "company_id": user.company_id})

    response = JSONResponse(content={"message": "Login successful"})
    response.set_cookie(
        key="session",
        value=access_token,
        httponly=True,  # ✅ Prevents access via JavaScript
        secure=False,    # ✅ Only send over HTTPS  TODO change to True for production!!
        samesite="Strict",  # ✅ Protects against CSRF attacks
    )
    return response
    #return {"access_token": access_token, "token_type": "bearer"}


@router.post("/logout")
def logout():
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("session", path="/", domain=None) 
    return response
