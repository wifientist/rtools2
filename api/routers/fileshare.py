"""
Fileshare Router - S3-based file sharing with presigned URLs.

Endpoints:
- Folder management (super only)
- Subfolder management (super only)
- Permission management (super only)
- File upload (presigned URLs)
- File download (presigned URLs)
- Audit logs (super only)
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, Field

from models.user import User, RoleEnum
from models.company import Company
from models.fileshare import (
    FileFolder, FileSubfolder, FolderPermission,
    SharedFile, FileshareAuditLog, PermissionType
)
from dependencies import get_db, get_current_user
from decorators import require_role
from services.s3_service import get_s3_service, S3Service
from utils.email import send_report_notification

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fileshare", tags=["Fileshare"])


# =============================================================================
# Pydantic Schemas
# =============================================================================

# Folder schemas
class FolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-z0-9-]+$')
    description: Optional[str] = Field(None, max_length=500)
    company_id: Optional[int] = None  # If set, company users get auto download access
    quota_bytes: int = Field(default=10 * 1024 * 1024 * 1024)  # 10GB default


class FolderUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    quota_bytes: Optional[int] = None
    company_id: Optional[int] = None  # Can be set to null to remove company association


class FolderResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str]
    company_id: Optional[int]
    company_name: Optional[str] = None
    quota_bytes: int
    used_bytes: int
    created_by_email: str
    created_at: datetime
    subfolder_count: int = 0
    file_count: int = 0
    can_download: bool = False
    can_upload: bool = False

    class Config:
        from_attributes = True


# Subfolder schemas
class SubfolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=50, pattern=r'^[a-z0-9-]+$')


class SubfolderResponse(BaseModel):
    id: int
    folder_id: int
    name: str
    slug: str
    created_at: datetime
    file_count: int = 0

    class Config:
        from_attributes = True


# Permission schemas
class PermissionGrantRequest(BaseModel):
    user_id: int
    permission_type: str = Field(..., pattern=r'^(download|upload)$')


class PermissionResponse(BaseModel):
    id: int
    folder_id: int
    user_id: int
    user_email: str
    permission_type: str
    granted_by_email: str
    granted_at: datetime

    class Config:
        from_attributes = True


# File schemas
class UploadInitiateRequest(BaseModel):
    folder_id: int
    subfolder_id: Optional[int] = None
    filename: str = Field(..., min_length=1, max_length=255)
    size_bytes: int = Field(..., gt=0)
    content_type: str = Field(..., min_length=1, max_length=100)


class UploadInitiateResponse(BaseModel):
    file_id: int
    method: str  # 'PUT' for single, 'MULTIPART' for multipart
    upload_url: Optional[str] = None  # For single-part upload
    upload_id: Optional[str] = None  # For multipart
    parts: Optional[list] = None  # For multipart: [{part_number, upload_url}]
    part_size: int = 50 * 1024 * 1024  # 50MB


class UploadCompleteRequest(BaseModel):
    file_id: int
    upload_id: str
    parts: list  # [{part_number, etag}]


class FileResponse(BaseModel):
    id: int
    folder_id: int
    folder_slug: str
    subfolder_id: Optional[int]
    subfolder_slug: Optional[str] = None
    filename: str
    size_bytes: int
    content_type: str
    uploaded_by_email: str
    uploaded_at: datetime
    expires_at: datetime
    download_count: int

    class Config:
        from_attributes = True


class DownloadResponse(BaseModel):
    download_url: str
    expires_in: int


# Audit log schemas
class AuditLogResponse(BaseModel):
    id: int
    user_email: str
    action: str
    filename: str
    folder_slug: str
    subfolder_slug: Optional[str]
    file_size_bytes: Optional[int]
    ip_address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# Terms acceptance schema
class TermsAcceptanceRequest(BaseModel):
    accepted: bool = Field(..., description="Must be true to accept terms")


class TermsAcceptanceResponse(BaseModel):
    message: str
    accepted_at: datetime


# Report file schema
class ReportFileRequest(BaseModel):
    file_id: int
    reason: str = Field(..., min_length=10, max_length=1000)


class ReportFileResponse(BaseModel):
    message: str
    report_id: int


# =============================================================================
# Blocked File Extensions (dangerous/executable files)
# =============================================================================

BLOCKED_EXTENSIONS = {
    # Executables
    '.exe', '.msi', '.bat', '.cmd', '.com', '.scr', '.pif',
    # Scripts
    '.js', '.jse', '.vbs', '.vbe', '.wsf', '.wsh', '.ps1', '.psm1',
    # Shell scripts
    '.sh', '.bash', '.zsh', '.csh', '.ksh',
    # Binary/system
    '.dll', '.sys', '.drv', '.bin',
    # Java
    '.jar', '.class',
    # Installers
    '.dmg', '.pkg', '.deb', '.rpm', '.apk', '.ipa',
    # Macros/templates that can contain macros
    '.docm', '.xlsm', '.pptm', '.dotm', '.xltm', '.potm',
    # Other potentially dangerous
    '.reg', '.inf', '.scf', '.lnk', '.pif', '.application',
    '.gadget', '.hta', '.cpl', '.msc', '.msp',
}

# Allowed content types (business files)
ALLOWED_CONTENT_TYPES = {
    # Documents
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'application/vnd.ms-powerpoint',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'text/plain',
    'text/csv',
    'text/markdown',
    # Images
    'image/jpeg',
    'image/png',
    'image/gif',
    'image/webp',
    'image/svg+xml',
    'image/bmp',
    'image/tiff',
    # Archives (commonly used for legitimate business files)
    'application/zip',
    'application/x-zip-compressed',
    'application/x-rar-compressed',
    'application/x-7z-compressed',
    'application/gzip',
    'application/x-tar',
    # Video
    'video/mp4',
    'video/webm',
    'video/quicktime',
    # Audio
    'audio/mpeg',
    'audio/wav',
    'audio/ogg',
    # Other business formats
    'application/json',
    'application/xml',
    'text/xml',
    'application/rtf',
}


def is_file_allowed(filename: str, content_type: str) -> tuple[bool, str]:
    """Check if file type is allowed. Returns (allowed, reason)."""
    # Check extension
    ext = '.' + filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext in BLOCKED_EXTENSIONS:
        return False, f"File type '{ext}' is not allowed for security reasons"

    # For archives, we allow them but warn they might contain blocked types
    # The responsibility is on the uploader per the ToS

    # Check content type - be permissive but block obviously dangerous types
    dangerous_content_types = {
        'application/x-msdownload',
        'application/x-executable',
        'application/x-dosexec',
    }
    if content_type in dangerous_content_types:
        return False, f"Content type '{content_type}' is not allowed"

    return True, ""


# =============================================================================
# Helper Functions
# =============================================================================

def check_download_permission(db: Session, user: User, folder: FileFolder) -> bool:
    """Check if user can download from folder."""
    if user.role == RoleEnum.super:
        return True
    if folder.company_id and user.company_id == folder.company_id:
        return True
    perm = db.query(FolderPermission).filter(
        FolderPermission.folder_id == folder.id,
        FolderPermission.user_id == user.id,
        FolderPermission.permission_type == 'download'
    ).first()
    return perm is not None


def check_upload_permission(db: Session, user: User, folder: FileFolder) -> bool:
    """Check if user can upload to folder."""
    if user.role == RoleEnum.super:
        return True
    perm = db.query(FolderPermission).filter(
        FolderPermission.folder_id == folder.id,
        FolderPermission.user_id == user.id,
        FolderPermission.permission_type == 'upload'
    ).first()
    return perm is not None


def log_fileshare_action(
    db: Session,
    user: User,
    action: str,
    filename: str,
    folder_slug: str,
    subfolder_slug: Optional[str] = None,
    file_id: Optional[int] = None,
    file_size_bytes: Optional[int] = None,
    bulk_file_ids: Optional[list] = None,
    request: Optional[Request] = None
):
    """Log a fileshare action to the audit log."""
    log_entry = FileshareAuditLog(
        user_id=user.id,
        user_email=user.email,
        action=action,
        file_id=file_id,
        filename=filename,
        folder_slug=folder_slug,
        subfolder_slug=subfolder_slug,
        file_size_bytes=file_size_bytes,
        bulk_file_ids=bulk_file_ids,
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent", "")[:500] if request else None
    )
    db.add(log_entry)
    db.commit()


# =============================================================================
# Folder Endpoints (Super Only)
# =============================================================================

@router.get("/folders", response_model=list[FolderResponse])
def list_folders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all folders the user has access to."""
    folders = db.query(FileFolder).all()

    result = []
    for folder in folders:
        can_download = check_download_permission(db, current_user, folder)
        can_upload = check_upload_permission(db, current_user, folder)

        # Non-super users only see folders they have access to
        if current_user.role != RoleEnum.super and not can_download and not can_upload:
            continue

        result.append(FolderResponse(
            id=folder.id,
            name=folder.name,
            slug=folder.slug,
            description=folder.description,
            company_id=folder.company_id,
            company_name=folder.company.name if folder.company else None,
            quota_bytes=folder.quota_bytes,
            used_bytes=folder.used_bytes,
            created_by_email=folder.created_by.email,
            created_at=folder.created_at,
            subfolder_count=len(folder.subfolders),
            file_count=len(folder.files),
            can_download=can_download,
            can_upload=can_upload
        ))

    return result


