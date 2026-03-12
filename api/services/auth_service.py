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

MAX_OTP_ATTEMPTS = 5

# In-memory tracker: {email: failed_attempt_count}
# Reset on successful verify or new OTP generation
_otp_attempt_counts: dict[str, int] = {}

def generate_and_send_otp(email: str, db: Session = Depends(get_db)):

    otp = f"{random.randint(100000, 999999)}"
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    user = get_user_by_email(db, email)
    if not user:
        raise Exception("User not found")

    user.otp_code = otp
    user.otp_expires_at = expires_at
    db.commit()

    # Reset attempt counter when a new OTP is issued
    _otp_attempt_counts.pop(email, None)

    logger.info(f"Generated login OTP for {email}")

    send_otp_email_via_api(user.email, otp)

def verify_otp_and_login(email: str, otp_code: str, db: Session = Depends(get_db)):

    user = get_user_by_email(db, email)
    if not user:
        raise Exception("User not found")

    # Check if too many failed attempts — invalidate the OTP
    attempts = _otp_attempt_counts.get(email, 0)
    if attempts >= MAX_OTP_ATTEMPTS:
        # Burn the OTP so they must request a new one
        user.otp_code = None
        user.otp_expires_at = None
        db.commit()
        _otp_attempt_counts.pop(email, None)
        raise Exception("Too many failed attempts. Please request a new OTP.")

    if user.otp_expires_at and user.otp_expires_at < datetime.utcnow():
        user.otp_code = None
        user.otp_expires_at = None
        db.commit()
        _otp_attempt_counts.pop(email, None)
        raise Exception("Invalid or expired OTP")

    if str(user.otp_code) != str(otp_code):
        _otp_attempt_counts[email] = attempts + 1
        remaining = MAX_OTP_ATTEMPTS - attempts - 1
        logger.warning(f"Failed OTP attempt for {email} ({remaining} attempts remaining)")
        raise Exception("Invalid or expired OTP")

    # Success — clear OTP and attempt counter
    user.last_authenticated_at = datetime.utcnow()
    user.otp_code = None
    user.otp_expires_at = None
    db.commit()
    _otp_attempt_counts.pop(email, None)

    token = create_user_token(user)

    logger.info(f"Login OTP verified for {email}")

    return token
