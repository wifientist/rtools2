from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship, validates
from datetime import datetime
from database import Base
from utils.encryption import encrypt_value, decrypt_value


class Controller(Base):
    """
    Controller model representing both RuckusONE and SmartZone controllers.

    Hierarchy:
    - RuckusONE (cloud platform)
      - MSP (Managed Service Provider)
      - EC (End Customer)
    - SmartZone (on-premise/cloud controller)

    Note: Field naming avoids confusion with R1's "tenant" terminology:
    - controller.id = our database PK
    - controller.r1_tenant_id = R1's tenant identifier (for RuckusONE only)
    """
    __tablename__ = "controllers"

    # ===== Common Fields (All Controllers) =====
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)  # User-friendly label

    # Controller type hierarchy
    controller_type = Column(String, nullable=False)  # "RuckusONE" or "SmartZone"
    controller_subtype = Column(String, nullable=True)  # "MSP" | "EC" (RuckusONE only)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ===== RuckusONE-Specific Fields (nullable for SmartZone) =====
    r1_tenant_id = Column(String, nullable=True)  # R1's tenant identifier
    r1_region = Column(String, nullable=True)  # "NA", "EU", "APAC"
    encrypted_r1_client_id = Column(String, nullable=True)
    encrypted_r1_shared_secret = Column(String, nullable=True)

    # ===== SmartZone-Specific Fields (nullable for RuckusONE) =====
    sz_host = Column(String, nullable=True)  # Hostname or IP
    sz_port = Column(Integer, nullable=True, default=8443)
    sz_use_https = Column(Boolean, nullable=True, default=True)
    encrypted_sz_username = Column(String, nullable=True)
    encrypted_sz_password = Column(String, nullable=True)
    sz_version = Column(String, nullable=True)  # e.g., "6.1", "7.0"

    # ===== Relationships =====
    user = relationship("User", back_populates="controllers", foreign_keys=[user_id])

    # ===== Constraints =====
    __table_args__ = (
        UniqueConstraint('user_id', 'name', name='uq_user_controller_name'),
    )

    # ===== Validation =====
    @validates('controller_type')
    def validate_controller_type(self, key, value):
        """Validate controller_type is a known value"""
        allowed = ["RuckusONE", "SmartZone"]
        if value not in allowed:
            raise ValueError(f"controller_type must be one of {allowed}, got: {value}")
        return value

    @validates('controller_subtype')
    def validate_controller_subtype(self, key, value):
        """Validate controller_subtype is appropriate for the controller_type"""
        if value is not None:
            allowed = ["MSP", "EC"]
            if value not in allowed:
                raise ValueError(f"controller_subtype must be one of {allowed} or None, got: {value}")

            # Subtype only valid for RuckusONE
            if self.controller_type == "SmartZone" and value is not None:
                raise ValueError("controller_subtype must be None for SmartZone controllers")

        return value

    @validates('r1_region')
    def validate_r1_region(self, key, value):
        """Validate R1 region value"""
        if value is not None:
            allowed = ["NA", "EU", "APAC"]
            if value not in allowed:
                raise ValueError(f"r1_region must be one of {allowed}, got: {value}")
        return value

    # ===== RuckusONE Credential Management =====
    def set_r1_client_id(self, raw_value: str):
        """Encrypt and store R1 client ID"""
        self.encrypted_r1_client_id = encrypt_value(raw_value)

    def get_r1_client_id(self) -> str:
        """Decrypt and return R1 client ID"""
        if not self.encrypted_r1_client_id:
            return ""
        return decrypt_value(self.encrypted_r1_client_id)

    def set_r1_shared_secret(self, raw_value: str):
        """Encrypt and store R1 shared secret"""
        self.encrypted_r1_shared_secret = encrypt_value(raw_value)

    def get_r1_shared_secret(self) -> str:
        """Decrypt and return R1 shared secret"""
        if not self.encrypted_r1_shared_secret:
            return ""
        return decrypt_value(self.encrypted_r1_shared_secret)

    # ===== SmartZone Credential Management =====
    def set_sz_username(self, raw_value: str):
        """Encrypt and store SmartZone username"""
        self.encrypted_sz_username = encrypt_value(raw_value)

    def get_sz_username(self) -> str:
        """Decrypt and return SmartZone username"""
        if not self.encrypted_sz_username:
            return ""
        return decrypt_value(self.encrypted_sz_username)

    def set_sz_password(self, raw_value: str):
        """Encrypt and store SmartZone password"""
        self.encrypted_sz_password = encrypt_value(raw_value)

    def get_sz_password(self) -> str:
        """Decrypt and return SmartZone password"""
        if not self.encrypted_sz_password:
            return ""
        return decrypt_value(self.encrypted_sz_password)

    # ===== Helper Methods =====
    def is_ruckusone(self) -> bool:
        """Check if this is a RuckusONE controller"""
        return self.controller_type == "RuckusONE"

    def is_smartzone(self) -> bool:
        """Check if this is a SmartZone controller"""
        return self.controller_type == "SmartZone"

    def is_msp(self) -> bool:
        """Check if this is an MSP RuckusONE controller"""
        return self.controller_type == "RuckusONE" and self.controller_subtype == "MSP"

    def is_ec(self) -> bool:
        """Check if this is an EC RuckusONE controller"""
        return self.controller_type == "RuckusONE" and self.controller_subtype == "EC"

    def __repr__(self):
        return f"<Controller id={self.id} name='{self.name}' type={self.controller_type}/{self.controller_subtype}>"
