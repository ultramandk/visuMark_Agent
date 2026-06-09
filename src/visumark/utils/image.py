"""Image utility functions for base64 encoding/decoding and manipulation."""

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image


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
