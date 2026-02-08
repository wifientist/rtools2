# Fileshare Feature Implementation Plan

## Overview

A secure file sharing system for rtools2 using AWS S3 with presigned URLs. Designed for sharing SmartZone backups, config files, Docker tars, and CSVs between authenticated users.

---

## 1. Permission Model

### Access Rules

| Action | Who Can Do It |
|--------|---------------|
| Create root folder | Super only |
| Create subfolder | Super only |
| Manage permissions | Super only |
| Download from company folder | Any user in that company (automatic) |
| Download from non-company folder | Users explicitly granted download permission |
| Upload to any folder | Users explicitly granted upload permission |
| Delete own files | File uploader |
| Delete any file | Super only |

### Permission Logic (Pseudocode)

```python
def can_download(user, folder):
    # Company folders: automatic access for company members
    if folder.company_id and user.company_id == folder.company_id:
        return True
    # Explicit permission check
    return FolderPermission.exists(folder=folder, user=user, type='download')

def can_upload(user, folder):
    # Always explicit - no automatic upload access
    return FolderPermission.exists(folder=folder, user=user, type='upload')
```

---

## 2. Database Schema

### New Models

```python
# api/models/fileshare.py

class FileFolder(Base):
    """Root-level folders (one per company + super-created special folders)"""
    __tablename__ = "file_folders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))  # "Ruckus Networks"
    slug: Mapped[str] = mapped_column(String(50), unique=True)  # "ruckus"
    description: Mapped[str | None] = mapped_column(Text)

    # If set, users in this company get automatic download access
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id"))

    quota_bytes: Mapped[int] = mapped_column(default=10 * 1024 * 1024 * 1024)  # 10GB
    used_bytes: Mapped[int] = mapped_column(default=0)

    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    company = relationship("Company")
    created_by = relationship("User")
    subfolders = relationship("FileSubfolder", back_populates="folder")
    permissions = relationship("FolderPermission", back_populates="folder")
    files = relationship("SharedFile", back_populates="folder")


class FileSubfolder(Base):
    """Subfolders for organization (inherit parent permissions)"""
    __tablename__ = "file_subfolders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("file_folders.id"))
    name: Mapped[str] = mapped_column(String(100))  # "vsz_backups"
    slug: Mapped[str] = mapped_column(String(50))  # "vsz_backups"

    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    folder = relationship("FileFolder", back_populates="subfolders")
    created_by = relationship("User")
    files = relationship("SharedFile", back_populates="subfolder")

    __table_args__ = (
        UniqueConstraint('folder_id', 'slug', name='uq_subfolder_slug'),
    )


class FolderPermission(Base):
    """Explicit permissions (upload always, download for non-company folders)"""
    __tablename__ = "folder_permissions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("file_folders.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    permission_type: Mapped[str] = mapped_column(String(20))  # 'download' | 'upload'

    granted_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

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

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    folder_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("file_folders.id"))
    subfolder_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("file_subfolders.id"))

    filename: Mapped[str] = mapped_column(String(255))  # Original filename
    s3_key: Mapped[str] = mapped_column(String(500))    # Full S3 object key
    size_bytes: Mapped[int]
    content_type: Mapped[str] = mapped_column(String(100))

    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    expires_at: Mapped[datetime]  # uploaded_at + 30 days

    download_count: Mapped[int] = mapped_column(default=0)

    # Relationships
    folder = relationship("FileFolder", back_populates="files")
    subfolder = relationship("FileSubfolder", back_populates="files")
    uploaded_by = relationship("User")


class FileshareAuditLog(Base):
    """Permanent audit log for fileshare operations (super visibility)"""
    __tablename__ = "fileshare_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Who performed the action
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    user_email: Mapped[str] = mapped_column(String(255))  # Denormalized for log permanence

    # What action was performed
    action: Mapped[str] = mapped_column(String(50))  # 'upload', 'download', 'delete', 'bulk_download'

    # Target file info (denormalized - file may be deleted later)
    file_id: Mapped[uuid.UUID | None]
    filename: Mapped[str] = mapped_column(String(255))
    folder_slug: Mapped[str] = mapped_column(String(50))
    subfolder_slug: Mapped[str | None] = mapped_column(String(50))
    file_size_bytes: Mapped[int | None]

    # For bulk downloads, store list of file IDs
    bulk_file_ids: Mapped[list | None] = mapped_column(JSON)

    # Metadata
    ip_address: Mapped[str | None] = mapped_column(String(45))  # IPv6 max length
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Relationships
    user = relationship("User")
```

