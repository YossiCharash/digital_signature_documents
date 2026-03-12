"""Storage service for handling file uploads to S3."""
import base64

import boto3
import requests
from botocore.config import Config
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
        self.s3_client = boto3.client(
            "s3",
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4")
        )

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
        if expiration is None:
            expiration = settings.s3_presigned_url_expiration

        try:
            url: str = self.s3_client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket_name, "Key": filename, "ResponseContentDisposition": "inline"},
                ExpiresIn=expiration,
            )
            url = shorten_url(url)
            return url
        except ClientError as e:
            logger.error(f"Failed to generate pre-signed URL for {filename}: {e}")
            raise StorageError(f"Failed to generate pre-signed URL: {e}")


def shorten_url(url: str) -> str:
    api_url = "https://is.gd/create.php"
    params = {"url": url, "format": "simple"}
    try:
        response = requests.get(api_url, params=params, timeout=5)

        if response.status_code == 200:
            print(str(response))
            return response.text.strip()

    except requests.RequestException:
        pass

    return url


def encode_url(url: str) -> str:
    encoded = base64.urlsafe_b64encode(url.encode()).decode()
    return encoded.rstrip("=")


def decode_url(code: str) -> str:
    padding = '=' * (-len(code) % 4)
    decoded = base64.urlsafe_b64decode(code + padding).decode()
    return decoded


def create_short_link(original_url: str) -> str:
    """
    יוצר מזהה קצר ושומר את ה-URL המקורי
    """
    code = encode_url(original_url)
    short_link = f"http://localhost:8000/doc/{code}"
    return short_link
