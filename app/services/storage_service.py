"""Storage service for handling file uploads to S3."""

import boto3
from botocore.exceptions import ClientError

from app.config import settings
from app.utils.logger import logger


class StorageError(Exception):
    """Raised when storage operations fail."""

    pass


def _ascii_safe(val: str) -> str:
    """Ensure string is ASCII-only; S3 metadata accepts ASCII only."""
    return val.encode("ascii", "replace").decode("ascii")


class StorageService:
    """Service for managing file storage in S3."""

    def __init__(self):
        self.enabled = settings.s3_enabled
        if not self.enabled:
            logger.info("S3 storage is disabled, using local storage placeholders")
            return

        self.bucket_name = settings.s3_bucket_name
        kwargs = {
            "region_name": settings.s3_region,
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key,
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        self.s3_client = boto3.client("s3", **kwargs)

    def upload_file(
        self,
        content: bytes,
        filename: str,
        content_type: str = "application/json",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload file content to S3 with optional metadata."""
        if not self.enabled:
            logger.warning(f"S3 disabled, skipping upload for {filename}")
            return filename

        try:
            kwargs = {
                "Bucket": self.bucket_name,
                "Key": filename,
                "Body": content,
                "ContentType": content_type,
            }
            if metadata:
                kwargs["Metadata"] = {k: _ascii_safe(v) for k, v in metadata.items()}
            self.s3_client.put_object(**kwargs)
            logger.info(f"Successfully uploaded {filename} to S3 bucket {self.bucket_name}")
            return filename
        except ClientError as e:
            logger.error(f"Failed to upload {filename} to S3: {e}")
            raise StorageError(f"S3 upload failed: {e}")

    def download_file(self, filename: str) -> bytes:
        """Download file content from S3."""
        if not self.enabled:
            logger.warning(f"S3 disabled, cannot download {filename}")
            raise StorageError("S3 storage is disabled")

        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=filename)
            content: bytes = response["Body"].read()
            logger.info(f"Successfully downloaded {filename} from S3")
            return content
        except ClientError as e:
            logger.error(f"Failed to download {filename} from S3: {e}")
            raise StorageError(f"S3 download failed: {e}")

    def generate_presigned_url(self, filename: str, expiration: int | None = None) -> str:
        """Generate a pre-signed URL for a file in S3."""
        if not self.enabled:
            # Fallback for local development if S3 is disabled
            return f"http://localhost:8000/documents/download/{filename}"

        if expiration is None:
            expiration = settings.s3_presigned_url_expiration

        try:
            url: str = self.s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": filename},
                ExpiresIn=expiration,
            )
            return url
        except ClientError as e:
            logger.error(f"Failed to generate pre-signed URL for {filename}: {e}")
            raise StorageError(f"Failed to generate pre-signed URL: {e}")