@router.post("/folders", response_model=FolderResponse, status_code=status.HTTP_201_CREATED)
@require_role(RoleEnum.super)
def create_folder(
    folder_data: FolderCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new root folder (super only)."""
    # Check slug uniqueness
    existing = db.query(FileFolder).filter(FileFolder.slug == folder_data.slug).first()
    if existing:
        raise HTTPException(status_code=400, detail="Folder with this slug already exists")

    # Validate company_id if provided
    if folder_data.company_id:
        company = db.query(Company).filter(Company.id == folder_data.company_id).first()
        if not company:
            raise HTTPException(status_code=400, detail="Company not found")

    folder = FileFolder(
        name=folder_data.name,
        slug=folder_data.slug,
        description=folder_data.description,
        company_id=folder_data.company_id,
        quota_bytes=folder_data.quota_bytes,
        created_by_id=current_user.id,
        created_at=datetime.utcnow()
    )
    db.add(folder)
    db.commit()
    db.refresh(folder)

    logger.info(f"Folder created: {folder.slug} by {current_user.email}")

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        slug=folder.slug,
        description=folder.description,
        company_id=folder.company_id,
        company_name=folder.company.name if folder.company else None,
        quota_bytes=folder.quota_bytes,
        used_bytes=0,
        created_by_email=current_user.email,
        created_at=folder.created_at,
        subfolder_count=0,
        file_count=0,
        can_download=True,
        can_upload=True
    )


@router.patch("/folders/{folder_id}", response_model=FolderResponse)
@require_role(RoleEnum.super)
def update_folder(
    folder_id: int,
    folder_data: FolderUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update folder details (super only)."""
    folder = db.query(FileFolder).filter(FileFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if folder_data.name is not None:
        folder.name = folder_data.name
    if folder_data.description is not None:
        folder.description = folder_data.description
    if folder_data.quota_bytes is not None:
        folder.quota_bytes = folder_data.quota_bytes
    # Handle company_id - check if explicitly provided (can be set to None to remove)
    if 'company_id' in folder_data.model_fields_set:
        folder.company_id = folder_data.company_id

    db.commit()
    db.refresh(folder)

    return FolderResponse(
        id=folder.id,
        name=folder.name,
        slug=folder.slug,
        description=folder.description,
        company_id=folder.company_id,
        company_name=folder.company.name if folder.company else None,
        quota_bytes=folder.quota_bytes,
        used_bytes=folder.used_bytes,
        created_by_email=folder.created_by.email,
        created_at=folder.created_at,
        subfolder_count=len(folder.subfolders),
        file_count=len(folder.files),
        can_download=True,
        can_upload=True
    )


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_role(RoleEnum.super)
def delete_folder(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a folder (super only). Must be empty."""
    folder = db.query(FileFolder).filter(FileFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if len(folder.files) > 0:
        raise HTTPException(status_code=400, detail="Cannot delete folder with files. Delete files first.")

    if len(folder.subfolders) > 0:
        raise HTTPException(status_code=400, detail="Cannot delete folder with subfolders. Delete subfolders first.")

    db.delete(folder)
    db.commit()

    logger.info(f"Folder deleted: {folder.slug} by {current_user.email}")
    return None


# =============================================================================
# Subfolder Endpoints
# =============================================================================

@router.get("/folders/{folder_id}/subfolders", response_model=list[SubfolderResponse])
def list_subfolders(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List subfolders in a folder."""
    folder = db.query(FileFolder).filter(FileFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if not check_download_permission(db, current_user, folder):
        raise HTTPException(status_code=403, detail="No access to this folder")

    return [
        SubfolderResponse(
            id=sf.id,
            folder_id=sf.folder_id,
            name=sf.name,
            slug=sf.slug,
            created_at=sf.created_at,
            file_count=len(sf.files)
        )
        for sf in folder.subfolders
    ]


@router.post("/folders/{folder_id}/subfolders", response_model=SubfolderResponse, status_code=status.HTTP_201_CREATED)
@require_role(RoleEnum.super)
def create_subfolder(
    folder_id: int,
    subfolder_data: SubfolderCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a subfolder (super only)."""
    folder = db.query(FileFolder).filter(FileFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Check slug uniqueness within folder
    existing = db.query(FileSubfolder).filter(
        FileSubfolder.folder_id == folder_id,
        FileSubfolder.slug == subfolder_data.slug
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Subfolder with this slug already exists in this folder")

    subfolder = FileSubfolder(
        folder_id=folder_id,
        name=subfolder_data.name,
        slug=subfolder_data.slug,
        created_by_id=current_user.id,
        created_at=datetime.utcnow()
    )
    db.add(subfolder)
    db.commit()
    db.refresh(subfolder)

    logger.info(f"Subfolder created: {folder.slug}/{subfolder.slug} by {current_user.email}")

    return SubfolderResponse(
        id=subfolder.id,
        folder_id=subfolder.folder_id,
        name=subfolder.name,
        slug=subfolder.slug,
        created_at=subfolder.created_at,
        file_count=0
    )


@router.delete("/subfolders/{subfolder_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_role(RoleEnum.super)
def delete_subfolder(
    subfolder_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a subfolder (super only). Must be empty."""
    subfolder = db.query(FileSubfolder).filter(FileSubfolder.id == subfolder_id).first()
    if not subfolder:
        raise HTTPException(status_code=404, detail="Subfolder not found")

    if len(subfolder.files) > 0:
        raise HTTPException(status_code=400, detail="Cannot delete subfolder with files. Delete files first.")

    folder_slug = subfolder.folder.slug
    db.delete(subfolder)
    db.commit()

    logger.info(f"Subfolder deleted: {folder_slug}/{subfolder.slug} by {current_user.email}")
    return None


# =============================================================================
# Permission Endpoints (Super Only)
# =============================================================================

@router.get("/folders/{folder_id}/permissions", response_model=list[PermissionResponse])
@require_role(RoleEnum.super)
def list_folder_permissions(
    folder_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all permissions for a folder (super only)."""
    folder = db.query(FileFolder).filter(FileFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    return [
        PermissionResponse(
            id=p.id,
            folder_id=p.folder_id,
            user_id=p.user_id,
            user_email=p.user.email,
            permission_type=p.permission_type,
            granted_by_email=p.granted_by.email,
            granted_at=p.granted_at
        )
        for p in folder.permissions
    ]


@router.post("/folders/{folder_id}/permissions", response_model=PermissionResponse, status_code=status.HTTP_201_CREATED)
@require_role(RoleEnum.super)
def grant_permission(
    folder_id: int,
    perm_data: PermissionGrantRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Grant permission to a user (super only)."""
    folder = db.query(FileFolder).filter(FileFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    # Validate user exists
    from models.user import User as UserModel
    target_user = db.query(UserModel).filter(UserModel.id == perm_data.user_id).first()
    if not target_user:
        raise HTTPException(status_code=400, detail="User not found")

    # Check if permission already exists
    existing = db.query(FolderPermission).filter(
        FolderPermission.folder_id == folder_id,
        FolderPermission.user_id == perm_data.user_id,
        FolderPermission.permission_type == perm_data.permission_type
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Permission already exists")

    perm = FolderPermission(
        folder_id=folder_id,
        user_id=perm_data.user_id,
        permission_type=perm_data.permission_type,
        granted_by_id=current_user.id,
        granted_at=datetime.utcnow()
    )
    db.add(perm)
    db.commit()
    db.refresh(perm)

    logger.info(f"Permission granted: {perm_data.permission_type} on {folder.slug} to {target_user.email} by {current_user.email}")

    return PermissionResponse(
        id=perm.id,
        folder_id=perm.folder_id,
        user_id=perm.user_id,
        user_email=target_user.email,
        permission_type=perm.permission_type,
        granted_by_email=current_user.email,
        granted_at=perm.granted_at
    )


@router.delete("/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_role(RoleEnum.super)
def revoke_permission(
    permission_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Revoke a permission (super only)."""
    perm = db.query(FolderPermission).filter(FolderPermission.id == permission_id).first()
    if not perm:
        raise HTTPException(status_code=404, detail="Permission not found")

    folder_slug = perm.folder.slug
    user_email = perm.user.email
    perm_type = perm.permission_type

    db.delete(perm)
    db.commit()

    logger.info(f"Permission revoked: {perm_type} on {folder_slug} from {user_email} by {current_user.email}")
    return None


# =============================================================================
# File List Endpoint
# =============================================================================

@router.get("/folders/{folder_id}/files", response_model=list[FileResponse])
def list_files(
    folder_id: int,
    subfolder_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List files in a folder (optionally filtered by subfolder)."""
    folder = db.query(FileFolder).filter(FileFolder.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if not check_download_permission(db, current_user, folder):
        raise HTTPException(status_code=403, detail="No access to this folder")

    query = db.query(SharedFile).filter(
        SharedFile.folder_id == folder_id,
        SharedFile.upload_status == 'completed'
    )

    if subfolder_id is not None:
        query = query.filter(SharedFile.subfolder_id == subfolder_id)

    files = query.order_by(SharedFile.uploaded_at.desc()).all()

    return [
        FileResponse(
            id=f.id,
            folder_id=f.folder_id,
            folder_slug=f.folder.slug,
            subfolder_id=f.subfolder_id,
            subfolder_slug=f.subfolder.slug if f.subfolder else None,
            filename=f.filename,
            size_bytes=f.size_bytes,
            content_type=f.content_type,
            uploaded_by_email=f.uploaded_by.email,
            uploaded_at=f.uploaded_at,
            expires_at=f.expires_at,
            download_count=f.download_count
        )
        for f in files
    ]


# =============================================================================
# Upload Endpoints
# =============================================================================

@router.post("/upload/initiate", response_model=UploadInitiateResponse)
def initiate_upload(
    upload_data: UploadInitiateRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Initiate a file upload. Returns presigned URL(s)."""
    s3 = get_s3_service()
    if not s3.is_configured:
        raise HTTPException(status_code=503, detail="File storage service not configured")

    folder = db.query(FileFolder).filter(FileFolder.id == upload_data.folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")

    if not check_upload_permission(db, current_user, folder):
        raise HTTPException(status_code=403, detail="No upload permission for this folder")

    # Validate file type
    allowed, reason = is_file_allowed(upload_data.filename, upload_data.content_type)
    if not allowed:
        raise HTTPException(status_code=400, detail=reason)

    # Validate subfolder if provided
    subfolder = None
    subfolder_slug = None
    if upload_data.subfolder_id:
        subfolder = db.query(FileSubfolder).filter(
            FileSubfolder.id == upload_data.subfolder_id,
            FileSubfolder.folder_id == folder.id
        ).first()
        if not subfolder:
            raise HTTPException(status_code=404, detail="Subfolder not found")
        subfolder_slug = subfolder.slug

    # Check quota
    if folder.used_bytes + upload_data.size_bytes > folder.quota_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Upload would exceed folder quota ({folder.quota_bytes - folder.used_bytes} bytes remaining)"
        )

    # Generate unique file ID and S3 key
    file_uuid = str(uuid.uuid4())
    s3_key = s3.generate_s3_key(folder.slug, file_uuid, upload_data.filename, subfolder_slug)

    # Create file record
    shared_file = SharedFile(
        folder_id=folder.id,
        subfolder_id=subfolder.id if subfolder else None,
        filename=upload_data.filename,
        s3_key=s3_key,
        size_bytes=upload_data.size_bytes,
        content_type=upload_data.content_type,
        upload_status='pending',
        uploaded_by_id=current_user.id,
        uploaded_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(days=30)
    )

    if s3.should_use_multipart(upload_data.size_bytes):
        # Multipart upload
        upload_id = s3.create_multipart_upload(s3_key, upload_data.content_type)
        shared_file.s3_upload_id = upload_id
        db.add(shared_file)
        db.commit()
        db.refresh(shared_file)

        num_parts = s3.calculate_parts(upload_data.size_bytes)
        part_urls = s3.generate_part_upload_urls(s3_key, upload_id, num_parts)

        logger.info(f"Multipart upload initiated: {upload_data.filename} ({num_parts} parts) by {current_user.email}")

        return UploadInitiateResponse(
            file_id=shared_file.id,
            method='MULTIPART',
            upload_id=upload_id,
            parts=part_urls,
            part_size=s3.PART_SIZE
        )
    else:
        # Single-part upload
        db.add(shared_file)
        db.commit()
        db.refresh(shared_file)

        upload_url = s3.generate_upload_url(s3_key, upload_data.content_type)

        logger.info(f"Single-part upload initiated: {upload_data.filename} by {current_user.email}")

        return UploadInitiateResponse(
            file_id=shared_file.id,
            method='PUT',
            upload_url=upload_url
        )


@router.post("/upload/complete")
def complete_upload(
    complete_data: UploadCompleteRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Complete a multipart upload."""
    s3 = get_s3_service()

    shared_file = db.query(SharedFile).filter(SharedFile.id == complete_data.file_id).first()
    if not shared_file:
        raise HTTPException(status_code=404, detail="File not found")

    if shared_file.uploaded_by_id != current_user.id and current_user.role != RoleEnum.super:
        raise HTTPException(status_code=403, detail="Not authorized to complete this upload")

    if shared_file.upload_status != 'pending':
        raise HTTPException(status_code=400, detail="Upload already completed or failed")

    try:
        s3.complete_multipart_upload(shared_file.s3_key, complete_data.upload_id, complete_data.parts)
        shared_file.upload_status = 'completed'

        # Update folder used_bytes
        folder = shared_file.folder
        folder.used_bytes += shared_file.size_bytes

        db.commit()

        # Log the upload
        log_fileshare_action(
            db, current_user, 'upload',
            shared_file.filename, folder.slug,
            shared_file.subfolder.slug if shared_file.subfolder else None,
            shared_file.id, shared_file.size_bytes, request=request
        )

        logger.info(f"Multipart upload completed: {shared_file.filename} by {current_user.email}")

        return {"status": "completed", "file_id": shared_file.id}

    except Exception as e:
        shared_file.upload_status = 'failed'
        db.commit()
        logger.error(f"Multipart upload failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to complete upload")


@router.post("/upload/{file_id}/confirm")
def confirm_single_upload(
    file_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Confirm a single-part upload completed successfully."""
    shared_file = db.query(SharedFile).filter(SharedFile.id == file_id).first()
    if not shared_file:
        raise HTTPException(status_code=404, detail="File not found")

    if shared_file.uploaded_by_id != current_user.id and current_user.role != RoleEnum.super:
        raise HTTPException(status_code=403, detail="Not authorized")

    if shared_file.upload_status != 'pending':
        raise HTTPException(status_code=400, detail="Upload already completed or failed")

    # Verify file exists in S3
    s3 = get_s3_service()
    size = s3.get_object_size(shared_file.s3_key)
    if size is None:
        shared_file.upload_status = 'failed'
        db.commit()
        raise HTTPException(status_code=400, detail="File not found in storage")

    shared_file.upload_status = 'completed'
    shared_file.size_bytes = size  # Update with actual size

    # Update folder used_bytes
    folder = shared_file.folder
    folder.used_bytes += size

    db.commit()

    # Log the upload
    log_fileshare_action(
        db, current_user, 'upload',
        shared_file.filename, folder.slug,
        shared_file.subfolder.slug if shared_file.subfolder else None,
        shared_file.id, size, request=request
    )

    logger.info(f"Single-part upload confirmed: {shared_file.filename} by {current_user.email}")

    return {"status": "completed", "file_id": shared_file.id, "size_bytes": size}


# =============================================================================
# Download Endpoint
# =============================================================================

@router.post("/download/{file_id}", response_model=DownloadResponse)
def get_download_url(
    file_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a presigned download URL for a file."""
    s3 = get_s3_service()
    if not s3.is_configured:
        raise HTTPException(status_code=503, detail="File storage service not configured")

    shared_file = db.query(SharedFile).filter(SharedFile.id == file_id).first()
    if not shared_file:
        raise HTTPException(status_code=404, detail="File not found")

    if shared_file.upload_status != 'completed':
        raise HTTPException(status_code=400, detail="File upload not completed")

    folder = shared_file.folder
    if not check_download_permission(db, current_user, folder):
        raise HTTPException(status_code=403, detail="No download permission for this folder")

    # Generate presigned URL
    download_url = s3.generate_download_url(shared_file.s3_key, shared_file.filename)

    # Increment download count
    shared_file.download_count += 1
    db.commit()

    # Log the download
    log_fileshare_action(
        db, current_user, 'download',
        shared_file.filename, folder.slug,
        shared_file.subfolder.slug if shared_file.subfolder else None,
        shared_file.id, shared_file.size_bytes, request=request
    )

    logger.debug(f"Download URL generated: {shared_file.filename} for {current_user.email}")

    return DownloadResponse(
        download_url=download_url,
        expires_in=s3.download_url_expiry
    )


# =============================================================================
# Delete Endpoint
# =============================================================================

@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    file_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a file. Uploader or super can delete."""
    shared_file = db.query(SharedFile).filter(SharedFile.id == file_id).first()
    if not shared_file:
        raise HTTPException(status_code=404, detail="File not found")

    # Check permission: uploader or super
    if shared_file.uploaded_by_id != current_user.id and current_user.role != RoleEnum.super:
        raise HTTPException(status_code=403, detail="Only the uploader or super admin can delete this file")

    folder = shared_file.folder
    s3 = get_s3_service()

    # Delete from S3
    if s3.is_configured:
        s3.delete_object(shared_file.s3_key)

    # Update folder used_bytes
    if shared_file.upload_status == 'completed':
        folder.used_bytes = max(0, folder.used_bytes - shared_file.size_bytes)

    # Log before deleting
    log_fileshare_action(
        db, current_user, 'delete',
        shared_file.filename, folder.slug,
        shared_file.subfolder.slug if shared_file.subfolder else None,
        shared_file.id, shared_file.size_bytes, request=request
    )

    db.delete(shared_file)
    db.commit()

    logger.info(f"File deleted: {shared_file.filename} by {current_user.email}")
    return None


# =============================================================================
# Audit Log Endpoint (Super Only)
# =============================================================================

@router.get("/audit-logs", response_model=list[AuditLogResponse])
@require_role(RoleEnum.super)
def get_audit_logs(
    user_email: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    folder_slug: Optional[str] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Query fileshare audit logs (super only)."""
    query = db.query(FileshareAuditLog).order_by(FileshareAuditLog.created_at.desc())

    if user_email:
        query = query.filter(FileshareAuditLog.user_email.ilike(f"%{user_email}%"))
    if action:
        query = query.filter(FileshareAuditLog.action == action)
    if folder_slug:
        query = query.filter(FileshareAuditLog.folder_slug == folder_slug)
    if start_date:
        query = query.filter(FileshareAuditLog.created_at >= start_date)
    if end_date:
        query = query.filter(FileshareAuditLog.created_at <= end_date)

    logs = query.offset(offset).limit(limit).all()

    return [
        AuditLogResponse(
            id=log.id,
            user_email=log.user_email,
            action=log.action,
            filename=log.filename,
            folder_slug=log.folder_slug,
            subfolder_slug=log.subfolder_slug,
            file_size_bytes=log.file_size_bytes,
            ip_address=log.ip_address,
            created_at=log.created_at
        )
        for log in logs
    ]


# =============================================================================
# Storage Audit (Super Only)
# =============================================================================

class StorageAuditResponse(BaseModel):
    """Response model for storage audit."""
    total_s3_objects: int
    total_s3_bytes: int
    total_db_records: int
    orphaned_s3_files: list[dict]  # In S3 but not in DB
    missing_s3_files: list[dict]   # In DB but not in S3
    synced_count: int              # Files in both


@router.get("/admin/storage-audit", response_model=StorageAuditResponse)
@require_role(RoleEnum.super)
def audit_storage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Audit S3 storage vs database records (super only).

    Returns lists of:
    - Orphaned S3 files: exist in S3 but have no DB record
    - Missing S3 files: have DB record but file missing from S3
    """
    s3 = get_s3_service()
    if not s3.is_configured:
        raise HTTPException(status_code=503, detail="S3 not configured")

    # Get all S3 objects
    s3_objects = s3.list_all_objects(prefix="files/")
    s3_keys = {obj['key']: obj for obj in s3_objects}

    # Get all DB records
    db_files = db.query(SharedFile).all()
    db_keys = {f.s3_key: f for f in db_files}

    # Find orphaned S3 files (in S3, not in DB)
    orphaned_s3 = []
    for key, obj in s3_keys.items():
        if key not in db_keys:
            orphaned_s3.append({
                "s3_key": key,
                "size_bytes": obj['size'],
                "last_modified": obj['last_modified'].isoformat()
            })

    # Find missing S3 files (in DB, not in S3)
    missing_s3 = []
    for key, file_record in db_keys.items():
        if key not in s3_keys:
            missing_s3.append({
                "s3_key": key,
                "db_id": file_record.id,
                "filename": file_record.filename,
                "uploaded_by": file_record.uploaded_by.email if file_record.uploaded_by else "unknown"
            })

    synced_count = len(set(s3_keys.keys()) & set(db_keys.keys()))
    total_s3_bytes = sum(obj['size'] for obj in s3_objects)

    logger.info(f"Storage audit by {current_user.email}: {len(orphaned_s3)} orphaned, {len(missing_s3)} missing")

    return StorageAuditResponse(
        total_s3_objects=len(s3_objects),
        total_s3_bytes=total_s3_bytes,
        total_db_records=len(db_files),
        orphaned_s3_files=orphaned_s3,
        missing_s3_files=missing_s3,
        synced_count=synced_count
    )


@router.delete("/admin/storage-audit/orphaned/{s3_key:path}", status_code=status.HTTP_204_NO_CONTENT)
@require_role(RoleEnum.super)
def delete_orphaned_s3_file(
    s3_key: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an orphaned file from S3 (exists in S3 but not in DB)."""
    s3 = get_s3_service()

    # Verify it's actually orphaned (not in DB)
    existing = db.query(SharedFile).filter(SharedFile.s3_key == s3_key).first()
    if existing:
        raise HTTPException(status_code=400, detail="File exists in database - not orphaned")

    # Verify it exists in S3
    if not s3.object_exists(s3_key):
        raise HTTPException(status_code=404, detail="File not found in S3")

    s3.delete_object(s3_key)
    logger.warning(f"Orphaned S3 file deleted by {current_user.email}: {s3_key}")
    return None


@router.delete("/admin/storage-audit/missing/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_role(RoleEnum.super)
def delete_missing_db_record(
    file_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a DB record for a file that no longer exists in S3."""
    s3 = get_s3_service()

    file_record = db.query(SharedFile).filter(SharedFile.id == file_id).first()
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found")

    # Verify it's actually missing from S3
    if s3.object_exists(file_record.s3_key):
        raise HTTPException(status_code=400, detail="File exists in S3 - not a broken record")

    # Update folder used_bytes
    folder = file_record.folder
    if file_record.upload_status == 'completed':
        folder.used_bytes = max(0, folder.used_bytes - file_record.size_bytes)

    db.delete(file_record)
    db.commit()

    logger.warning(f"Missing file DB record deleted by {current_user.email}: {file_record.filename} (id={file_id})")
    return None


# =============================================================================
# Terms of Service Acceptance
# =============================================================================

FILESHARE_TERMS_OF_SERVICE = """
RUCKUS.Tools Fileshare Terms of Service

By using this file sharing service, you agree to:

1. ACCEPTABLE USE: You will only upload files that you have the legal right to share and that comply with all applicable laws.

2. PROHIBITED CONTENT: You will NOT upload:
   - Illegal content of any kind
   - Malware, viruses, or malicious code
   - Content that infringes on intellectual property rights
   - Confidential information you are not authorized to share
   - Inappropriate or offensive material

3. SECURITY: You understand that certain file types are blocked for security reasons (executables, scripts, etc.).

4. MONITORING: All uploads and downloads are logged for audit and security purposes.

5. EXPIRATION: Files automatically expire after 30 days unless otherwise specified.

6. REPORTING: Users may report files they believe violate these terms. Reported files will be reviewed and may be removed.

7. LIABILITY: You assume full responsibility for the content you upload. The service administrators are not liable for user-uploaded content.

8. COOPERATION: You agree to cooperate with any legal requests related to content you have uploaded.

Violation of these terms may result in removal of access and potential legal action.
"""


@router.post("/terms/accept", response_model=TermsAcceptanceResponse)
def accept_terms(
    acceptance: TermsAcceptanceRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Accept the Fileshare Terms of Service. Logged to audit trail."""
    if not acceptance.accepted:
        raise HTTPException(status_code=400, detail="You must accept the terms to continue")

    # Log acceptance to audit trail
    log = FileshareAuditLog(
        user_id=current_user.id,
        user_email=current_user.email,
        action='terms_accepted',
        filename='FILESHARE_TERMS_OF_SERVICE',
        folder_slug='_system',
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get('user-agent', '')[:500]
    )
    db.add(log)
    db.commit()

    logger.info(f"Terms accepted by {current_user.email}")

    return TermsAcceptanceResponse(
        message="Terms of Service accepted successfully",
        accepted_at=log.created_at
    )


@router.get("/terms")
def get_terms():
    """Get the current Terms of Service text."""
    return {"terms": FILESHARE_TERMS_OF_SERVICE}


# =============================================================================
# Report File Endpoint
# =============================================================================

@router.post("/report", response_model=ReportFileResponse)
def report_file(
    report: ReportFileRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Report a file for review. Notifies super admins and relevant company admins."""
    # Get the file
    shared_file = db.query(SharedFile).filter(SharedFile.id == report.file_id).first()
    if not shared_file:
        raise HTTPException(status_code=404, detail="File not found")

    folder = shared_file.folder

    # Check user has at least download access to report
    if not check_download_permission(db, current_user, folder):
        raise HTTPException(status_code=403, detail="No access to this file")

    # Log the report to audit trail
    log = FileshareAuditLog(
        user_id=current_user.id,
        user_email=current_user.email,
        action='report',
        file_id=shared_file.id,
        filename=shared_file.filename,
        folder_slug=folder.slug,
        subfolder_slug=shared_file.subfolder.slug if shared_file.subfolder else None,
        file_size_bytes=shared_file.size_bytes,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get('user-agent', '')[:500]
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # Collect notification recipients
    notify_emails = []

    # Get all super admins
    super_admins = db.query(User).filter(User.role == RoleEnum.super).all()
    notify_emails.extend([u.email for u in super_admins])

    # If folder belongs to a company, get admins of that company
    if folder.company_id:
        company_admins = db.query(User).filter(
            User.company_id == folder.company_id,
            User.role.in_([RoleEnum.admin, RoleEnum.super])
        ).all()
        notify_emails.extend([u.email for u in company_admins])

    # Deduplicate
    notify_emails = list(set(notify_emails))

    # Send notifications
    if notify_emails:
        send_report_notification(
            to_emails=notify_emails,
            reporter_email=current_user.email,
            filename=shared_file.filename,
            folder_name=folder.name,
            reason=report.reason,
            file_id=shared_file.id
        )

    logger.warning(f"File reported: {shared_file.filename} by {current_user.email}. Reason: {report.reason}")

    return ReportFileResponse(
        message="File reported successfully. Administrators have been notified.",
        report_id=log.id
    )
