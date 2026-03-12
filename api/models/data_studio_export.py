from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, Float, BigInteger, Text, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base
from utils.encryption import encrypt_value, decrypt_value


class DataStudioExportConfig(Base):
    """Configuration for automated Data Studio report exports via Playwright."""
    __tablename__ = "data_studio_export_configs"

    id = Column(Integer, primary_key=True, index=True)

    # Which company this config belongs to (drives fileshare RBAC)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)

    # Web login credentials (username/password for ruckus.cloud, NOT OAuth2 API creds)
    encrypted_web_username = Column(String, nullable=False)
    encrypted_web_password = Column(String, nullable=False)

    # Report configuration
    report_name = Column(String(255), nullable=False)

    # Which tenants to export: [{tenant_id: str, tenant_name: str}, ...]
    tenant_configs = Column(JSON, nullable=False, default=list)

    # Schedule & retention
    enabled = Column(Boolean, default=True, nullable=False)
    interval_minutes = Column(Integer, default=60, nullable=False)
    retention_count = Column(Integer, default=24, nullable=False)  # Max exports per tenant

    # Metadata
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    company = relationship("Company")
    created_by = relationship("User", foreign_keys=[created_by_id])
    runs = relationship("DataStudioExportRun", back_populates="config", cascade="all, delete-orphan")

    # Credential management (same pattern as Controller model)
    def set_web_username(self, raw_value: str):
        self.encrypted_web_username = encrypt_value(raw_value)

    def get_web_username(self) -> str:
        if not self.encrypted_web_username:
            return ""
        return decrypt_value(self.encrypted_web_username)

    def set_web_password(self, raw_value: str):
        self.encrypted_web_password = encrypt_value(raw_value)

    def get_web_password(self) -> str:
        if not self.encrypted_web_password:
            return ""
        return decrypt_value(self.encrypted_web_password)

    def __repr__(self):
        return f"<DataStudioExportConfig id={self.id} report='{self.report_name}' tenants={len(self.tenant_configs or [])}>"


class DataStudioExportRun(Base):
    """Per-tenant export run history."""
    __tablename__ = "data_studio_export_runs"

    id = Column(Integer, primary_key=True, index=True)
    config_id = Column(Integer, ForeignKey("data_studio_export_configs.id", ondelete="CASCADE"), nullable=False, index=True)

    tenant_id = Column(String, nullable=False, index=True)
    tenant_name = Column(String, nullable=True)

    # Result
    status = Column(String(20), nullable=False)  # "success", "failed", "skipped"
    error_message = Column(Text, nullable=True)
    screenshot_s3_key = Column(String(500), nullable=True)  # Debug screenshot on failure

    # File info (on success)
    s3_key = Column(String(500), nullable=True)
    shared_file_id = Column(Integer, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    filename = Column(String(255), nullable=True)

    # Timing
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Relationships
    config = relationship("DataStudioExportConfig", back_populates="runs")

    def __repr__(self):
        return f"<DataStudioExportRun id={self.id} tenant={self.tenant_id} status={self.status}>"
