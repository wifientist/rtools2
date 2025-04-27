import random
from datetime import datetime, timedelta
#from sqlalchemy.ext.asyncio import AsyncSession
from models.user import User
from utils.email import send_otp_email_via_api
from crud.crud_users import get_user_by_email
from sqlalchemy.orm import Session
from dependencies import get_db
from fastapi import Depends
from security import create_access_token

def generate_and_send_otp(email: str, db: Session = Depends(get_db)):

    otp = f"{random.randint(100000, 999999)}"
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    user = get_user_by_email(db, email)
    if not user:
        raise Exception("User not found")

    user.otp_code = otp
    user.otp_expires_at = expires_at
    db.commit()

    print(f"Generated login OTP: {email} : {otp}")

    send_otp_email_via_api(user.email, otp)

def verify_otp_and_login(email: str, otp_code: str, db: Session = Depends(get_db)):

    user = get_user_by_email(db, email)
    if not user:
        raise Exception("User not found")
    
    if str(user.otp_code) != str(otp_code) or user.otp_expires_at < datetime.utcnow():
        raise Exception("Invalid or expired OTP")

    user.last_authenticated_at = datetime.utcnow()
    user.otp_code = None
    user.otp_expires_at = None
    db.commit()

    token = create_access_token({
        "sub": user.email,
        "id": user.id,
        "role": user.role,
        "company_id": user.company_id,
    })

    print(f"Verify login OTP: {email} : {otp_code} ==> {token}")

    return token
