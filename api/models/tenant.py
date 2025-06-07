from sqlalchemy import Column, Integer, String, ForeignKey, Float, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy import UniqueConstraint
import datetime
from database import Base
from utils.encryption import encrypt_value, decrypt_value

class Tenant(Base):
    __tablename__ = 'tenants'
    __table_args__ = (
        UniqueConstraint('user_id', 'name', name='uq_user_tenant_name'),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # A label for this R1 instance ("Client A", "Client B", etc.)
    tenant_id = Column(String, nullable=False)  # The unique identifier for this R1 instance
    encrypted_client_id = Column(String, nullable=False)
    encrypted_shared_secret = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    user = relationship("User", back_populates="tenants", foreign_keys="[Tenant.user_id]")

    def set_client_id(self, raw_client_id: str):
        self.encrypted_client_id = encrypt_value(raw_client_id)

    def set_shared_secret(self, raw_shared_secret: str):
        self.encrypted_shared_secret = encrypt_value(raw_shared_secret)

    def get_client_id(self) -> str:
        return decrypt_value(self.encrypted_client_id)

    def get_shared_secret(self) -> str:
        return decrypt_value(self.encrypted_shared_secret)
