from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, BigInteger, JSON, Enum, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base
import enum


class PermissionType(str, enum.Enum):
    DOWNLOAD = "download"
    UPLOAD = "upload"


class FileFolder(Base):
    """Root-level folders (one per company + super-created special folders)"""
    __tablename__ = "file_folders"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)  # "Ruckus Networks"
    slug = Column(String(50), unique=True, nullable=False)  # "ruckus"
    description = Column(String(500), nullable=True)

    # If set, users in this company get automatic download access
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=True, index=True)

    quota_bytes = Column(BigInteger, default=10 * 1024 * 1024 * 1024, nullable=False)  # 10GB
    used_bytes = Column(BigInteger, default=0, nullable=False)

    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    company = relationship("Company")
    created_by = relationship("User", foreign_keys=[created_by_id])
    subfolders = relationship("FileSubfolder", back_populates="folder", cascade="all, delete-orphan")
    permissions = relationship("FolderPermission", back_populates="folder", cascade="all, delete-orphan")
    files = relationship("SharedFile", back_populates="folder", cascade="all, delete-orphan")


class FileSubfolder(Base):
    """Subfolders for organization (inherit parent permissions)"""
    __tablename__ = "file_subfolders"

    id = Column(Integer, primary_key=True, index=True)
    folder_id = Column(Integer, ForeignKey("file_folders.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)  # "vsz_backups"
    slug = Column(String(50), nullable=False)  # "vsz-backups"

    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    folder = relationship("FileFolder", back_populates="subfolders")
    created_by = relationship("User", foreign_keys=[created_by_id])
    files = relationship("SharedFile", back_populates="subfolder", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('folder_id', 'slug', name='uq_subfolder_slug'),
    )


class FolderPermission(Base):
    """Explicit permissions (upload always, download for non-company folders)"""
    __tablename__ = "folder_permissions"

    id = Column(Integer, primary_key=True, index=True)
    folder_id = Column(Integer, ForeignKey("file_folders.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_type = Column(String(20), nullable=False)  # 'download' | 'upload'

    granted_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    folder = relationship("FileFolder", back_populates="permissions")
    user = relationship("User", foreign_keys=[user_id])
    granted_by = relationship("User", foreign_keys=[granted_by_id])

    __table_args__ = (
        UniqueConstraint('folder_id', 'user_id', 'permission_type', name='uq_folder_user_perm'),
    )


class SharedFile(Base):
    """Files stored in S3"""
    __tablename__ = "shared_files"

    id = Column(Integer, primary_key=True, index=True)
    folder_id = Column(Integer, ForeignKey("file_folders.id", ondelete="CASCADE"), nullable=False, index=True)
    subfolder_id = Column(Integer, ForeignKey("file_subfolders.id", ondelete="CASCADE"), nullable=True, index=True)

    filename = Column(String(255), nullable=False)  # Original filename
    s3_key = Column(String(500), nullable=False)  # Full S3 object key
    size_bytes = Column(BigInteger, nullable=False)
    content_type = Column(String(100), nullable=False)

    # Upload status for multipart uploads
    upload_status = Column(String(20), default="pending", nullable=False)  # 'pending', 'completed', 'failed'
    s3_upload_id = Column(String(200), nullable=True)  # For multipart uploads

    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)  # uploaded_at + 30 days

    download_count = Column(Integer, default=0, nullable=False)

    # Relationships
    folder = relationship("FileFolder", back_populates="files")
    subfolder = relationship("FileSubfolder", back_populates="files")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])


class FileshareAuditLog(Base):
    """Permanent audit log for fileshare operations (super visibility)"""
    __tablename__ = "fileshare_audit_logs"

    id = Column(Integer, primary_key=True, index=True)

    # Who performed the action
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    user_email = Column(String(255), nullable=False)  # Denormalized for log permanence

    # What action was performed
    action = Column(String(50), nullable=False, index=True)  # 'upload', 'download', 'delete', 'bulk_download'

    # Target file info (denormalized - file may be deleted later)
    file_id = Column(Integer, nullable=True)
    filename = Column(String(255), nullable=False)
    folder_slug = Column(String(50), nullable=False)
    subfolder_slug = Column(String(50), nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)

    # For bulk downloads, store list of file IDs
    bulk_file_ids = Column(JSON, nullable=True)

    # Metadata
    ip_address = Column(String(45), nullable=True)  # IPv6 max length
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    user = relationship("User")
