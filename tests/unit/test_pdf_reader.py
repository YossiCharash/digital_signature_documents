"""Unit tests for PDF reader service."""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.pdf_reader import PDFReadError, PDFReader


class TestPDFReader:
    """Test cases for PDFReader."""

    def test_init_with_ocr_enabled(self):
        """Test PDFReader initialization with OCR enabled."""
        reader = PDFReader(ocr_enabled=True)
        assert reader.ocr_enabled is True

    def test_init_with_ocr_disabled(self):
        """Test PDFReader initialization with OCR disabled."""
        reader = PDFReader(ocr_enabled=False)
        assert reader.ocr_enabled is False

    @patch("app.services.pdf_reader.pdfplumber")
    def test_read_text_success(self, mock_pdfplumber):
        """Test successful text extraction."""
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Sample invoice text"
        mock_pdf.pages = [mock_page]
        mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf

        reader = PDFReader(ocr_enabled=False)
        text = reader.read_text(Path("test.pdf"))

        assert text == "Sample invoice text"
        mock_pdfplumber.open.assert_called_once()

    @patch("app.services.pdf_reader.pdfplumber")
    def test_read_text_empty(self, mock_pdfplumber):
        """Test text extraction with empty PDF."""
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_pdf.pages = [mock_page]
        mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf

        reader = PDFReader(ocr_enabled=False)
        with pytest.raises(PDFReadError, match="Insufficient text"):
            reader.read_text(Path("test.pdf"))

    @patch("app.services.pdf_reader.pdfplumber")
    def test_read_bytes_success(self, mock_pdfplumber):
        """Test successful text extraction from bytes."""
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Sample invoice text"
        mock_pdf.pages = [mock_page]
        mock_pdfplumber.open.return_value.__enter__.return_value = mock_pdf

        reader = PDFReader(ocr_enabled=False)
        pdf_bytes = b"fake pdf content"
        text = reader.read_bytes(pdf_bytes)

        assert text == "Sample invoice text"

    def test_read_bytes_empty(self):
        """Test reading empty PDF bytes."""
        reader = PDFReader(ocr_enabled=False)
        with pytest.raises(PDFReadError, match="Insufficient text"):
            reader.read_bytes(b"")
