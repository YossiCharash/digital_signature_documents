"""Find the position of the small stamp on the reference PDF.

This script helps locate the small yellow-orange padlock stamp
and suggests appropriate size for the signature image.
"""

import sys
from io import BytesIO
from pathlib import Path

try:
    import fitz  # PyMuPDF
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Error: Required packages not installed.")
    print("Install with: pip install PyMuPDF Pillow")
    sys.exit(1)


def find_small_stamp_position(
    reference_pdf_path: str,
    output_image_path: str | None = None,
    page_number: int = 0,
    grid_spacing: int = 10,  # Smaller spacing for precise measurement
) -> dict:
    """
    Create a detailed grid overlay to find the small stamp position.

    Args:
        reference_pdf_path: Path to PDF with stamps
        output_image_path: Path to save grid image
        page_number: Page to analyze (0-indexed)
        grid_spacing: Grid spacing in points (smaller = more precise)

    Returns:
        Dictionary with suggested position and size
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
    zoom = 3.0  # Higher zoom for better precision
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    # Convert to PIL Image
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

    # Draw fine grid lines
    grid_color = (255, 0, 0, 100)  # Red with transparency
    text_color = (255, 0, 0, 255)  # Red

    # Vertical lines (X coordinates) - every 10 points
    x = 0
    label_count = 0
    while x <= page_width_pts:
        img_x = int(x * scale_x)
        draw.line([(img_x, 0), (img_x, img_height)], fill=grid_color, width=1)
        # Add X coordinate label every 50 points to avoid clutter
        if label_count % 5 == 0:
            try:
                font = ImageFont.truetype("arial.ttf", 12)
            except OSError:
                font = ImageFont.load_default()
            draw.text((img_x + 2, 2), f"{x:.0f}", fill=text_color, font=font)
        x += grid_spacing
        label_count += 1

    # Horizontal lines (Y coordinates from bottom) - every 10 points
    y = 0
    label_count = 0
    while y <= page_height_pts:
        # Y is measured from bottom, so invert
        img_y = int(img_height - (y * scale_y))
        draw.line([(0, img_y), (img_width, img_y)], fill=grid_color, width=1)
        # Add Y coordinate label every 50 points
        if label_count % 5 == 0:
            try:
                font = ImageFont.truetype("arial.ttf", 12)
            except OSError:
                font = ImageFont.load_default()
            draw.text((2, img_y + 2), f"{y:.0f}", fill=text_color, font=font)
        y += grid_spacing
        label_count += 1

    # Highlight top-left area where small stamp typically is
    # Draw a yellow rectangle in the top-left quadrant as a guide
    highlight_color = (255, 255, 0, 50)  # Yellow with transparency
    top_left_rect = fitz.Rect(0, page_rect.height * 0.7, page_rect.width * 0.5, page_rect.height)
    img_x0 = int(top_left_rect.x0 * scale_x)
    img_y0 = int(img_height - (top_left_rect.y1 * scale_y))
    img_x1 = int(top_left_rect.x1 * scale_x)
    img_y1 = int(img_height - (top_left_rect.y0 * scale_y))
    draw.rectangle([(img_x0, img_y0), (img_x1, img_y1)], fill=highlight_color, outline=(255, 255, 0, 255), width=2)

    # Add corner markers
    corner_size = 15
    # Bottom-left corner (origin)
    draw.rectangle([(0, img_height - corner_size), (corner_size, img_height)],
                  fill=(0, 255, 0, 200), outline=(0, 255, 0, 255), width=2)
    try:
        font = ImageFont.truetype("arial.ttf", 10)
    except OSError:
        font = ImageFont.load_default()
    draw.text((2, img_height - 12), "0,0", fill=(0, 255, 0, 255), font=font)

    # Add instructions
    instructions = [
        "מציאת מיקום החותמת הקטנה - Small Stamp Position Finder",
        f"Page: {page_number + 1}",
        f"Page size: {page_width_pts:.1f} x {page_height_pts:.1f} points",
        "",
        "Instructions (הוראות):",
        "1. Find the small yellow-orange padlock stamp",
        "2. Note its position (X from left, Y from bottom)",
        "3. Measure its size (width and height)",
        "",
        "החלמה הקטנה נמצאת בדרך כלל:",
        "- בפינה השמאלית-עליונה",
        "- מעל טבלת הנתונים",
        "- גודל משוער: 40-60 נקודות",
        "",
        f"Grid spacing: {grid_spacing} points",
    ]

    y_pos = img_height - 280
    for i, line in enumerate(instructions):
        try:
            font = ImageFont.truetype("arial.ttf", 11)
        except OSError:
            font = ImageFont.load_default()
        draw.text((10, y_pos + i * 18), line, fill=(0, 0, 255, 255), font=font)

    # Save image
    if output_image_path:
        grid_img.save(output_image_path)
        print(f"\nGrid overlay saved to: {output_image_path}")
    else:
        output_path = f"small_stamp_grid_page{page_number + 1}.png"
        grid_img.save(output_path)
        print(f"\nGrid overlay saved to: {output_path}")

    pdf_doc.close()

    # Suggest typical values for small stamp
    print(f"\n{'='*60}")
    print("Suggested configuration for small stamp:")
    print(f"{'='*60}")
    print("Based on typical small stamp location (top-left area):")
    print("")
    print("Typical position range:")
    print("  X: 20-100 points (from left edge)")
    print(f"  Y: {page_height_pts - 150:.0f}-{page_height_pts - 50:.0f} points (from bottom)")
    print("")
    print("Typical size for small stamp:")
    print("  Width: 40-60 points")
    print("  Height: 40-60 points")
    print("")
    print("Example .env configuration:")
    print("  SIGNATURE_POSITION_X=50.0")
    print(f"  SIGNATURE_POSITION_Y={page_height_pts - 100:.0f}")
    print("  SIGNATURE_WIDTH=50.0")
    print("  SIGNATURE_HEIGHT=50.0")
    print(f"{'='*60}")
    print("\nNext steps:")
    print("1. Open the grid image file")
    print("2. Find the small yellow-orange stamp")
    print("3. Read the exact X and Y coordinates")
    print("4. Measure the stamp size")
    print("5. Update your .env file with these values")
    print(f"{'='*60}")

    return {
        "page_width": page_width_pts,
        "page_height": page_height_pts,
        "suggested_x_range": (20, 100),
        "suggested_y_range": (page_height_pts - 150, page_height_pts - 50),
        "suggested_size_range": (40, 60),
    }


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/find_small_stamp_position.py <reference_pdf> [page_number] [output_image]")
        print("\nExample:")
        print("  python scripts/find_small_stamp_position.py reference.pdf 0 small_stamp_grid.png")
        sys.exit(1)

    pdf_path = sys.argv[1]
    page_num = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    output_path = sys.argv[3] if len(sys.argv) > 3 else None

    try:
        find_small_stamp_position(pdf_path, output_path, page_num)
    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
