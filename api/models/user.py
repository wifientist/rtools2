from sqlalchemy import Column, Integer, String, ForeignKey, Float, DateTime, Enum, Boolean
from sqlalchemy.orm import relationship
import datetime
from database import Base
import enum


class RoleEnum(str, enum.Enum):
    user = "user"
    admin = "admin"
    super = "super"


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    # hashed_password = Column(String, nullable=False)
    role = Column(Enum(RoleEnum), default=RoleEnum.user, nullable=False)  # Default role is "user"

    otp_code = Column(String, nullable=True)
    otp_expires_at = Column(DateTime, nullable=True)
    last_authenticated_at = Column(DateTime, nullable=True)

    # Beta feature flag
    beta_enabled = Column(Boolean, default=False, nullable=False)

    # 🔹 Add ForeignKey to link Users to Companies
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, default=-1)

    # 🔹 Track the active R1 instance
    active_tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    secondary_tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)

    # Relationships
    #proposals = relationship("Proposal", back_populates="creator", cascade="all, delete")
    #bids = relationship("Bid", back_populates="bidder", cascade="all, delete")
    company = relationship("Company", back_populates="users")
    tenants = relationship("Tenant", back_populates="user", cascade="all, delete", foreign_keys="[Tenant.user_id]")

