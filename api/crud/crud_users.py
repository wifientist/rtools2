from sqlalchemy.orm import Session
import models, schemas.auth
#from crud.crud_security import hash_password

### 🚀 Create User
def create_user(db: Session, user: schemas.auth.UserCreate):
    #hashed_pw = hash_password(user.password)
    db_user = models.user.User(email=user.email) #, hashed_password=hashed_pw)
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

