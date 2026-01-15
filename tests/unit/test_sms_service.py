"""Unit tests for SMS service."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.sms_service import SMSDeliveryError, SMSService


class TestSMSService:
    """Test cases for SMSService."""

    def test_init(self):
        """Test SMSService initialization."""
        service = SMSService(
            api_url="https://api.test.com",
            api_key="test_key",
        )
        assert service.api_url == "https://api.test.com"
        assert service.api_key == "test_key"

    @patch("app.services.sms_service.httpx.AsyncClient")
    async def test_send_via_api_success(self, mock_client):
        """Test successful SMS send via API."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(
            return_value=mock_response
        )

        service = SMSService(
            api_url="https://api.test.com",
            api_key="test_key",
        )

        result = await service._send_via_api("0501234567", "INV-001", "invoice-id", None)

        assert result is True

    def test_normalize_phone_israeli(self):
        """Test Israeli phone number normalization."""
        service = SMSService()
        assert service._normalize_phone("0501234567") == "972501234567"
        assert service._normalize_phone("972501234567") == "972501234567"

    async def test_send_invoice_reference_missing_config(self):
        """Test sending SMS with missing configuration."""
        service = SMSService(api_url=None)

        with pytest.raises(SMSDeliveryError, match="SMS API URL"):
            await service.send_invoice_reference("0501234567", "INV-001", "invoice-id")
