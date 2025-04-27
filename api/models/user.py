from sqlalchemy import Column, Integer, String, ForeignKey, Float, DateTime, Enum
from sqlalchemy.orm import relationship
import datetime
from database import Base
import enum

class RoleEnum(str, enum.Enum):
    admin = "admin"
    user = "user"

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    # hashed_password = Column(String, nullable=False)
    role = Column(Enum(RoleEnum), default=RoleEnum.user, nullable=False)  # Default role is "user"

    otp_code = Column(String, nullable=True)
    otp_expires_at = Column(DateTime, nullable=True)
    last_authenticated_at = Column(DateTime, nullable=True)

    # 🔹 Add ForeignKey to link Users to Companies
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, default=-1)

    # Relationships
    #proposals = relationship("Proposal", back_populates="creator", cascade="all, delete")
    #bids = relationship("Bid", back_populates="bidder", cascade="all, delete")
    company = relationship("Company", back_populates="users")


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    domain = Column(String, unique=True, nullable=False)

    # Relationship: A company has multiple users
    users = relationship("User", back_populates="company")


# class Proposal(Base):
#     __tablename__ = "proposals"

#     id = Column(Integer, primary_key=True, index=True)
#     title = Column(String)
#     description = Column(String)
#     budget = Column(Float)
#     location = Column(String)
#     deadline = Column(DateTime)
#     created_by = Column(Integer, ForeignKey("users.id"))

#     # Relationships
#     creator = relationship("User", back_populates="proposals")
#     bids = relationship("Bid", back_populates="proposal", cascade="all, delete")


# class Bid(Base):
#     __tablename__ = "bids"

#     id = Column(Integer, primary_key=True, index=True)
#     proposal_id = Column(Integer, ForeignKey("proposals.id"))
#     bidder_id = Column(Integer, ForeignKey("users.id"))
#     amount = Column(Float)
#     message = Column(String)
#     submitted_at = Column(DateTime, default=datetime.datetime.utcnow)

#     # Relationships
#     proposal = relationship("Proposal", back_populates="bids")
#     bidder = relationship("User", back_populates="bids")
