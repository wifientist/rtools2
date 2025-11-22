from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    action = Column(String, nullable=False, index=True)  # e.g., 'role_change', 'token_revoked', 'user_created'
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Who performed the action
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # Who was affected
    details = Column(JSON, nullable=True)  # Additional context as JSON
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    # Relationships
    actor = relationship("User", foreign_keys=[actor_id])
    target_user = relationship("User", foreign_keys=[target_user_id])
