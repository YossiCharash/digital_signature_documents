"""SMS document deliverer – uploads to S3 and sends download link via SMS."""

import mimetypes
from typing import Any

from app.delivery.abstractions import IDocumentDeliverer
from app.services.sms_service import SMSDeliveryError, SMSService
from app.services.storage_service import StorageError, StorageService
from app.utils.logger import logger


def _content_type_for(filename: str) -> str:
    ct, _ = mimetypes.guess_type(filename)
    return ct or "application/octet-stream"


class SMSDocumentDeliverer(IDocumentDeliverer):
    """Delivers document via SMS with S3 download link (open/closed)."""

    def __init__(
        self,
        storage_service: StorageService | None = None,
        sms_service: SMSService | None = None,
    ):
        self._storage = storage_service or StorageService()
        self._sms = sms_service or SMSService()

    async def deliver(
        self,
        document: bytes,
        filename: str,
        recipient: str,
        **kwargs: Any,
    ) -> bool:
        if not getattr(self._storage, "enabled", True):
            raise StorageError("S3 is disabled. SMS delivery requires S3 for download links.")

        message: str | None = kwargs.get("message")
        key = filename  # Use original filename as-is in S3
        content_type = _content_type_for(filename)

        try:
            self._storage.upload_file(document, key, content_type=content_type)
        except StorageError as e:
            logger.error(f"SMS deliverer: upload failed: {e}")
            raise

        try:
            url = self._storage.generate_presigned_url(key)
        except StorageError as e:
            logger.error(f"SMS deliverer: presigned URL failed: {e}")
            raise

        try:
            return await self._sms.send_document_link(
                to_phone=recipient,
                document_url=url,
                message=message,
            )
        except SMSDeliveryError as e:
            logger.error(f"SMS deliverer failed: {e}")
            raise
