from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from dependencies import get_current_user
from models.user import User
from constants.roles import role_hierarchy
import os
from dotenv import load_dotenv
load_dotenv() 

# 📌 Load environment variables
load_dotenv()
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
AUTH_ALGORITHM = os.getenv("AUTH_ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 1 day

# 🔑 Password hashing setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 📌 OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


### 🚀 Generate JWT Token
def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, AUTH_SECRET_KEY, algorithm=AUTH_ALGORITHM)

### 🚀 Decode JWT Token
def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
        return payload.get("sub")  # Returns email
    except JWTError:
        return None

# ### 🔐 Hash Password
# def get_password_hash(password: str) -> str:
#     return pwd_context.hash(password)

# ### 🔐 Verify Password
# def verify_password(plain_password: str, hashed_password: str) -> bool:
#     return pwd_context.verify(plain_password, hashed_password)

### 🔐 Verify Access Token
def verify_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])
        return payload  # Contains { "sub": email, "role": ..., ... }
    except JWTError:
        return None

### 🔒 Require Specific Role (RBAC) - old version, just requiring role explicitly
# def require_role(required_role: str):
#     def role_checker(user: User = Depends(get_current_user)): #dict = Depends(get_current_user)):
#         if user.role != required_role:
#             logging.warning(f"User {user.email} does not have the required role {required_role}")
#             raise HTTPException(status_code=403, detail="Insufficient permissions")
#         return user
#     return role_checker

def require_role(required_role: str):  #new version, pulling constants role heirarchy and allowing role or better
    def role_checker(user: User = Depends(get_current_user)):
        user_level = role_hierarchy.get(user.role, 0)
        required_level = role_hierarchy.get(required_role, 0)
        if user_level < required_level:
            import logging
            logging.warning(f"User {user.email} does not have the required role {required_role}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return user
    return role_checker

#TODO add some more checks here for who can and cannot access certain endpoints
def require_same_company(requested_company_id: int):
    def company_checker(user: User = Depends(get_current_user)):
        if not user.company_id == requested_company_id:
            raise HTTPException(status_code=403, detail="User does not belong to that company")
        return user
    return company_checker

def require_admin_company(required_company_id: int):
    def company_checker(user: User = Depends(get_current_user)):
        if not user.company_id == required_company_id:
            raise HTTPException(status_code=403, detail="User does not belong to an Admin company")
        return user
    return company_checker


### 🔒 Require Specific Role (RBAC)
# def require_role(required_role: str):
#     def role_checker(user: User = Depends(get_current_user)):
#         if user.role != required_role:
#             raise HTTPException(
#                 status_code=status.HTTP_403_FORBIDDEN,
#                 detail="Insufficient permissions"
#             )
#         return user
#     return role_checker



### 👤 Get Current User from Token
# def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(lambda: next(get_db()))):

#     from sqlalchemy.orm import Session
#     from database import get_db
#     db: Session = get_db()

#     email = decode_access_token(token)
#     if email is None:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="Invalid authentication credentials",
#             headers={"WWW-Authenticate": "Bearer"},
#         )
#     user = db.query(User).filter(User.email == email).first()
#     if user is None:
#         raise HTTPException(
#             status_code=status.HTTP_401_UNAUTHORIZED,
#             detail="User not found",
#             headers={"WWW-Authenticate": "Bearer"},
#         )
#     return user

