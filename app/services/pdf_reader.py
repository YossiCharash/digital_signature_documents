"""PDF reading service for text-based PDFs only."""

import io
from pathlib import Path

import pdfplumber
from PyPDF2 import PdfReader

from app.utils.logger import logger


class PDFReadError(Exception):
    """Raised when PDF reading fails."""

    pass


class ImageOnlyPDFError(Exception):
    """Raised when PDF appears to be image-only (scanned)."""

    pass


class PDFReader:
    """Service for reading text from text-based PDF files only.

    This service only supports PDFs with embedded text. Scanned/image-only
    PDFs will be rejected with a clear error message.
    """

    # Minimum text threshold to consider PDF as text-based
    MIN_TEXT_LENGTH = 50

    def read_text(self, pdf_path: Path) -> str:
        """Extract text from text-based PDF file.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Extracted text content

        Raises:
            PDFReadError: If PDF reading fails
            ImageOnlyPDFError: If PDF appears to be image-only (scanned)
        """
        try:
            logger.info(f"Reading text-based PDF: {pdf_path}")

            # Extract text using pdfplumber
            text = self._extract_text(pdf_path)

            # Validate that we have sufficient text (not image-only)
            if not text or len(text.strip()) < self.MIN_TEXT_LENGTH:
                logger.warning(
                    f"PDF appears to be image-only: extracted only {len(text.strip()) if text else 0} characters"
                )
                raise ImageOnlyPDFError(
                    "This PDF appears to be image-only (scanned). "
                    "Only text-based PDFs with embedded text are supported. "
                    "Please provide a PDF with selectable text content."
                )

            logger.info(f"Extracted {len(text)} characters from text-based PDF")
            return text

        except ImageOnlyPDFError:
            raise
        except Exception as e:
            logger.error(f"Failed to read PDF: {e}")
            raise PDFReadError(f"PDF reading failed: {e}") from e

    def read_bytes(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes.

        Args:
            pdf_bytes: PDF file content as bytes

        Returns:
            Extracted text content

        Raises:
            PDFReadError: If PDF reading fails
            ImageOnlyPDFError: If PDF appears to be image-only (scanned)
        """
        try:
            logger.info("Reading text-based PDF from bytes")

            # Extract text using pdfplumber
            text = self._extract_text_from_bytes(pdf_bytes)

            # Validate that we have sufficient text (not image-only)
            if not text or len(text.strip()) < self.MIN_TEXT_LENGTH:
                logger.warning(
                    f"PDF appears to be image-only: extracted only {len(text.strip()) if text else 0} characters"
                )
                raise ImageOnlyPDFError(
                    "This PDF appears to be image-only (scanned). "
                    "Only text-based PDFs with embedded text are supported. "
                    "Please provide a PDF with selectable text content."
                )

            logger.info(f"Extracted {len(text)} characters from text-based PDF bytes")
            return text

        except ImageOnlyPDFError:
            raise
        except Exception as e:
            logger.error(f"Failed to read PDF bytes: {e}")
            raise PDFReadError(f"PDF reading failed: {e}") from e

    def _extract_text(self, pdf_path: Path) -> str:
        """Extract text using pdfplumber with PyPDF2 fallback."""
        try:
            text_parts = []
            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                        logger.debug(f"Extracted {len(page_text)} characters from page {page_num}")

            if not text_parts:
                logger.warning("No text extracted from PDF using pdfplumber, trying PyPDF2")
                return self._extract_text_pypdf2(pdf_path)

            return "\n".join(text_parts)

        except Exception as e:
            logger.warning(f"pdfplumber extraction failed: {e}, trying PyPDF2")
            return self._extract_text_pypdf2(pdf_path)

    def _extract_text_pypdf2(self, pdf_path: Path) -> str:
        """Extract text using PyPDF2 as fallback."""
        try:
            text_parts = []
            with open(pdf_path, "rb") as f:
                reader = PdfReader(f)
                for page_num, page in enumerate(reader.pages, 1):
                    text = page.extract_text()
                    if text:
                        text_parts.append(text)
                        logger.debug(f"Extracted {len(text)} characters from page {page_num} using PyPDF2")

            return "\n".join(text_parts)

        except Exception as e:
            logger.error(f"PyPDF2 extraction failed: {e}")
            return ""

    def _extract_text_from_bytes(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes using pdfplumber with PyPDF2 fallback."""
        try:
            text_parts = []
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                        logger.debug(f"Extracted {len(page_text)} characters from page {page_num}")

            if not text_parts:
                logger.warning("No text extracted from PDF bytes using pdfplumber, trying PyPDF2")
                return self._extract_text_pypdf2_from_bytes(pdf_bytes)

            return "\n".join(text_parts)

        except Exception as e:
            logger.warning(f"pdfplumber extraction from bytes failed: {e}, trying PyPDF2")
            return self._extract_text_pypdf2_from_bytes(pdf_bytes)

    def _extract_text_pypdf2_from_bytes(self, pdf_bytes: bytes) -> str:
        """Extract text from PDF bytes using PyPDF2 as fallback."""
        try:
            text_parts = []
            reader = PdfReader(io.BytesIO(pdf_bytes))
            for page_num, page in enumerate(reader.pages, 1):
                text = page.extract_text()
                if text:
                    text_parts.append(text)
                    logger.debug(f"Extracted {len(text)} characters from page {page_num} using PyPDF2")

            return "\n".join(text_parts)

        except Exception as e:
            logger.error(f"PyPDF2 extraction from bytes failed: {e}")
            return ""
