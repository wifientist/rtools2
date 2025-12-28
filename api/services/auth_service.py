import logging
import random
from datetime import datetime, timedelta
from models.user import User
from utils.email import send_otp_email_via_api
from crud.crud_users import get_user_by_email
from sqlalchemy.orm import Session
from dependencies import get_db
from fastapi import Depends
from security import create_user_token

logger = logging.getLogger(__name__)

def generate_and_send_otp(email: str, db: Session = Depends(get_db)):

    otp = f"{random.randint(100000, 999999)}"
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    user = get_user_by_email(db, email)
    if not user:
        raise Exception("User not found")

    user.otp_code = otp
    user.otp_expires_at = expires_at
    db.commit()

    logger.info(f"Generated login OTP for {email}")

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

    token = create_user_token(user)

    logger.info(f"Login OTP verified for {email}")

    return token
