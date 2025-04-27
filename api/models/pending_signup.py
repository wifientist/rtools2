from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from database import Base

class PendingSignupOtp(Base):
    __tablename__ = "pending_signup_otps"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False)
    otp_code = Column(String, nullable=False)
    otp_expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