### Migration

```python
# api/alembic/versions/xxx_add_fileshare_tables.py

def upgrade():
    op.create_table('file_folders', ...)
    op.create_table('file_subfolders', ...)
    op.create_table('folder_permissions', ...)
    op.create_table('shared_files', ...)
    op.create_table('fileshare_audit_logs', ...)

    # Index for permission lookups
    op.create_index('ix_folder_perm_user', 'folder_permissions', ['user_id', 'permission_type'])
    op.create_index('ix_shared_files_folder', 'shared_files', ['folder_id', 'subfolder_id'])
    op.create_index('ix_shared_files_expires', 'shared_files', ['expires_at'])

    # Audit log indexes for query performance
    op.create_index('ix_audit_created_at', 'fileshare_audit_logs', ['created_at'])
    op.create_index('ix_audit_user_email', 'fileshare_audit_logs', ['user_email'])
    op.create_index('ix_audit_action', 'fileshare_audit_logs', ['action'])
```

---

## 3. AWS Setup

### S3 Bucket Configuration

```bash
# Bucket name (must be globally unique)
BUCKET_NAME=rtools-fileshare-prod

# Create bucket
aws s3api create-bucket \
    --bucket $BUCKET_NAME \
    --region us-west-2 \
    --create-bucket-configuration LocationConstraint=us-west-2

# Block ALL public access
aws s3api put-public-access-block \
    --bucket $BUCKET_NAME \
    --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

### S3 Lifecycle Rule (30-day auto-delete)

```json
{
    "Rules": [
        {
            "ID": "AutoDeleteAfter30Days",
            "Status": "Enabled",
            "Filter": {
                "Prefix": "files/"
            },
            "Expiration": {
                "Days": 30
            }
        }
    ]
}
```

```bash
aws s3api put-bucket-lifecycle-configuration \
    --bucket $BUCKET_NAME \
    --lifecycle-configuration file://lifecycle.json
```

### CORS Configuration (for browser uploads)

```json
{
    "CORSRules": [
        {
            "AllowedOrigins": ["https://rtools.ruckusnetworks.com", "http://localhost:5173"],
            "AllowedMethods": ["GET", "PUT", "POST"],
            "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag"],
            "MaxAgeSeconds": 3600
        }
    ]
}
```

### IAM Policy for rtools Backend

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowPresignedURLOperations",
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject",
                "s3:DeleteObject",
                "s3:ListBucket"
            ],
            "Resource": [
                "arn:aws:s3:::rtools-fileshare-prod",
                "arn:aws:s3:::rtools-fileshare-prod/*"
            ]
        },
        {
            "Sid": "AllowMultipartUpload",
            "Effect": "Allow",
            "Action": [
                "s3:CreateMultipartUpload",
                "s3:UploadPart",
                "s3:CompleteMultipartUpload",
                "s3:AbortMultipartUpload",
                "s3:ListMultipartUploadParts"
            ],
            "Resource": "arn:aws:s3:::rtools-fileshare-prod/*"
        }
    ]
}
```

### Environment Variables (.env)

```bash
# AWS S3 Configuration
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-west-2
S3_FILESHARE_BUCKET=rtools-fileshare-prod

# Presigned URL expiry (seconds)
S3_PRESIGNED_URL_EXPIRY=900  # 15 minutes
```

---

## 4. S3 Key Structure

```
rtools-fileshare-prod/
└── files/
    └── {folder_slug}/
        └── {subfolder_slug}/          # optional
            └── {file_uuid}/
                └── {original_filename}

# Examples:
files/ruckus/vsz_backups/a1b2c3d4.../backup-2024-01-15.tar.gz
files/ruckus/configs/e5f6g7h8.../szconfig.xml
files/comcast/migrations/i9j0k1l2.../migration-export.csv
```

The UUID prefix prevents filename collisions and provides unpredictability.

### File Versioning Behavior

When a user uploads a file with the same name as an existing file:
- **Keep both files** - each upload gets a unique UUID, so no collision occurs
- The UI displays both files with their upload timestamps
- Users can see multiple versions: `backup.tar.gz (uploaded Jan 15)`, `backup.tar.gz (uploaded Jan 20)`
- Old versions still auto-expire after 30 days per lifecycle rule

