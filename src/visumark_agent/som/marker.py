"""Set-of-Mark (SoM) visual annotation — draw labeled bounding boxes on page screenshots."""

from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from visumark_agent.som.extractor import PageElement

_DEFAULT_FONT: FreeTypeFont | None = None


def _get_font(size: int = 14) -> FreeTypeFont:
    """Load a reasonable font; falls back to PIL default."""
    global _DEFAULT_FONT
    if _DEFAULT_FONT is not None and _DEFAULT_FONT.size == size:
        return _DEFAULT_FONT

    import sys
    if sys.platform == "win32":
        candidates = ["arial.ttf", "segoeui.ttf", "consola.ttf"]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]

    for name in candidates:
        try:
            _font = ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
        else:
            return _font

    return ImageFont.load_default()


class SoMMarker:
    """Draws labeled rectangular overlays on a screenshot for each interactive element."""

    COLORS = [
        "#FF0000", "#00AA00", "#0066FF", "#CC6600", "#9900CC",
        "#FF6699", "#009999", "#FF6600", "#3366CC", "#99CC00",
    ]

    def __init__(
        self,
        font_size: int = 14,
        border_color: str = "#FF0000",
        border_width: int = 2,
        show_labels: bool = True,
    ):
        self.font_size = font_size
        self.border_color = border_color
        self.border_width = border_width
        self.show_labels = show_labels

    def annotate(
        self,
        screenshot: bytes,
        elements: list[PageElement],
        viewport_w: int = 1280,
        viewport_h: int = 720,
    ) -> bytes:
        """Draw SoM overlays and return an annotated PNG as bytes."""
        image = Image.open(BytesIO(screenshot)).convert("RGBA")
        draw = ImageDraw.Draw(image)
        font = _get_font(self.font_size)

        for elem in elements:
            color = self.COLORS[(elem.id - 1) % len(self.COLORS)]
            x, y, w, h = elem.bbox
            px, py = int(x * viewport_w), int(y * viewport_h)
            pw, ph = int(w * viewport_w), int(h * viewport_h)

            # bounding box
            draw.rectangle(
                [px, py, px + pw, py + ph],
                outline=color,
                width=self.border_width,
            )

            if not self.show_labels:
                continue

            label = str(elem.id)
            bbox = draw.textbbox((0, 0), label, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

            # label background
            label_x, label_y = px, max(0, py - th - 2)
            draw.rectangle(
                [label_x, label_y, label_x + tw + 4, label_y + th + 2],
                fill=color,
            )
            draw.text((label_x + 2, label_y + 1), label, fill="#FFFFFF", font=font)

        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
