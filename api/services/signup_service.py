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


def verify_signup_otp(email: str, otp_code: str, db: Session = Depends(get_db)) -> bool:
    pending = db.query(PendingSignupOtp).filter_by(email=email).first()
    if not pending:
        return False
    if pending.otp_code != otp_code:
        return False
    if pending.otp_expires_at < datetime.utcnow():
        return False

    # OTP is valid, delete the pending record
    db.delete(pending)
    db.commit()

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
    logger.info(f'Signup OTP generated for {email}: {otp}')
    return otp
