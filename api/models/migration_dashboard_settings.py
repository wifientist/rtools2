from sqlalchemy import Column, Integer, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class MigrationDashboardSettings(Base):
    __tablename__ = "migration_dashboard_settings"

    id = Column(Integer, primary_key=True, index=True)
    controller_id = Column(
        Integer,
        ForeignKey("controllers.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    target_aps = Column(Integer, default=180000, nullable=False)
    target_switches = Column(Integer, default=10000, nullable=False)
    ignored_tenant_ids = Column(JSON, default=list, nullable=False)
    # SmartZone controllers whose connected APs count as "left to migrate".
    # When set, AP progress is R1 / (R1 + SZ connected) and target_aps is unused.
    sz_controller_ids = Column(JSON, default=list, nullable=False, server_default="[]")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    controller = relationship("Controller")
