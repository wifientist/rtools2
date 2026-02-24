from sqlalchemy.orm import Session
import models, schemas.auth
#from crud.crud_security import hash_password

### 🚀 Create User
def create_user(db: Session, user: schemas.auth.UserCreate):
    #hashed_pw = hash_password(user.password)
    # Build user with provided fields
    user_data = {"email": user.email}
    if user.role is not None:
        user_data["role"] = user.role
    if user.beta_enabled is not None:
        user_data["beta_enabled"] = user.beta_enabled
    if user.alpha_enabled is not None:
        user_data["alpha_enabled"] = user.alpha_enabled
    # Auto-enable alpha for super users
    if user.role == "super" and "alpha_enabled" not in user_data:
        user_data["alpha_enabled"] = True

    db_user = models.user.User(**user_data) #, hashed_password=hashed_pw)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

### 🚀 Get User By ID
def get_user(db: Session, user_id: int):
    return db.query(models.user.User).filter(models.user.User.id == user_id).first()

### 🚀 Get User By Email
def get_user_by_email(db: Session, email: str):
    return db.query(models.user.User).filter(models.user.User.email == email).first()