---

## 5. Backend API Endpoints

### New Router: `api/routers/fileshare.py`

```python
router = APIRouter(prefix="/api/fileshare", tags=["fileshare"])
```

### Folder Management (Super Only)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/folders` | List all folders (with permission info for current user) |
| POST | `/folders` | Create root folder |
| PATCH | `/folders/{folder_id}` | Update folder (name, quota) |
| DELETE | `/folders/{folder_id}` | Delete folder (must be empty) |
| POST | `/folders/{folder_id}/subfolders` | Create subfolder |
| DELETE | `/subfolders/{subfolder_id}` | Delete subfolder (must be empty) |

### Permission Management (Super Only)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/folders/{folder_id}/permissions` | List all permissions for folder |
| POST | `/folders/{folder_id}/permissions` | Grant permission to user |
| DELETE | `/permissions/{permission_id}` | Revoke permission |

### File Operations

| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/folders/{folder_id}/files` | List files in folder/subfolder | Download access |
| POST | `/upload/initiate` | Get presigned URL(s) for upload | Upload access |
| POST | `/upload/complete` | Complete multipart upload | Upload access |
| POST | `/download/{file_id}` | Get presigned download URL | Download access |
| POST | `/download/bulk` | Download multiple files as ZIP | Download access |
| DELETE | `/files/{file_id}` | Delete file | Uploader or Super |

### Audit Log (Super Only)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/audit-logs` | List audit logs with filtering (user, action, date range) |

### Upload Flow (Multipart for files > 100MB)

```
1. Client: POST /upload/initiate
   Body: { folder_id, subfolder_id?, filename, size_bytes, content_type }

2. Server: Creates SharedFile record, determines single vs multipart
   Response:
   - Single: { file_id, upload_url, method: "PUT" }
   - Multipart: { file_id, upload_id, parts: [{ part_number, upload_url }] }

3. Client: Uploads directly to S3 using presigned URL(s)

4. Client: POST /upload/complete (multipart only)
   Body: { file_id, upload_id, parts: [{ part_number, etag }] }

5. Server: Completes multipart upload, updates file record
```

### Download Flow

```
1. Client: POST /download/{file_id}

2. Server: Checks permission, generates presigned GET URL
   Response: { download_url, expires_in: 900 }

3. Client: Redirects browser to download_url (or fetches directly)
```

### Bulk Download Flow (ZIP)

```
1. Client: POST /download/bulk
   Body: { file_ids: [uuid, uuid, ...] }

2. Server:
   - Validates all files exist and user has download access to all
   - Streams files from S3, zips on-the-fly, returns as response
   - Logs bulk_download action with all file_ids

3. Response: StreamingResponse with Content-Disposition: attachment; filename="fileshare-download.zip"
```

Note: For very large bulk downloads (>500MB total), consider async job + presigned URL approach instead.

---

## 6. Backend Implementation Details

### S3 Service Class

```python
# api/services/s3_service.py

import boto3
from botocore.config import Config

class S3Service:
    MULTIPART_THRESHOLD = 100 * 1024 * 1024  # 100MB
    PART_SIZE = 50 * 1024 * 1024  # 50MB parts

    def __init__(self):
        self.s3 = boto3.client(
            's3',
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            config=Config(signature_version='s3v4')
        )
        self.bucket = settings.S3_FILESHARE_BUCKET

    def generate_upload_url(self, key: str, content_type: str, expires_in: int = 900) -> str:
        """Generate presigned PUT URL for single-part upload"""
        return self.s3.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': self.bucket,
                'Key': key,
                'ContentType': content_type
            },
            ExpiresIn=expires_in
        )

    def create_multipart_upload(self, key: str, content_type: str) -> str:
        """Initiate multipart upload, return upload_id"""
        response = self.s3.create_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            ContentType=content_type
        )
        return response['UploadId']

    def generate_part_upload_urls(
        self, key: str, upload_id: str, num_parts: int, expires_in: int = 3600
    ) -> list[dict]:
        """Generate presigned URLs for each part"""
        urls = []
        for part_number in range(1, num_parts + 1):
            url = self.s3.generate_presigned_url(
                'upload_part',
                Params={
                    'Bucket': self.bucket,
                    'Key': key,
                    'UploadId': upload_id,
                    'PartNumber': part_number
                },
                ExpiresIn=expires_in
            )
            urls.append({'part_number': part_number, 'upload_url': url})
        return urls

    def complete_multipart_upload(
        self, key: str, upload_id: str, parts: list[dict]
    ) -> None:
        """Complete multipart upload"""
        self.s3.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                'Parts': [
                    {'PartNumber': p['part_number'], 'ETag': p['etag']}
                    for p in sorted(parts, key=lambda x: x['part_number'])
                ]
            }
        )

    def generate_download_url(self, key: str, filename: str, expires_in: int = 900) -> str:
        """Generate presigned GET URL"""
        return self.s3.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': self.bucket,
                'Key': key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"'
            },
            ExpiresIn=expires_in
        )

    def delete_object(self, key: str) -> None:
        """Delete object from S3"""
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def get_folder_size(self, prefix: str) -> int:
        """Calculate total size of objects under prefix"""
        total = 0
        paginator = self.s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                total += obj['Size']
        return total

    def stream_object(self, key: str) -> Iterator[bytes]:
        """Stream object content for zip generation"""
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        return response['Body'].iter_chunks(chunk_size=1024 * 1024)  # 1MB chunks
```

