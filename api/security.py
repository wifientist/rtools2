from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from dependencies import get_current_user
from models.user import User
import os
import uuid
from dotenv import load_dotenv
load_dotenv() 

# 📌 Load environment variables
AUTH_SECRET_KEY = os.getenv("AUTH_SECRET_KEY")
AUTH_ALGORITHM = os.getenv("AUTH_ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = 720  # 12 hours (one workday)
REFRESH_TOKEN_EXPIRE_DAYS = 7  # 7 days (weekly OTP)
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")  # Default to development for safety

# 🔑 Password hashing setup
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 📌 OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# 🔐 Helper function to determine if cookies should be secure
def is_production():
    """Returns True if running in production environment."""
    return ENVIRONMENT.lower() in ["production", "prod"]


### 🚀 Generate JWT Access Token
def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """
    Create a short-lived access token (12 hours).
    Includes a unique JTI for revocation tracking.
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    jti = str(uuid.uuid4())  # Unique token identifier for revocation
    to_encode.update({
        "exp": expire,
        "jti": jti,
        "type": "access"
    })
    return jwt.encode(to_encode, AUTH_SECRET_KEY, algorithm=AUTH_ALGORITHM)


### 🔄 Generate JWT Refresh Token
def create_refresh_token(user_id: int):
    """
    Create a long-lived refresh token (7 days).
    Used to obtain new access tokens without re-authenticating via OTP.
    """
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    jti = str(uuid.uuid4())
    to_encode = {
        "sub": user_id,
        "exp": expire,
        "jti": jti,
        "type": "refresh"
    }
    return jwt.encode(to_encode, AUTH_SECRET_KEY, algorithm=AUTH_ALGORITHM)


### 🚀 Create JWT Tokens from User Object
def create_user_tokens(user: User):
    """
    Generate both access and refresh tokens from a User object.
    Returns a dict with both tokens.
    """
    access_token = create_access_token({
        "sub": user.email,
        "id": user.id,
        "role": user.role,
        "company_id": user.company_id,
        "beta_enabled": user.beta_enabled,
    })
    refresh_token = create_refresh_token(user.id)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token
    }


### 🚀 Create JWT Token from User Object (Legacy - for backwards compatibility)
def create_user_token(user: User, expires_delta: timedelta | None = None):
    """
    DEPRECATED: Use create_user_tokens() instead for access + refresh tokens.
    Generate a JWT access token from a User object with all necessary fields.
    """
    return create_access_token({
        "sub": user.email,
        "id": user.id,
        "role": user.role,
        "company_id": user.company_id,
        "beta_enabled": user.beta_enabled,
    }, expires_delta)

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
def verify_access_token(token: str, db: Session = None) -> dict:
    """
    Verify and decode a JWT token.
    Optionally checks if token has been revoked (requires db session).
    """
    try:
        payload = jwt.decode(token, AUTH_SECRET_KEY, algorithms=[AUTH_ALGORITHM])

        # Check if token is revoked (if db session provided)
        if db and is_token_revoked(payload.get("jti"), db):
            return None

        return payload  # Contains { "sub": email, "role": ..., ... }
    except JWTError:
        return None


### 🚫 Check if Token is Revoked
def is_token_revoked(jti: str, db: Session) -> bool:
    """
    Check if a token has been revoked by looking up its JTI in the database.
    """
    if not jti:
        return False

    from models.revoked_token import RevokedToken
    revoked = db.query(RevokedToken).filter(RevokedToken.jti == jti).first()
    return revoked is not None


### 🗑️ Revoke Token
def revoke_token(jti: str, token_type: str, user_id: int, expires_at: datetime,
                 db: Session, revoked_by: int = None, reason: str = None):
    """
    Add a token to the revocation list.
    """
    from models.revoked_token import RevokedToken

    revoked_token = RevokedToken(
        jti=jti,
        token_type=token_type,
        user_id=user_id,
        expires_at=expires_at,
        revoked_by=revoked_by,
        reason=reason
    )
    db.add(revoked_token)
    db.commit()


### 🧹 Cleanup Expired Revoked Tokens
def cleanup_expired_revoked_tokens(db: Session):
    """
    Remove revoked tokens that have already expired (no longer need to track them).
    Should be run periodically as a maintenance task.
    """
    from models.revoked_token import RevokedToken

    db.query(RevokedToken).filter(
        RevokedToken.expires_at < datetime.utcnow()
    ).delete()
    db.commit()


# NOTE: require_role has been moved to decorators.py for better organization
# Import from decorators instead: from decorators import require_role

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

