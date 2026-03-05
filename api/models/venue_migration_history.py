from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from database import Base


class VenueMigrationHistory(Base):
    __tablename__ = "venue_migration_history"

    id = Column(Integer, primary_key=True, index=True)
    controller_id = Column(
        Integer,
        ForeignKey("controllers.id", ondelete="CASCADE"),
        nullable=False,
    )
    venue_id = Column(String, nullable=False)
    venue_name = Column(String, nullable=False)
    tenant_id = Column(String, nullable=False)
    tenant_name = Column(String, nullable=False)
    ap_count = Column(Integer, nullable=False, default=0)
    operational = Column(Integer, nullable=False, default=0)
    status = Column(String, nullable=False, default="Pending")

    # Transition timestamps (never overwritten once set)
    pending_at = Column(DateTime, nullable=True)
    in_progress_at = Column(DateTime, nullable=True)
    migrated_at = Column(DateTime, nullable=True)
    removed_at = Column(DateTime, nullable=True)

    controller = relationship("Controller")

    __table_args__ = (
        UniqueConstraint("controller_id", "venue_id", name="uq_controller_venue"),
        Index("ix_vmh_controller_status", "controller_id", "status"),
    )
