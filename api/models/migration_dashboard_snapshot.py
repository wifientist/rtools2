from sqlalchemy import Column, Integer, ForeignKey, DateTime, JSON, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class MigrationDashboardSnapshot(Base):
    __tablename__ = "migration_dashboard_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    controller_id = Column(
        Integer,
        ForeignKey("controllers.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_aps = Column(Integer, nullable=False)
    operational_aps = Column(Integer, nullable=False)
    total_switches = Column(Integer, nullable=False, default=0, server_default="0")
    operational_switches = Column(Integer, nullable=False, default=0, server_default="0")
    total_venues = Column(Integer, nullable=False)
    total_clients = Column(Integer, nullable=False)
    total_ecs = Column(Integer, nullable=False)
    # Online + flagged APs across the selected SZ controllers. NULL = not
    # measured (no SZ controllers picked, or a controller failed to answer).
    sz_connected_aps = Column(Integer, nullable=True)
    # Online switches on the same SZ controllers; NULL on the same terms.
    sz_online_switches = Column(Integer, nullable=True)
    tenant_data = Column(JSON, nullable=False)
    captured_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    controller = relationship("Controller")

    __table_args__ = (
        Index("ix_snapshots_controller_captured", "controller_id", "captured_at"),
    )
