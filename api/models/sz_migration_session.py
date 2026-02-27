from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from datetime import datetime

from database import Base


class SZMigrationSession(Base):
    __tablename__ = "sz_migration_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, nullable=False, default="draft")
    # draft | extracting | reviewing | executing | completed | failed
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Source (SZ)
    sz_controller_id = Column(Integer, ForeignKey("controllers.id", ondelete="SET NULL"), nullable=True)
    sz_domain_id = Column(String, nullable=True)
    sz_zone_id = Column(String, nullable=True)
    sz_zone_name = Column(String, nullable=True)

    # Destination (R1)
    r1_controller_id = Column(Integer, ForeignKey("controllers.id", ondelete="SET NULL"), nullable=True)
    r1_tenant_id = Column(String, nullable=True)
    r1_venue_id = Column(String, nullable=True)
    r1_venue_name = Column(String, nullable=True)

    # Job references (Redis keys — may expire, but IDs stay for audit)
    extraction_job_id = Column(String, nullable=True)
    r1_snapshot_job_id = Column(String, nullable=True)
    plan_job_id = Column(String, nullable=True)
    execution_job_id = Column(String, nullable=True)

    # Cached summary (survives Redis expiry)
    current_step = Column(Integer, nullable=False, default=1)
    wlan_count = Column(Integer, nullable=True)
    summary_json = Column(JSON, nullable=True)

    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    sz_controller = relationship("Controller", foreign_keys=[sz_controller_id])
    r1_controller = relationship("Controller", foreign_keys=[r1_controller_id])
