from sqlalchemy import Column, Integer, String, ForeignKey, Float, DateTime, Enum, Boolean
from sqlalchemy.orm import relationship
import datetime
from database import Base


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False)
    domain = Column(String, unique=True, nullable=False)
    is_approved = Column(Boolean, default=False, nullable=False, server_default='false')
    created_at = Column(DateTime, default=datetime.datetime.utcnow, nullable=True)

    # Relationship: A company has multiple users
    users = relationship("User", back_populates="company")

