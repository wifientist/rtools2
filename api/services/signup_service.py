import logging
import random
from datetime import datetime, timedelta
from models.user import User
from crud.crud_users import get_user_by_email
from models.pending_signup import PendingSignupOtp
from sqlalchemy.orm import Session
from dependencies import get_db
from fastapi import Depends

logger = logging.getLogger(__name__)

MAX_OTP_ATTEMPTS = 5

# In-memory tracker: {email: failed_attempt_count}
_signup_otp_attempt_counts: dict[str, int] = {}


def verify_signup_otp(email: str, otp_code: str, db: Session = Depends(get_db)) -> bool:
    pending = db.query(PendingSignupOtp).filter_by(email=email).first()
    if not pending:
        return False

    # Check if too many failed attempts — burn the OTP
    attempts = _signup_otp_attempt_counts.get(email, 0)
    if attempts >= MAX_OTP_ATTEMPTS:
        db.delete(pending)
        db.commit()
        _signup_otp_attempt_counts.pop(email, None)
        logger.warning(f"Signup OTP burned for {email} after {MAX_OTP_ATTEMPTS} failed attempts")
        return False

    if pending.otp_expires_at < datetime.utcnow():
        db.delete(pending)
        db.commit()
        _signup_otp_attempt_counts.pop(email, None)
        return False

    if pending.otp_code != otp_code:
        _signup_otp_attempt_counts[email] = attempts + 1
        remaining = MAX_OTP_ATTEMPTS - attempts - 1
        logger.warning(f"Failed signup OTP attempt for {email} ({remaining} attempts remaining)")
        return False

    # Success — delete pending record and clear counter
    db.delete(pending)
    db.commit()
    _signup_otp_attempt_counts.pop(email, None)

    logger.info(f'Signup OTP verified for {email}')

    return True

def generate_and_store_signup_otp(email: str, db: Session = Depends(get_db)):
    otp = f"{random.randint(100000, 999999)}"
    expires_at = datetime.utcnow() + timedelta(minutes=15)

    pending = db.query(PendingSignupOtp).filter_by(email=email).first()
    if pending:
        pending.otp_code = otp
        pending.otp_expires_at = expires_at
    else:
        pending = PendingSignupOtp(email=email, otp_code=otp, otp_expires_at=expires_at)
        db.add(pending)

    db.commit()

    # Reset attempt counter when a new OTP is issued
    _signup_otp_attempt_counts.pop(email, None)

    logger.info(f'Signup OTP generated for {email}')
    return otp
