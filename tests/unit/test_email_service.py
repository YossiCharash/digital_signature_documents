"""Unit tests for email service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.email_service import EmailDeliveryError, EmailService


class TestEmailService:
    """Test cases for EmailService."""

    def test_init_smtp(self):
        """Test EmailService initialization with SMTP."""
        service = EmailService(provider="smtp", smtp_host="smtp.test.com")
        assert service.provider == "smtp"
        assert service.smtp_host == "smtp.test.com"

    def test_init_api(self):
        """Test EmailService initialization with API."""
        service = EmailService(provider="api", api_url="https://api.test.com")
        assert service.provider == "api"
        assert service.api_url == "https://api.test.com"

    @patch("app.services.email_service.smtplib.SMTP")
    async def test_send_via_smtp_success(self, mock_smtp):
        """Test successful SMTP email send."""
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server

        service = EmailService(
            provider="smtp",
            smtp_host="smtp.test.com",
            smtp_port=587,
            smtp_use_tls=True,
            smtp_from_email="test@example.com",
            smtp_from_name="Test",
        )

        invoice_data = {"invoice": {"invoice_number": "INV-001"}}
        result = await service._send_via_smtp(
            "recipient@example.com", invoice_data, "INV-001", None, None
        )

        assert result is True
        mock_server.starttls.assert_called_once()
        mock_server.send_message.assert_called_once()

    @patch("app.services.email_service.httpx.AsyncClient")
    async def test_send_via_api_success(self, mock_client):
        """Test successful API email send."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        service = EmailService(
            provider="api",
            api_url="https://api.test.com",
            api_key="test_key",
        )

        invoice_data = {"invoice": {"invoice_number": "INV-001"}}
        result = await service._send_via_api(
            "recipient@example.com", invoice_data, "INV-001", None, None
        )

        assert result is True

    async def test_send_invoice_missing_config(self):
        """Test sending email with missing configuration."""
        service = EmailService(provider="smtp", smtp_host=None)

        with pytest.raises(EmailDeliveryError, match="SMTP host"):
            await service.send_invoice("test@example.com", {}, "INV-001")
