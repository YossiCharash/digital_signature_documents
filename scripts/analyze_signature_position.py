"""Utility script to analyze reference PDF and extract signature position.

This script helps determine the exact position where the signature stamp
should be placed by analyzing a reference PDF that already has the signature.
"""

import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
    from PIL import Image
except ImportError:
    print("Error: PyMuPDF and Pillow are required. Install with: pip install PyMuPDF Pillow")
    sys.exit(1)


def find_signature_in_pdf(pdf_path: str, signature_image_path: str) -> None:
    """
    Analyze PDF to find where signature image appears.

    Args:
        pdf_path: Path to reference PDF with signature already placed
        signature_image_path: Path to signature image file
    """
    pdf_path_obj = Path(pdf_path)
    sig_path_obj = Path(signature_image_path)

    if not pdf_path_obj.exists():
        print(f"Error: PDF file not found: {pdf_path}")
        return

    if not sig_path_obj.exists():
        print(f"Error: Signature image not found: {signature_image_path}")
        return

    # Open PDF
    pdf_doc = fitz.open(pdf_path)

    # Load signature image for comparison
    sig_img = Image.open(signature_image_path)
    sig_width, sig_height = sig_img.size

    print(f"\nAnalyzing PDF: {pdf_path}")
    print(f"Signature image: {signature_image_path} ({sig_width}x{sig_height} pixels)")
    print(f"PDF has {len(pdf_doc)} page(s)\n")

    # For each page, try to find the signature
    for page_num in range(len(pdf_doc)):
        page = pdf_doc[page_num]
        page_rect = page.rect

        print(f"Page {page_num + 1}:")
        print(f"  Page size: {page_rect.width:.2f} x {page_rect.height:.2f} points")
        print("  (1 point = 1/72 inch)")

        # Get page as image for visual inspection
        # Note: This is a simplified approach - in practice, you'd need
        # image recognition to find the exact position

        # For now, we'll provide instructions
        print("\n  To find the exact position:")
        print("  1. Open the PDF in a PDF viewer")
        print("  2. Note the position of the signature stamp")
        print("  3. Use the following coordinates (in points, from bottom-left):")
        print("     - X: distance from left edge")
        print("     - Y: distance from bottom edge")
        print("  4. Set in .env:")
        print("     SIGNATURE_POSITION_X=<x_value>")
        print("     SIGNATURE_POSITION_Y=<y_value>")
        print("     SIGNATURE_WIDTH=<width_in_points> (optional, defaults to image size)")
        print("     SIGNATURE_HEIGHT=<height_in_points> (optional, defaults to image size)")
        print(f"     SIGNATURE_PAGE={page_num} (0-indexed, or -1 for all pages)")

    pdf_doc.close()

    print("\n" + "="*60)
    print("Example configuration for .env:")
    print("="*60)
    print("SIGNATURE_IMAGE_PATH=assets/signature_stamp.png")
    print("SIGNATURE_POSITION_X=50.0")
    print("SIGNATURE_POSITION_Y=50.0")
    print("SIGNATURE_PAGE=0")
    print("="*60)


def main():
    """Main entry point."""
    if len(sys.argv) < 3:
        print("Usage: python scripts/analyze_signature_position.py <reference_pdf> <signature_image>")
        print("\nExample:")
        print("  python scripts/analyze_signature_position.py reference.pdf assets/signature_stamp.png")
        sys.exit(1)

    pdf_path = sys.argv[1]
    signature_path = sys.argv[2]

    find_signature_in_pdf(pdf_path, signature_path)


if __name__ == "__main__":
    main()
