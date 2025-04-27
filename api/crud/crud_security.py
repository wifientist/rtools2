from sqlalchemy.orm import Session
import models, schemas
from datetime import datetime
from passlib.context import CryptContext



pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

### 🚀 Hash Password
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

### 🚀 Verify Password
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

### 🚀 Authenticate User
def authenticate_user(db: Session, email: str, password: str):
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


