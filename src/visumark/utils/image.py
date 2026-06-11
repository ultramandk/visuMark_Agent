"""Image utility functions for base64 encoding/decoding and manipulation."""

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw


def encode_base64(image_bytes: bytes) -> str:
    """Encode image bytes to a base64 string (for WebSocket/JSON transport)."""
    return base64.b64encode(image_bytes).decode("utf-8")


def decode_base64(b64_str: str) -> bytes:
    """Decode a base64 string back to image bytes."""
    return base64.b64decode(b64_str)


def bytes_to_image(image_bytes: bytes) -> Image.Image:
    """Convert PNG/JPEG bytes to a PIL Image."""
    return Image.open(BytesIO(image_bytes)).convert("RGBA")


def image_to_bytes(image: Image.Image, format: str = "PNG") -> bytes:
    """Convert a PIL Image to bytes."""
    buf = BytesIO()
    image.save(buf, format=format)
    return buf.getvalue()


def save_image(image_bytes: bytes, path: str | Path) -> None:
    """Save image bytes to a file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(image_bytes)


def resize_image(
    image_bytes: bytes,
    max_width: int = 1920,
    max_height: int = 1080,
) -> bytes:
    """Resize an image to fit within max dimensions while preserving aspect ratio."""
    image = Image.open(BytesIO(image_bytes))
    image.thumbnail((max_width, max_height), Image.LANCZOS)
    return image_to_bytes(image)


def is_blank_screenshot(
    image_bytes: bytes,
    variance_threshold: float = 40.0,
    sample_points: int = 200,
) -> bool:
    """Check if a screenshot is mostly blank/white — pure PIL, no numpy.

    Samples pixels across the image and computes variance. A blank white
    page has all pixels near 255 with very low variance. A page with real
    content has darker pixels and higher variance.

    Args:
        image_bytes: PNG or JPEG image bytes.
        variance_threshold: Standard deviation below which the image
            is considered blank. Lower = stricter.
        sample_points: Number of pixel samples (evenly spaced grid).

    Returns:
        True if the image appears to be blank/white.
    """
    try:
        image = Image.open(BytesIO(image_bytes)).convert("L")  # Grayscale
        w, h = image.size

        # Sample pixels on an evenly-spaced grid
        import math
        cols = int(math.sqrt(sample_points * w / max(h, 1)))
        rows = int(math.sqrt(sample_points * h / max(w, 1)))
        cols = max(2, min(cols, w))
        rows = max(2, min(rows, h))

        pixels = []
        for y in range(0, h, max(1, h // rows)):
            for x in range(0, w, max(1, w // cols)):
                pixels.append(image.getpixel((x, y)))
                if len(pixels) >= sample_points:
                    break
            if len(pixels) >= sample_points:
                break

        if not pixels:
            return True  # Can't sample → treat as blank

        mean = sum(pixels) / len(pixels)
        variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
        std = variance ** 0.5

        return std < variance_threshold
    except Exception:
        return False


def highlight_element(
    image_bytes: bytes,
    bbox: tuple[float, float, float, float],
    color: str = "#FF4444",
    width: int = 4,
) -> bytes:
    """Draw a highlighted border around an element on the image.

    Args:
        image_bytes: PNG/JPEG bytes.
        bbox: Normalized bounding box (x, y, w, h) in [0, 1].
        color: Border color.
        width: Border line width in pixels.

    Returns:
        Modified image bytes (PNG).
    """
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
        iw, ih = img.size
        x1 = int(bbox[0] * iw)
        y1 = int(bbox[1] * ih)
        x2 = int((bbox[0] + bbox[2]) * iw)
        y2 = int((bbox[1] + bbox[3]) * ih)

        # Clamp to image bounds
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(iw, x2)
        y2 = min(ih, y2)

        if x2 - x1 < 4 or y2 - y1 < 4:
            return image_bytes  # Too small to highlight

        draw = ImageDraw.Draw(img)
        # Draw thick border
        for offset in range(width):
            draw.rectangle(
                [x1 - offset, y1 - offset, x2 + offset, y2 + offset],
                outline=color,
            )
        return image_to_bytes(img, "PNG")
    except Exception:
        return image_bytes
