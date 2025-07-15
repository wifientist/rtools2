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

    token = request.cookies.get("session")
    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, os.getenv("AUTH_SECRET_KEY"), algorithms=[os.getenv("AUTH_ALGORITHM")])
        email: str = payload.get("sub")
        role: str = payload.get("role")
        if email is None or role is None:
            raise credentials_exception

        user = db.query(User).filter(User.email == email).first()
        if user is None:
            raise credentials_exception
        return user

    except JWTError:
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