### Bulk Download (Streaming ZIP)

```python
# api/routers/fileshare.py

import zipfile
from io import BytesIO
from fastapi.responses import StreamingResponse

@router.post("/download/bulk")
async def bulk_download(
    request: BulkDownloadRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    client_request: Request = None
):
    """Download multiple files as a ZIP archive"""
    files = await get_files_by_ids(db, request.file_ids)

    # Verify download permission for all files
    folders_checked = set()
    for file in files:
        if file.folder_id not in folders_checked:
            if not await check_download_permission(db, current_user, file.folder):
                raise HTTPException(403, f"No access to folder {file.folder.name}")
            folders_checked.add(file.folder_id)

    # Log the bulk download
    await log_fileshare_action(
        db, current_user, 'bulk_download',
        filename=f"{len(files)} files",
        folder_slug="multiple",
        bulk_file_ids=[str(f.id) for f in files],
        request=client_request
    )

    async def generate_zip():
        """Stream ZIP file generation"""
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            s3 = S3Service()
            for file in files:
                # Build path in zip: folder/subfolder/filename
                zip_path = f"{file.folder.slug}/"
                if file.subfolder:
                    zip_path += f"{file.subfolder.slug}/"
                zip_path += file.filename

                # Stream from S3 and add to zip
                obj = s3.s3.get_object(Bucket=s3.bucket, Key=file.s3_key)
                zf.writestr(zip_path, obj['Body'].read())

        buffer.seek(0)
        yield buffer.read()

    return StreamingResponse(
        generate_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=fileshare-download.zip"}
    )
```

### Permission Checking

```python
# api/services/fileshare_permissions.py

async def check_download_permission(
    db: AsyncSession, user: User, folder: FileFolder
) -> bool:
    """Check if user can download from folder"""
    # Super can access everything
    if user.role == 'super':
        return True

    # Company folders: automatic access for company members
    if folder.company_id and user.company_id == folder.company_id:
        return True

    # Check explicit permission
    result = await db.execute(
        select(FolderPermission).where(
            FolderPermission.folder_id == folder.id,
            FolderPermission.user_id == user.id,
            FolderPermission.permission_type == 'download'
        )
    )
    return result.scalar_one_or_none() is not None


async def check_upload_permission(
    db: AsyncSession, user: User, folder: FileFolder
) -> bool:
    """Check if user can upload to folder"""
    # Super can upload everywhere
    if user.role == 'super':
        return True

    # Check explicit permission (no automatic upload access)
    result = await db.execute(
        select(FolderPermission).where(
            FolderPermission.folder_id == folder.id,
            FolderPermission.user_id == user.id,
            FolderPermission.permission_type == 'upload'
        )
    )
    return result.scalar_one_or_none() is not None
```

### Audit Logging

