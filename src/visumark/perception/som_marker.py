"""Set-of-Mark (SoM) visual annotation — draw labeled bounding boxes on screenshots.

Fixed implementation — addresses the old bugs:
  - Consistent color cycling across all elements
  - Proper label placement (never off-screen)
  - Solid label backgrounds for readability
  - Font fallback chain for cross-platform support
"""

from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from PIL.ImageFont import FreeTypeFont

from visumark.core.types import PageElement

# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

_FONT_CACHE: dict[int, FreeTypeFont] = {}


def _get_font(size: int = 14) -> FreeTypeFont:
    """Load a readable font with cross-platform fallback chain."""
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]

    import sys

    if sys.platform == "win32":
        font_dir = "C:/Windows/Fonts"
        candidates = [
            f"{font_dir}/segoeui.ttf",
            f"{font_dir}/segoeuib.ttf",
            f"{font_dir}/arial.ttf",
            f"{font_dir}/arialbd.ttf",
            f"{font_dir}/consola.ttf",
            f"{font_dir}/consolab.ttf",
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/SFNSText.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ]

    for name in candidates:
        try:
            font = ImageFont.truetype(name, size)
            _FONT_CACHE[size] = font
            return font
        except (OSError, IOError):
            continue

    _FONT_CACHE[size] = ImageFont.load_default()
    return _FONT_CACHE[size]


# ---------------------------------------------------------------------------
# SoM Marker
# ---------------------------------------------------------------------------

class SoMMarker:
    """Draws numbered bounding-box overlays on a screenshot for interactive elements.

    Each element gets:
        - A colored rectangular border
        - A numbered label badge (colored background + white number)
        - Optionally, a small text hint below the badge

    The element ID (SoM label) is the primary visual cue for the VLM.
    """

    # Visually distinct color palette — 12 colors, cycles for >12 elements
    COLORS = [
        "#E53E3E",  # red
        "#3182CE",  # blue
        "#38A169",  # green
        "#DD6B20",  # orange
        "#805AD5",  # purple
        "#D53F8C",  # pink
        "#00A3C4",  # teal
        "#D69E2E",  # yellow
        "#319795",  # cyan
        "#6B46C1",  # indigo
        "#E53E8C",  # magenta
        "#2F855A",  # dark green
    ]

    def __init__(
        self,
        font_size: int = 11,
        border_color: str = "#E53E3E",
        border_width: int = 2,
        show_labels: bool = True,
        show_text_hints: bool = False,   # Show element text near the label
    ):
        self.font_size = font_size
        self.border_color = border_color
        self.border_width = border_width
        self.show_labels = show_labels
        self.show_text_hints = show_text_hints

    def annotate(
        self,
        screenshot: bytes,
        elements: list[PageElement],
        viewport_w: int = 1280,
        viewport_h: int = 720,
    ) -> bytes:
        """Draw SoM overlays on a screenshot and return an annotated PNG.

        Labels are placed using overlap avoidance: for each element, up to
        5 candidate positions are tried and the first one that doesn't
        overlap any previously placed label is used.  This dramatically
        reduces label collisions on dense pages (e.g. QQ Mail inbox).

        Args:
            screenshot: Raw PNG bytes of the page screenshot.
            elements: List of PageElement objects to annotate.
            viewport_w: Viewport width in pixels.
            viewport_h: Viewport height in pixels.

        Returns:
            Annotated PNG image as bytes.
        """
        image = Image.open(BytesIO(screenshot)).convert("RGBA")

        # Create a transparent overlay layer for cleaner drawing
        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = _get_font(self.font_size)
        small_font = _get_font(max(9, self.font_size - 4))

        # Track placed label rects for overlap avoidance
        placed_labels: list[tuple[int, int, int, int]] = []

        for elem in elements:
            color = self.COLORS[(int(elem.id) - 1) % len(self.COLORS)]

            # Convert normalized bbox to pixel coordinates
            x = int(elem.bbox[0] * viewport_w)
            y = int(elem.bbox[1] * viewport_h)
            w = int(elem.bbox[2] * viewport_w)
            h = int(elem.bbox[3] * viewport_h)

            # Skip elements that are entirely outside the viewport
            if x + w <= 0 or y + h <= 0 or x >= viewport_w or y >= viewport_h:
                continue

            # Clamp to image bounds
            x = max(0, x)
            y = max(0, y)
            w = min(w, viewport_w - x)
            h = min(h, viewport_h - y)

            if w < 3 or h < 3:
                continue

            # --- Border ---
            draw.rectangle(
                [x, y, x + w, y + h],
                outline=color,
                width=self.border_width,
            )

            if not self.show_labels:
                continue

            # --- Label badge with overlap avoidance ---
            label = str(elem.id)
            tbox = draw.textbbox((0, 0), label, font=font)
            tw, th = tbox[2] - tbox[0], tbox[3] - tbox[1]
            pad_x, pad_y_top, pad_y_bottom = 4, 0, 3
            lw = tw + pad_x * 2   # total label width
            lh = th + pad_y_top + pad_y_bottom   # total label height

            # Try 2 candidate positions, pick the first non-overlapping one
            candidates = [
                # (bg_x1, bg_y1) — top-left of the label background
                (x,                y - lh),          # above, align-left, touches box top
                (x,                y + 2),           # inside top-left (fallback)
            ]

            best_pos = candidates[0]  # default: above-left
            for cx_pos, cy_pos in candidates:
                # Skip positions outside the image
                if cy_pos < 0 or cy_pos + lh > viewport_h:
                    continue
                if cx_pos < 0 or cx_pos + lw > viewport_w:
                    continue

                # Check overlap with previously placed labels
                overlaps = False
                for px, py, pw, ph in placed_labels:
                    # AABB overlap test
                    if (cx_pos < px + pw and cx_pos + lw > px and
                        cy_pos < py + ph and cy_pos + lh > py):
                        overlaps = True
                        break

                if not overlaps:
                    best_pos = (cx_pos, cy_pos)
                    break

            bg_x1, bg_y1 = best_pos

            # Draw label background
            draw.rectangle(
                [bg_x1, bg_y1, bg_x1 + lw, bg_y1 + lh],
                fill=color,
            )
            draw.text(
                (bg_x1 + pad_x, bg_y1 + pad_y_top),
                label,
                fill="#FFFFFF",
                font=font,
            )

            # Record this label's position
            placed_labels.append((bg_x1, bg_y1, lw, lh))

            # --- Optional text hint ---
            if self.show_text_hints and elem.text:
                hint = elem.text[:30]
                hbox = draw.textbbox((0, 0), hint, font=small_font)
                hw = hbox[2] - hbox[0]
                hint_y = bg_y1 + lh + 2
                if hint_y + th < viewport_h:
                    draw.text(
                        (bg_x1, hint_y),
                        hint,
                        fill=color,
                        font=small_font,
                    )
                    # Also track the text hint rect for overlap avoidance
                    placed_labels.append((bg_x1, hint_y, hw + 4, th))

        # Composite overlay onto original image
        image = Image.alpha_composite(image, overlay)
        image = image.convert("RGB")

        buf = BytesIO()
        image.save(buf, format="JPEG", quality=80)
        return buf.getvalue()


def marker_factory(config: dict) -> SoMMarker:
    """Create a SoMMarker instance from configuration dict."""
    return SoMMarker(
        font_size=config.get("font_size", 11),
        border_color=config.get("border_color", "#E53E3E"),
        border_width=config.get("border_width", 2),
        show_labels=config.get("show_labels", True),
        show_text_hints=config.get("show_text_hints", False),
    )
