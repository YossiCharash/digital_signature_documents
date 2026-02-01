"""Cleanup service for removing old documents from S3."""

from datetime import UTC, datetime, timedelta

from botocore.exceptions import ClientError

from app.config import settings
from app.services.storage_service import StorageService
from app.utils.logger import logger


class CleanupService:
    """Service for cleaning up old documents from S3 storage."""

    def __init__(self, storage_service: StorageService):
        self.storage_service = storage_service
        self.retention_days = settings.s3_cleanup_retention_days

    def cleanup_old_documents(self) -> dict:
        """
        Delete all documents from S3 that are older than retention_days.

        Returns:
            dict with cleanup statistics
        """
        if not self.storage_service.enabled:
            logger.info("S3 storage is disabled, skipping cleanup")
            return {
                "status": "skipped",
                "reason": "S3 storage is disabled",
                "deleted_count": 0,
                "errors": 0,
            }

        try:
            cutoff_date = datetime.now(UTC) - timedelta(days=self.retention_days)
            logger.info(
                f"Starting cleanup of documents older than {self.retention_days} days (before {cutoff_date.isoformat()})"
            )

            deleted_count = 0
            errors = 0
            total_scanned = 0

            # List all objects in the bucket
            paginator = self.storage_service.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.storage_service.bucket_name)

            for page in pages:
                if "Contents" not in page:
                    continue

                for obj in page["Contents"]:
                    total_scanned += 1
                    key = obj["Key"]
                    last_modified = obj["LastModified"]

                    # Check if object is older than retention period
                    if last_modified.replace(tzinfo=UTC) < cutoff_date:
                        try:
                            # Delete the object
                            self.storage_service.s3_client.delete_object(
                                Bucket=self.storage_service.bucket_name,
                                Key=key,
                            )
                            deleted_count += 1
                            logger.info(
                                f"Deleted old document: {key} (created: {last_modified.isoformat()})"
                            )
                        except ClientError as e:
                            errors += 1
                            logger.error(f"Failed to delete {key}: {e}")

            result = {
                "status": "completed",
                "deleted_count": deleted_count,
                "total_scanned": total_scanned,
                "errors": errors,
                "cutoff_date": cutoff_date.isoformat(),
            }
            logger.info(
                f"Cleanup completed: {deleted_count} documents deleted, "
                f"{total_scanned} scanned, {errors} errors"
            )
            return result

        except ClientError as e:
            logger.error(f"Failed to list objects in S3 bucket: {e}")
            return {
                "status": "error",
                "error": str(e),
                "deleted_count": 0,
                "errors": 1,
            }
        except Exception as e:
            logger.error(f"Unexpected error during cleanup: {e}")
            return {
                "status": "error",
                "error": str(e),
                "deleted_count": 0,
                "errors": 1,
            }
