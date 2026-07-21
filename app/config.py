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
    api_url: str = "http://localhost:8000/"
    app_name: str = "Document Delivery"
    app_version: str = "1.0.0"
    debug: bool = False
    log_level: str = "INFO"

    host: str = "0.0.0.0"
    port: int = 8000

    # Email
    email_provider: str = "smtp"  # smtp | ses | sendgrid | mailjet | api
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    # Sender identity. Has defaults so the app can start without configuration;
    # for SES this MUST be overridden with a verified SES identity.
    smtp_from_email: str = "noreply@example.com"
    smtp_from_name: str = "Document Delivery"
    email_api_url: str | None = None
    email_api_key: str | None = None

    # Amazon SES (used when email_provider="ses"). On AWS (App Runner/ECS/EC2)
    # leave the keys empty to use the instance IAM role; boto3 resolves
    # credentials automatically. The From address (smtp_from_email) must be a
    # verified SES identity in this region.
    ses_region: str | None = None  # falls back to s3_region, then AWS default
    ses_access_key: str | None = None
    ses_secret_key: str | None = None
    ses_configuration_set: str | None = None  # optional SES configuration set

    # SendGrid (used when email_provider="sendgrid"). Delivery goes over the
    # Web API v3 (HTTPS) rather than SMTP, so it also works on hosts that block
    # outbound SMTP ports. smtp_from_email must be a verified SendGrid sender
    # (Single Sender or an address on an authenticated domain).
    sendgrid_api_key: str | None = None
    sendgrid_api_url: str = "https://api.sendgrid.com/v3/mail/send"
    # When true SendGrid validates the request but never delivers – useful for
    # testing the integration without sending real mail.
    sendgrid_sandbox_mode: bool = False

    # Mailjet (used when email_provider="mailjet"). Both keys come from the same
    # row in the Mailjet dashboard under Account > API Key Management; they are
    # sent as HTTP Basic credentials. smtp_from_email must be an authorised
    # sender under Account > Sender domains & addresses.
    mailjet_api_key: str | None = None
    mailjet_secret_key: str | None = None
    mailjet_api_url: str = "https://api.mailjet.com/v3.1/send"
    # When true Mailjet validates the request but never delivers.
    mailjet_sandbox_mode: bool = False

    # SMS
    sms_provider: str = "api"
    sms_api_url: str | None = 'https://capi.inforu.co.il/api/v2/SMS/SendSms'
    sms_api_key: str | None = None
    sms_sender_name: str = "נוהלים"

    # S3 (required for SMS download links)
    s3_enabled: bool = False
    s3_bucket_name: str | None = None
    s3_region: str = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_endpoint_url: str | None = None
    s3_presigned_url_expiration: int = 3600
    s3_cleanup_retention_days: int = 7  # Days to keep documents before cleanup

    # Database (optional – required for the internal URL shortener)
    database_url: str | None = None

    # Delivery log retention: successful sends are purged after this many days;
    # failures are kept forever so they can always be investigated.
    delivery_log_success_retention_days: int = 2

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
        if v.lower() not in ("smtp", "api", "ses", "sendgrid", "mailjet"):
            raise ValueError(
                "email_provider must be 'smtp', 'ses', 'sendgrid', 'mailjet', or 'api'"
            )
        return v.lower()

    def ensure_directories(self) -> None:
        Path("uploads").mkdir(parents=True, exist_ok=True)
        Path("temp").mkdir(parents=True, exist_ok=True)


settings = Settings()
