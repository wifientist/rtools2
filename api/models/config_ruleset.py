from sqlalchemy import Column, Integer, String, DateTime, JSON, ForeignKey, UniqueConstraint
from datetime import datetime
from database import Base


class ConfigRuleset(Base):
    """A company's config rules, shared and editable by everyone in the company.

    `rules`: [{"id", "name", "field", "op", "value", "enabled"}]; see
    services/config_rules.py. Rules name settings, never a tenant or venue, so
    one ruleset can be run against any of the company's tenants.
    """
    __tablename__ = "config_rulesets"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_config_rulesets_company_name"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    rules = Column(JSON, default=list, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
