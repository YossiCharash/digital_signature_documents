"""Measure signature position from reference PDF with visual grid.

This script creates a visual grid overlay on the PDF page to help
you measure the exact position of the signature stamp.
"""

import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Error: Required packages not installed.")
    print("Install with: pip install PyMuPDF Pillow")
    sys.exit(1)


def create_grid_overlay(
    reference_pdf_path: str,
    output_image_path: str | None = None,
    page_number: int = 0,
    grid_spacing: int = 50,
) -> None:
    """
    Create a grid overlay image from PDF page to help measure signature position.

    Args:
        reference_pdf_path: Path to PDF with signature
        output_image_path: Path to save grid image (optional)
        page_number: Page to analyze (0-indexed)
        grid_spacing: Grid spacing in points
    """
    pdf_path = Path(reference_pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {reference_pdf_path}")

    # Open PDF
    pdf_doc = fitz.open(reference_pdf_path)

    if page_number >= len(pdf_doc):
        raise ValueError(f"Page {page_number} not found. PDF has {len(pdf_doc)} pages.")

    page = pdf_doc[page_number]
    page_rect = page.rect

    # Render page as high-resolution image
    zoom = 2.0  # 2x zoom for better visibility
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    # Convert to PIL Image
    from io import BytesIO

    img_data = pix.tobytes("png")
    page_img = Image.open(BytesIO(img_data))

    # Create a copy for drawing grid
    grid_img = page_img.copy()
    draw = ImageDraw.Draw(grid_img)

    # Calculate grid lines in image coordinates
    img_width, img_height = grid_img.size
    page_width_pts = page_rect.width
    page_height_pts = page_rect.height

    # Scale factor: image pixels to PDF points
    scale_x = img_width / page_width_pts
    scale_y = img_height / page_height_pts

    # Draw grid lines
    grid_color = (255, 0, 0, 128)  # Red with transparency
    text_color = (255, 0, 0, 255)  # Red

    # Vertical lines (X coordinates)
    x = 0
    while x <= page_width_pts:
        img_x = int(x * scale_x)
        draw.line([(img_x, 0), (img_x, img_height)], fill=grid_color, width=2)
        # Add X coordinate label at top
        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except OSError:
            font = ImageFont.load_default()
        draw.text((img_x + 5, 5), f"X={x:.0f}", fill=text_color, font=font)
        x += grid_spacing

    # Horizontal lines (Y coordinates from bottom)
    y = 0
    while y <= page_height_pts:
        # Y is measured from bottom, so invert
        img_y = int(img_height - (y * scale_y))
        draw.line([(0, img_y), (img_width, img_y)], fill=grid_color, width=2)
        # Add Y coordinate label on left
        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except OSError:
            font = ImageFont.load_default()
        draw.text((5, img_y + 5), f"Y={y:.0f}", fill=text_color, font=font)
        y += grid_spacing

    # Add corner markers
    corner_size = 20
    # Bottom-left corner (origin)
    draw.rectangle(
        [(0, img_height - corner_size), (corner_size, img_height)],
        fill=(0, 255, 0, 200),
        outline=(0, 255, 0, 255),
        width=3,
    )
    draw.text((5, img_height - 15), "0,0", fill=(0, 255, 0, 255), font=font)

    # Add instructions
    instructions = [
        "Grid Overlay for Signature Position Measurement",
        f"Page: {page_number + 1}",
        f"Page size: {page_width_pts:.1f} x {page_height_pts:.1f} points",
        "",
        "Instructions:",
        "1. Find the signature stamp on this image",
        "2. Note the X coordinate (red vertical line)",
        "3. Note the Y coordinate (red horizontal line)",
        "4. X is measured from LEFT edge",
        "5. Y is measured from BOTTOM edge",
        "",
        f"Grid spacing: {grid_spacing} points",
    ]

    y_pos = img_height - 200
    for i, line in enumerate(instructions):
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except OSError:
            font = ImageFont.load_default()
        draw.text((10, y_pos + i * 20), line, fill=(0, 0, 255, 255), font=font)

    # Save or show image
    if output_image_path:
        grid_img.save(output_image_path)
        print(f"\nGrid overlay saved to: {output_image_path}")
    else:
        output_path = f"signature_grid_page{page_number + 1}.png"
        grid_img.save(output_path)
        print(f"\nGrid overlay saved to: {output_path}")

    pdf_doc.close()

    print(f"\n{'=' * 60}")
    print("Next steps:")
    print("1. Open the grid image file")
    print("2. Find where the signature stamp is located")
    print("3. Read the X and Y coordinates from the grid lines")
    print("4. Update your .env file with these coordinates")
    print(f"{'=' * 60}")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print(
            "Usage: python scripts/measure_signature_position.py <reference_pdf> [page_number] [output_image]"
        )
        print("\nExample:")
        print("  python scripts/measure_signature_position.py reference.pdf 0 grid.png")
        sys.exit(1)

    pdf_path = sys.argv[1]
    page_num = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    try:
        create_grid_overlay(pdf_path, output_path, page_num)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
