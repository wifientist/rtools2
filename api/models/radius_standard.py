from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, UniqueConstraint
from datetime import datetime
from database import Base


class RadiusStandard(Base):
    """One customer's standard RADIUS attribute groups, owned like a controller.

    `groups`:
        [{"name": "fast", "attributes": [{"attributeName": "WISPr-Bandwidth-Max-Down",
          "operator": "ADD", "attributeValue": "524288000", "dataType": "INTEGER"}]}]
    """
    __tablename__ = "radius_standards"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_radius_standards_user_name"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    groups = Column(JSON, default=list, nullable=False)
    source = Column(String, nullable=True)
    source_tenant_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
