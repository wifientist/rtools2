from sqlalchemy import Column, Integer, String, Text, Date, Boolean, DateTime, JSON
from datetime import datetime
from database import Base


class TrackerSite(Base):
    """One site on the Migration Tracker board.

    Each step has a date (planned until its *_done flag is set). `events`
    records what moved the dates — e.g. a failed validation that pushed
    validation and the hardware swap out a day:
        [{"date": "2026-10-27", "kind": "validation_failed", "note": "...", "slip_days": 1}]
    """
    __tablename__ = "tracker_sites"

    id = Column(Integer, primary_key=True, index=True)
    site_name = Column(String, nullable=False)
    vsz = Column(String, nullable=True)
    tenant = Column(String, nullable=True)
    sz_ap_count = Column(Integer, nullable=True)
    sz_wlan_count = Column(Integer, nullable=True)
    category = Column(String, nullable=True)

    config_pushed = Column(Date, nullable=True)
    config_pushed_done = Column(Boolean, default=False, nullable=False)
    validation = Column(Date, nullable=True)
    validation_done = Column(Boolean, default=False, nullable=False)
    hw_migrated = Column(Date, nullable=True)
    hw_migrated_done = Column(Boolean, default=False, nullable=False)

    notes = Column(Text, nullable=True)
    events = Column(JSON, default=list, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_by = Column(String, nullable=True)
