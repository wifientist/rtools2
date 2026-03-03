from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class ScheduledReport(Base):
    __tablename__ = "scheduled_reports"
    __table_args__ = (
        UniqueConstraint("report_type", "context_id", name="uq_report_type_context"),
    )

    id = Column(Integer, primary_key=True, index=True)
    report_type = Column(String, nullable=False)  # "migration", "config_audit", etc.
    owner_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    context_id = Column(String, nullable=True)  # e.g., controller_id as string
    enabled = Column(Boolean, default=True, nullable=False)
    frequency = Column(String, default="weekly", nullable=False)  # "daily" / "weekly" / "monthly"
    day_of_week = Column(Integer, default=0, nullable=False)  # 0=Mon, 6=Sun (for weekly)
    recipients = Column(JSON, default=list, nullable=False)
    last_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    owner = relationship("User")
