"""Extract signature position from reference PDF.

This script helps you determine the exact position where the signature
stamp should be placed by comparing a reference PDF with the signature image.
"""

import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
    from PIL import Image
except ImportError:
    print("Error: Required packages not installed.")
    print("Install with: pip install PyMuPDF Pillow")
    sys.exit(1)


def extract_signature_position(
    reference_pdf_path: str,
    signature_image_path: str,
    page_number: int = 0,
) -> dict:
    """
    Extract signature position from reference PDF.

    Args:
        reference_pdf_path: Path to PDF that already has signature
        signature_image_path: Path to signature image file
        page_number: Page to analyze (0-indexed)

    Returns:
        Dictionary with position information
    """
    pdf_path = Path(reference_pdf_path)
    sig_path = Path(signature_image_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {reference_pdf_path}")
    if not sig_path.exists():
        raise FileNotFoundError(f"Signature image not found: {signature_image_path}")

    # Open PDF
    pdf_doc = fitz.open(reference_pdf_path)

    if page_number >= len(pdf_doc):
        raise ValueError(f"Page {page_number} not found. PDF has {len(pdf_doc)} pages.")

    page = pdf_doc[page_number]
    page_rect = page.rect

    # Load signature image
    sig_img = Image.open(signature_image_path)

    print(f"\n{'='*60}")
    print(f"Reference PDF: {reference_pdf_path}")
    print(f"Signature Image: {signature_image_path}")
    print(f"Page: {page_number + 1} (0-indexed: {page_number})")
    print(f"{'='*60}")
    print("\nPage dimensions:")
    print(f"  Width:  {page_rect.width:.2f} points ({page_rect.width/72:.2f} inches)")
    print(f"  Height: {page_rect.height:.2f} points ({page_rect.height/72:.2f} inches)")
    print("\nSignature image dimensions:")
    print(f"  Width:  {sig_img.width} pixels")
    print(f"  Height: {sig_img.height} pixels")

    # Manual position entry (since automatic detection is complex)
    print(f"\n{'='*60}")
    print("To determine the exact position:")
    print("1. Open the reference PDF in a PDF viewer")
    print("2. Measure the signature position:")
    print("   - X: distance from LEFT edge of page (in points)")
    print("   - Y: distance from BOTTOM edge of page (in points)")
    print("3. Measure the signature size (if different from image):")
    print("   - Width: width of signature on page (in points)")
    print("   - Height: height of signature on page (in points)")
    print(f"{'='*60}")

    # Calculate default size (assuming 96 DPI image, 72 DPI PDF)
    default_width = sig_img.width * 72 / 96
    default_height = sig_img.height * 72 / 96

    print("\nSuggested configuration (based on image size):")
    print(f"SIGNATURE_IMAGE_PATH={signature_image_path}")
    print("SIGNATURE_POSITION_X=<measure_from_left>")
    print("SIGNATURE_POSITION_Y=<measure_from_bottom>")
    print(f"SIGNATURE_WIDTH={default_width:.1f}  # Optional, defaults to image size")
    print(f"SIGNATURE_HEIGHT={default_height:.1f}  # Optional, defaults to image size")
    print(f"SIGNATURE_PAGE={page_number}")

    pdf_doc.close()

    return {
        "page_width": page_rect.width,
        "page_height": page_rect.height,
        "signature_width_pixels": sig_img.width,
        "signature_height_pixels": sig_img.height,
        "suggested_width_points": default_width,
        "suggested_height_points": default_height,
        "page_number": page_number,
    }


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python scripts/extract_signature_position.py <reference_pdf> <signature_image> [page_number]")
        print("\nExample:")
        print("  python scripts/extract_signature_position.py reference.pdf assets/signature_stamp.png 0")
        sys.exit(1)

    pdf_path = sys.argv[1]
    signature_path = sys.argv[2]
    page_num = int(sys.argv[3]) if len(sys.argv) > 3 else 0

    try:
        extract_signature_position(pdf_path, signature_path, page_num)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
