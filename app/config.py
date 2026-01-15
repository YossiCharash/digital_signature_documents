"""Application configuration using Pydantic settings."""

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    app_name: str = Field(default="Invoice Digitalization Platform")
    app_version: str = Field(default="1.0.0")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # Security
    tls_enabled: bool = Field(default=True)
    tls_cert_path: Optional[str] = Field(default=None)
    tls_key_path: Optional[str] = Field(default=None)

    # Digital Signature
    signing_cert_path: str = Field(..., description="Path to X.509 certificate for signing")
    signing_key_path: str = Field(..., description="Path to private key for signing")
    signing_key_password: Optional[str] = Field(default=None)
    signing_algorithm: str = Field(default="SHA256")

    # Email Service
    email_provider: str = Field(default="smtp", description="smtp or api")
    smtp_host: Optional[str] = Field(default=None)
    smtp_port: int = Field(default=587)
    smtp_user: Optional[str] = Field(default=None)
    smtp_password: Optional[str] = Field(default=None)
    smtp_use_tls: bool = Field(default=True)
    smtp_from_email: str = Field(default="noreply@example.com")
    smtp_from_name: str = Field(default="Invoice Digitalization")
    email_api_url: Optional[str] = Field(default=None)
    email_api_key: Optional[str] = Field(default=None)

    # SMS Service
    sms_provider: str = Field(default="api")
    sms_api_url: str = Field(..., description="SMS API endpoint URL")
    sms_api_key: str = Field(..., description="SMS API key")
    sms_sender_name: str = Field(default="InvoiceSystem")

    # Storage
    upload_dir: str = Field(default="./uploads")
    temp_dir: str = Field(default="./temp")
    invoice_storage_dir: str = Field(default="./invoices")

    # Archival
    archival_enabled: bool = Field(default=True)
    archival_retention_years: int = Field(default=7)

    @field_validator("signing_algorithm")
    @classmethod
    def validate_signing_algorithm(cls, v: str) -> str:
        """Validate signing algorithm."""
        allowed = ["SHA256", "SHA384", "SHA512"]
        if v.upper() not in allowed:
            raise ValueError(f"Signing algorithm must be one of {allowed}")
        return v.upper()

    @field_validator("email_provider")
    @classmethod
    def validate_email_provider(cls, v: str) -> str:
        """Validate email provider."""
        if v.lower() not in ["smtp", "api"]:
            raise ValueError("Email provider must be 'smtp' or 'api'")
        return v.lower()

    def get_signing_cert_path(self) -> Path:
        """Get signing certificate path as Path object."""
        return Path(self.signing_cert_path)

    def get_signing_key_path(self) -> Path:
        """Get signing key path as Path object."""
        return Path(self.signing_key_path)

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        Path(self.upload_dir).mkdir(parents=True, exist_ok=True)
        Path(self.temp_dir).mkdir(parents=True, exist_ok=True)
        Path(self.invoice_storage_dir).mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = Settings()