```python
# api/services/fileshare_audit.py

async def log_fileshare_action(
    db: AsyncSession,
    user: User,
    action: str,  # 'upload', 'download', 'delete', 'bulk_download'
    filename: str,
    folder_slug: str,
    subfolder_slug: str | None = None,
    file_id: uuid.UUID | None = None,
    file_size_bytes: int | None = None,
    bulk_file_ids: list[str] | None = None,
    request: Request | None = None
):
    """Log a fileshare action to the permanent audit log"""
    log_entry = FileshareAuditLog(
        user_id=user.id,
        user_email=user.email,  # Denormalized for permanence
        action=action,
        file_id=file_id,
        filename=filename,
        folder_slug=folder_slug,
        subfolder_slug=subfolder_slug,
        file_size_bytes=file_size_bytes,
        bulk_file_ids=bulk_file_ids,
        ip_address=request.client.host if request else None,
        user_agent=request.headers.get("user-agent", "")[:500] if request else None
    )
    db.add(log_entry)
    await db.commit()


# Usage in endpoints:
# After successful upload:
await log_fileshare_action(db, user, 'upload', file.filename, folder.slug, ...)

# After generating download URL:
await log_fileshare_action(db, user, 'download', file.filename, folder.slug, ...)

# After file deletion:
await log_fileshare_action(db, user, 'delete', file.filename, folder.slug, ...)
```

### Audit Log Query Endpoint (Super Only)

```python
@router.get("/audit-logs")
@require_role("super")
async def get_audit_logs(
    db: AsyncSession = Depends(get_db),
    user_email: str | None = None,
    action: str | None = None,
    folder_slug: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 100,
    offset: int = 0
):
    """Query fileshare audit logs with filtering"""
    query = select(FileshareAuditLog).order_by(FileshareAuditLog.created_at.desc())

    if user_email:
        query = query.where(FileshareAuditLog.user_email.ilike(f"%{user_email}%"))
    if action:
        query = query.where(FileshareAuditLog.action == action)
    if folder_slug:
        query = query.where(FileshareAuditLog.folder_slug == folder_slug)
    if start_date:
        query = query.where(FileshareAuditLog.created_at >= start_date)
    if end_date:
        query = query.where(FileshareAuditLog.created_at <= end_date)

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    return result.scalars().all()
```

---

## 7. Frontend Components

### New Route Structure

```typescript
// src/App.tsx
<Route path="/fileshare" element={<FilesharePage />} />
<Route path="/fileshare/:folderSlug" element={<FolderView />} />
<Route path="/fileshare/:folderSlug/:subfolderSlug" element={<FolderView />} />

// Super-only admin page
<Route path="/admin/fileshare" element={<FileshareAdminPage />} />
```

### Components

```
src/pages/
└── Fileshare/
    ├── FilesharePage.tsx        # Folder list view
    ├── FolderView.tsx           # File browser for a folder
    ├── FileUpload.tsx           # Upload component with progress
    └── FileshareAdmin.tsx       # Super-only: manage folders & permissions

src/components/fileshare/
├── FileList.tsx                 # Table of files with download/delete actions
├── FolderCard.tsx               # Folder display card
├── UploadProgress.tsx           # Multipart upload progress indicator
├── PermissionManager.tsx        # Grant/revoke permissions UI
└── QuotaIndicator.tsx           # Visual quota usage display
```

### Upload Component (Multipart Support)

```typescript
// src/pages/Fileshare/FileUpload.tsx

const CHUNK_SIZE = 50 * 1024 * 1024; // 50MB

async function uploadFile(file: File, folderId: string, subfolderId?: string) {
  // 1. Initiate upload
  const initResponse = await api.post('/fileshare/upload/initiate', {
    folder_id: folderId,
    subfolder_id: subfolderId,
    filename: file.name,
    size_bytes: file.size,
    content_type: file.type || 'application/octet-stream'
  });

  if (initResponse.method === 'PUT') {
    // Single-part upload
    await fetch(initResponse.upload_url, {
      method: 'PUT',
      body: file,
      headers: { 'Content-Type': file.type }
    });
  } else {
    // Multipart upload
    const parts: { part_number: number; etag: string }[] = [];

    for (const part of initResponse.parts) {
      const start = (part.part_number - 1) * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, file.size);
      const chunk = file.slice(start, end);

      const uploadResponse = await fetch(part.upload_url, {
        method: 'PUT',
        body: chunk
      });

      parts.push({
        part_number: part.part_number,
        etag: uploadResponse.headers.get('ETag')!
      });

      // Update progress
      setProgress((part.part_number / initResponse.parts.length) * 100);
    }

    // Complete multipart upload
    await api.post('/fileshare/upload/complete', {
      file_id: initResponse.file_id,
      upload_id: initResponse.upload_id,
      parts
    });
  }
}
```

