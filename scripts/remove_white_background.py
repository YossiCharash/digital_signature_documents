"""Remove white background from signature stamp image and make it transparent."""

import sys
from pathlib import Path

from PIL import Image


def remove_white_background(
    input_path: str, output_path: str | None = None, threshold: int = 240
) -> None:
    """
    Remove white background from an image and make it transparent.

    Args:
        input_path: Path to input image file
        output_path: Path to save output image (if None, overwrites input)
        threshold: RGB threshold for white detection (0-255, default 240)
                   Pixels with R, G, B all above this value will be made transparent
    """
    # Load the image
    img = Image.open(input_path)

    # Convert to RGBA if not already (needed for transparency)
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Get pixel data as a list of tuples (R, G, B, A)
    pixels = list(img.getdata())

    # Create new image data with transparency
    new_pixels = []
    for pixel in pixels:
        r, g, b, a = pixel
        # If pixel is white (or near-white), make it transparent
        # Check if R, G, B are all above threshold
        if r >= threshold and g >= threshold and b >= threshold:
            # Make transparent
            new_pixels.append((255, 255, 255, 0))
        else:
            # Keep original pixel
            new_pixels.append(pixel)

    # Update image data
    img.putdata(new_pixels)

    # Determine output path
    if output_path is None:
        output_path = input_path
    else:
        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Save the image with transparency
    img.save(output_path, "PNG")
    print(f"Successfully removed white background from {input_path}")
    print(f"  Saved to: {output_path}")


def main():
    """Main function to run the script."""
    if len(sys.argv) < 2:
        print(
            "Usage: python scripts/remove_white_background.py <input_image> [output_image] [threshold]"
        )
        print("\nExample:")
        print("  python scripts/remove_white_background.py assets/signature_stamp.png")
        print(
            "  python scripts/remove_white_background.py assets/signature_stamp.png assets/signature_stamp_transparent.png"
        )
        print(
            "  python scripts/remove_white_background.py assets/signature_stamp.png assets/signature_stamp_transparent.png 230"
        )
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    threshold = int(sys.argv[3]) if len(sys.argv) > 3 else 240

    if not Path(input_path).exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)

    try:
        remove_white_background(input_path, output_path, threshold)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
