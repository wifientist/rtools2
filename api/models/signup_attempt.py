from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime
from database import Base


class SignupAttempt(Base):
    __tablename__ = "signup_attempts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, index=True)
    domain = Column(String, nullable=False, index=True)
    reason = Column(String, nullable=False)  # "domain_not_approved" | "domain_pending"
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
