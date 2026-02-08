"""Application configuration."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Document Delivery"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8000

    # Email
    email_provider: str = "smtp"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from_email: str = "noreply@example.com"
    smtp_from_name: str = "Document Delivery"
    email_api_url: str | None = None
    email_api_key: str | None = None

    # SMS
    sms_provider: str = "api"
    sms_api_url: str | None = None
    sms_api_key: str | None = None
    sms_sender_name: str = "DocDelivery"

    # S3 (required for SMS download links)
    s3_enabled: bool = False
    s3_bucket_name: str | None = None
    s3_region: str = "us-east-1"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_endpoint_url: str | None = None
    s3_presigned_url_expiration: int = 3600
    s3_cleanup_retention_days: int = 7  # Days to keep documents before cleanup

    # Signing
    private_key_pem: str | None = None
    private_key_path: str | None = None

    # Signer identity (used in certificate Subject + PDF signature metadata)
    signer_name: str = "הכנס שם"
    signer_email: str = "user@example.com"
    signer_company: str = "My Company"

    # PDF signature metadata (Reason/Location as shown in PDF viewers)
    signature_reason: str = "Document authentication and integrity verification"
    signature_location: str = "Digital Signature Service"
    signature_contact: str | None = None

    # TSA (Trusted Timestamping Authority) - optional but recommended
    # Free TSA services: http://timestamp.digicert.com, http://timestamp.sectigo.com
    tsa_url: str | None = None  # TSA server URL (RFC 3161)
    tsa_username: str | None = None  # Optional: TSA username if authentication required
    tsa_password: str | None = None  # Optional: TSA password if authentication required
    tsa_add_doctimestamp: bool = True  # Add RFC3161 DocTimeStamp signature when TSA is enabled

    # Visual signature stamp
    signature_image_path: str = "assets/signature_stamp.png"
    signature_position_x: float = 50.0  # X coordinate in points (from left)
    signature_position_y: float = 50.0  # Y coordinate in points (from bottom)
    signature_width: float | None = None  # Width in points (None = use image width)
    signature_height: float | None = None  # Height in points (None = use image height)
    signature_page: int = 0  # Page number (0-indexed, -1 for all pages)

    @field_validator("email_provider")
    @classmethod
    def _email_provider(cls, v: str) -> str:
        if v.lower() not in ("smtp", "api"):
            raise ValueError("email_provider must be 'smtp' or 'api'")
        return v.lower()

    def ensure_directories(self) -> None:
        Path("uploads").mkdir(parents=True, exist_ok=True)
        Path("temp").mkdir(parents=True, exist_ok=True)


settings = Settings()
