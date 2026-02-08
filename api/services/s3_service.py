"""
S3 Service for Fileshare feature.

Handles all S3 operations including presigned URL generation for uploads/downloads,
multipart upload management, and file deletion.
"""
import os
import logging
import math
from typing import Optional
from datetime import datetime, timedelta

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3Service:
    """Service for S3 operations."""

    # Multipart upload thresholds
    MULTIPART_THRESHOLD = 100 * 1024 * 1024  # 100MB - use multipart for files larger than this
    PART_SIZE = 50 * 1024 * 1024  # 50MB parts

    def __init__(self):
        """Initialize S3 client with credentials from environment."""
        self.access_key = os.getenv("AWS_ACCESS_KEY_ID")
        self.secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.bucket = os.getenv("S3_FILESHARE_BUCKET")

        self.download_url_expiry = int(os.getenv("S3_DOWNLOAD_URL_EXPIRY", "900"))  # 15 min default
        self.upload_url_expiry = int(os.getenv("S3_UPLOAD_URL_EXPIRY", "3600"))  # 1 hour default

        if not all([self.access_key, self.secret_key, self.bucket]):
            logger.warning("S3 credentials not fully configured - fileshare will be unavailable")
            self._client = None
        else:
            self._client = boto3.client(
                's3',
                region_name=self.region,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                config=Config(signature_version='s3v4')
            )
            logger.info(f"S3 service initialized for bucket: {self.bucket}")

    @property
    def is_configured(self) -> bool:
        """Check if S3 is properly configured."""
        return self._client is not None

    def _ensure_configured(self):
        """Raise error if S3 is not configured."""
        if not self.is_configured:
            raise RuntimeError("S3 service is not configured. Check AWS credentials in environment.")

    def generate_s3_key(
        self,
        folder_slug: str,
        file_uuid: str,
        filename: str,
        subfolder_slug: Optional[str] = None
    ) -> str:
        """
        Generate S3 object key following our convention.

        Format: files/{folder_slug}/{subfolder_slug?}/{file_uuid}/{filename}
        """
        if subfolder_slug:
            return f"files/{folder_slug}/{subfolder_slug}/{file_uuid}/{filename}"
        return f"files/{folder_slug}/{file_uuid}/{filename}"

    def generate_upload_url(
        self,
        key: str,
        content_type: str,
        expires_in: Optional[int] = None
    ) -> str:
        """
        Generate presigned PUT URL for single-part upload.

        Args:
            key: S3 object key
            content_type: MIME type of the file
            expires_in: URL expiry in seconds (default from env)

        Returns:
            Presigned PUT URL
        """
        self._ensure_configured()
        expires_in = expires_in or self.upload_url_expiry

        url = self._client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': self.bucket,
                'Key': key,
                'ContentType': content_type
            },
            ExpiresIn=expires_in
        )
        logger.debug(f"Generated upload URL for key: {key}")
        return url

    def create_multipart_upload(self, key: str, content_type: str) -> str:
        """
        Initiate a multipart upload.

        Args:
            key: S3 object key
            content_type: MIME type of the file

        Returns:
            Upload ID for the multipart upload
        """
        self._ensure_configured()

        response = self._client.create_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            ContentType=content_type
        )
        upload_id = response['UploadId']
        logger.info(f"Created multipart upload for key: {key}, upload_id: {upload_id}")
        return upload_id

    def generate_part_upload_urls(
        self,
        key: str,
        upload_id: str,
        num_parts: int,
        expires_in: Optional[int] = None
    ) -> list[dict]:
        """
        Generate presigned URLs for each part of a multipart upload.

        Args:
            key: S3 object key
            upload_id: Multipart upload ID
            num_parts: Number of parts to generate URLs for
            expires_in: URL expiry in seconds

        Returns:
            List of dicts with part_number and upload_url
        """
        self._ensure_configured()
        expires_in = expires_in or self.upload_url_expiry

        urls = []
        for part_number in range(1, num_parts + 1):
            url = self._client.generate_presigned_url(
                'upload_part',
                Params={
                    'Bucket': self.bucket,
                    'Key': key,
                    'UploadId': upload_id,
                    'PartNumber': part_number
                },
                ExpiresIn=expires_in
            )
            urls.append({
                'part_number': part_number,
                'upload_url': url
            })

        logger.debug(f"Generated {num_parts} part upload URLs for key: {key}")
        return urls

    def complete_multipart_upload(
        self,
        key: str,
        upload_id: str,
        parts: list[dict]
    ) -> None:
        """
        Complete a multipart upload.

        Args:
            key: S3 object key
            upload_id: Multipart upload ID
            parts: List of dicts with part_number and etag
        """
        self._ensure_configured()

        self._client.complete_multipart_upload(
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
        logger.info(f"Completed multipart upload for key: {key}")

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """
        Abort a multipart upload.

        Args:
            key: S3 object key
            upload_id: Multipart upload ID
        """
        self._ensure_configured()

        try:
            self._client.abort_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id
            )
            logger.info(f"Aborted multipart upload for key: {key}")
        except ClientError as e:
            logger.warning(f"Failed to abort multipart upload: {e}")

    def generate_download_url(
        self,
        key: str,
        filename: str,
        expires_in: Optional[int] = None
    ) -> str:
        """
        Generate presigned GET URL for download.

        Args:
            key: S3 object key
            filename: Original filename for Content-Disposition header
            expires_in: URL expiry in seconds

        Returns:
            Presigned GET URL
        """
        self._ensure_configured()
        expires_in = expires_in or self.download_url_expiry

        url = self._client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': self.bucket,
                'Key': key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"'
            },
            ExpiresIn=expires_in
        )
        logger.debug(f"Generated download URL for key: {key}")
        return url

    def delete_object(self, key: str) -> bool:
        """
        Delete an object from S3.

        Args:
            key: S3 object key

        Returns:
            True if deleted successfully
        """
        self._ensure_configured()

        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
            logger.info(f"Deleted object: {key}")
            return True
        except ClientError as e:
            logger.error(f"Failed to delete object {key}: {e}")
            return False

    def get_object_size(self, key: str) -> Optional[int]:
        """
        Get the size of an object in S3.

        Args:
            key: S3 object key

        Returns:
            Size in bytes, or None if object doesn't exist
        """
        self._ensure_configured()

        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
            return response['ContentLength']
        except ClientError:
            return None

    def get_folder_size(self, prefix: str) -> int:
        """
        Calculate total size of objects under a prefix.

        Args:
            prefix: S3 key prefix (e.g., "files/ruckus/")

        Returns:
            Total size in bytes
        """
        self._ensure_configured()

        total = 0
        paginator = self._client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                total += obj['Size']
        return total

    def get_object_stream(self, key: str):
        """
        Get a streaming body for an S3 object.

        Args:
            key: S3 object key

        Returns:
            StreamingBody object for reading
        """
        self._ensure_configured()

        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response['Body']

    def calculate_parts(self, file_size: int) -> int:
        """
        Calculate number of parts needed for multipart upload.

        Args:
            file_size: Size of file in bytes

        Returns:
            Number of parts
        """
        return math.ceil(file_size / self.PART_SIZE)

    def should_use_multipart(self, file_size: int) -> bool:
        """
        Determine if multipart upload should be used.

        Args:
            file_size: Size of file in bytes

        Returns:
            True if file is larger than multipart threshold
        """
        return file_size > self.MULTIPART_THRESHOLD


# Singleton instance for use across the application
_s3_service: Optional[S3Service] = None


def get_s3_service() -> S3Service:
    """
    Get or create the S3 service singleton.

    Returns:
        S3Service instance
    """
    global _s3_service
    if _s3_service is None:
        _s3_service = S3Service()
    return _s3_service
