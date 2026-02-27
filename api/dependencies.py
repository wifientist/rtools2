import logging
from datetime import datetime

from fastapi import Request, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
#import security
#from clients.r1_client import get_r1_clients
from models.user import User
from jose import JWTError, jwt
import os
from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal

logger = logging.getLogger("auth.debug")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

### 🚀 Get Database Session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(request: Request, db: Session = Depends(get_db)):  #token: str = Depends(oauth2_scheme),
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    endpoint = request.url.path
    token = request.cookies.get("session")
    has_refresh = request.cookies.get("refresh_token") is not None

    if not token:
        logger.warning(f"[AUTH 401] No session cookie | endpoint={endpoint} has_refresh_cookie={has_refresh}")
        raise credentials_exception

    try:
        payload = jwt.decode(token, os.getenv("AUTH_SECRET_KEY"), algorithms=[os.getenv("AUTH_ALGORITHM")])
        email: str = payload.get("sub")
        role: str = payload.get("role")
        token_type = payload.get("type", "unknown")
        exp = payload.get("exp")
        jti = payload.get("jti", "none")

        # Log token details for debugging
        exp_dt = datetime.utcfromtimestamp(exp) if exp else None
        now = datetime.utcnow()
        remaining = (exp_dt - now).total_seconds() if exp_dt else None
        logger.info(f"[AUTH] Token decoded | endpoint={endpoint} email={email} type={token_type} "
                     f"exp={exp_dt} remaining={remaining:.0f}s jti={jti[:8]}...")

        if email is None or role is None:
            logger.warning(f"[AUTH 401] Token missing email/role | endpoint={endpoint} email={email} role={role}")
            raise credentials_exception

        # Check if token has been revoked
        if jti and jti != "none":
            from security import is_token_revoked
            if is_token_revoked(jti, db):
                logger.warning(f"[AUTH 401] Token revoked | endpoint={endpoint} email={email} jti={jti[:8]}...")
                raise credentials_exception

        user = db.query(User).filter(User.email == email).first()
        if user is None:
            logger.warning(f"[AUTH 401] User not found in DB | endpoint={endpoint} email={email}")
            raise credentials_exception
        return user

    except JWTError as e:
        # This is the most critical log — tells us WHY the JWT failed (expired, bad signature, etc.)
        logger.error(f"[AUTH 401] JWT decode error | endpoint={endpoint} error={type(e).__name__}: {e} "
                     f"has_refresh_cookie={has_refresh}")
        raise credentials_exception

### 🚀 Get Current User
# def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
#     email = security.decode_access_token(token)
#     if not email:
#         raise HTTPException(status_code=401, detail="Invalid or expired token")

#     user = crud.get_user_by_email(db, email)
#     if not user:
#         raise HTTPException(status_code=404, detail="User not found")

#     return user


# # Scoped R1Client Dependency
# def get_scoped_r1_client(selector: str):
#     async def _get_client(r1_clients=Depends(get_r1_clients)):
#         client = r1_clients[selector]
#         if getattr(client, "auth_failed", False):
#             raise HTTPException(status_code=401, detail="R1Client authentication failed")
#         return client
#     return _get_client