---

## 8. Security Considerations

### Presigned URL Security

- **Short expiry**: 15 minutes for download URLs, 1 hour for upload parts
- **Backend gates all URL generation**: Must pass auth + permission checks
- **No direct S3 access**: Bucket blocks all public access
- **Audit trail**: Log all presigned URL generations

### Content Safety

- **File type validation**: Check content-type header matches extension
- **Max file size enforced**: Backend rejects initiate requests > quota remaining
- **Filename sanitization**: Strip path traversal attempts, limit length

### Rate Limiting

```python
# Add to existing rate limiter
FILESHARE_LIMITS = {
    "upload_initiate": "10/minute",
    "download_url": "60/minute",
}
```

---

## 9. Scheduled Tasks

### Cleanup Expired File Records

S3 lifecycle handles actual object deletion, but we need to clean up DB records:

```python
# api/scheduler/fileshare_cleanup.py

async def cleanup_expired_files():
    """Remove SharedFile records for expired files (run daily)"""
    async with get_db_session() as db:
        result = await db.execute(
            delete(SharedFile).where(SharedFile.expires_at < datetime.utcnow())
        )
        logger.info(f"Cleaned up {result.rowcount} expired file records")
```

### Quota Recalculation

```python
async def recalculate_folder_quotas():
    """Sync used_bytes with actual S3 usage (run daily)"""
    s3 = S3Service()
    async with get_db_session() as db:
        folders = await db.execute(select(FileFolder))
        for folder in folders.scalars():
            prefix = f"files/{folder.slug}/"
            actual_size = s3.get_folder_size(prefix)
            folder.used_bytes = actual_size
        await db.commit()
```

---

## 10. Implementation Order

### Phase 1: Foundation
1. [ ] Set up AWS S3 bucket with lifecycle rules and CORS
2. [ ] Create IAM user/role with minimal permissions
3. [ ] Add boto3 to requirements.txt
4. [ ] Add AWS env vars to .env.example and docker-compose
5. [ ] Create database migration for new tables (including FileshareAuditLog)
6. [ ] Implement S3Service class

### Phase 2: Backend API - Core
7. [ ] Implement folder/subfolder CRUD endpoints (super only)
8. [ ] Implement permission management endpoints (super only)
9. [ ] Implement upload initiate/complete endpoints
10. [ ] Implement single-file download URL generation endpoint
11. [ ] Implement file deletion endpoint
12. [ ] Add permission checking middleware

### Phase 3: Backend API - Extended
13. [ ] Implement audit logging service (log all file operations)
14. [ ] Implement audit log query endpoint (super only)
15. [ ] Implement bulk download as ZIP endpoint

### Phase 4: Frontend
16. [ ] Create FilesharePage with folder list
17. [ ] Create FolderView with file browser (show multiple versions by timestamp)
18. [ ] Implement FileUpload component with multipart support + progress
19. [ ] Add multi-select for bulk download
20. [ ] Create FileshareAdmin page for super users (folders, permissions, audit logs)
21. [ ] Add Fileshare to navigation

### Phase 5: Polish
22. [ ] Implement quota enforcement and display
23. [ ] Add scheduled cleanup tasks
24. [ ] Write tests

---

## 11. Initial Folder Setup

After deployment, create initial folders:

```sql
-- Create company folders
INSERT INTO file_folders (id, name, slug, company_id, created_by_id)
SELECT gen_random_uuid(), c.name, lower(replace(c.name, ' ', '-')), c.id,
       (SELECT id FROM users WHERE role = 'super' LIMIT 1)
FROM companies c;

-- Create subfolders for each
INSERT INTO file_subfolders (id, folder_id, name, slug, created_by_id)
SELECT gen_random_uuid(), f.id, 'VSZ Backups', 'vsz-backups', f.created_by_id
FROM file_folders f;
-- Repeat for: configs, migrations, exports, etc.
```

---

## Design Decisions (Resolved)

| Question | Decision |
|----------|----------|
| Email notifications? | **No** - seeing folder contents is sufficient |
| Download tracking? | **Yes** - permanent audit log in database (FileshareAuditLog table) |
| File versioning? | **Yes** - keep all versions, each upload gets unique UUID |
| Bulk operations? | **Yes** - bulk download as streaming ZIP |
