from sqlalchemy import Column, Integer, String, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String, unique=True, nullable=False, index=True)  # JWT ID (unique identifier)
    token_type = Column(String, nullable=False)  # 'access' or 'refresh'
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    revoked_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)  # Original token expiration
    revoked_by = Column(Integer, ForeignKey("users.id"), nullable=True)  # Admin who revoked (if manual)
    reason = Column(String, nullable=True)  # Optional reason for revocation

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    revoker = relationship("User", foreign_keys=[revoked_by])
