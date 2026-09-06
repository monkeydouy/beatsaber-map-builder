"""Generated cover art.

Every package ships a cover, generated locally from the song's own metadata and
a seeded palette. Nothing is fetched from the network, so map generation never
depends on an external service being reachable.
"""

from __future__ import annotations

import colorsys
import hashlib
import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

COVER_SIZE = 512

#: Fonts to try, in order, before falling back to Pillow's bundled face.
FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Futura.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for candidate in FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


def _palette(seed_text: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Derive two neon accents deterministically from the song's identity."""
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    hue = digest[0] / 255.0
    complement = (hue + 0.42 + digest[1] / 255.0 * 0.16) % 1.0
    primary = colorsys.hsv_to_rgb(hue, 0.85, 1.0)
    secondary = colorsys.hsv_to_rgb(complement, 0.75, 1.0)
    to_rgb = lambda values: tuple(int(value * 255) for value in values)  # noqa: E731
    return to_rgb(primary), to_rgb(secondary)  # type: ignore[return-value]


def _fit_text(
    draw: ImageDraw.ImageDraw, text: str, max_width: int, start_size: int, min_size: int
) -> tuple[str, ImageFont.FreeTypeFont]:
    """Shrink, then truncate, until the text fits the available width."""
    size = start_size
    while size >= min_size:
        font = _load_font(size)
        if draw.textlength(text, font=font) <= max_width:
            return text, font
        size -= 4

    font = _load_font(min_size)
    truncated = text
    while truncated and draw.textlength(truncated + "…", font=font) > max_width:
        truncated = truncated[:-1]
    return (truncated + "…") if truncated else "", font


def generate_cover(
    destination: Path,
    *,
    title: str,
    artist: str,
    difficulty_label: str,
) -> Path:
    """Render a 512x512 cover and write it to `destination`."""
    primary, secondary = _palette(f"{title}|{artist}")

    image = Image.new("RGB", (COVER_SIZE, COVER_SIZE), (8, 9, 16))
    draw = ImageDraw.Draw(image)

    # Vertical gradient ground.
    for y in range(COVER_SIZE):
        ratio = y / COVER_SIZE
        draw.line(
            [(0, y), (COVER_SIZE, y)],
            fill=(
                int(8 + 22 * ratio + primary[0] * 0.05 * (1 - ratio)),
                int(9 + 14 * ratio + primary[1] * 0.05 * (1 - ratio)),
                int(16 + 34 * ratio + primary[2] * 0.07),
            ),
        )

    # Soft glow behind the artwork.
    glow = Image.new("RGB", (COVER_SIZE, COVER_SIZE), (0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-120, 200, 320, 640), fill=tuple(int(c * 0.55) for c in primary))
    glow_draw.ellipse((240, -80, 660, 340), fill=tuple(int(c * 0.40) for c in secondary))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    image = Image.blend(image, glow, 0.55)
    draw = ImageDraw.Draw(image)

    # A stylised waveform: original geometry, deterministic from the title.
    centre_y = 300
    seed = int(hashlib.sha256(title.encode("utf-8")).hexdigest()[:8], 16)
    bar_count = 46
    for index in range(bar_count):
        x = 40 + index * ((COVER_SIZE - 80) / bar_count)
        phase = (seed >> (index % 24)) & 0xFF
        height = 18 + abs(math.sin(index * 0.55 + phase * 0.012)) * 92
        blend = index / bar_count
        colour = tuple(
            int(primary[channel] * (1 - blend) + secondary[channel] * blend)
            for channel in range(3)
        )
        draw.rounded_rectangle(
            [x, centre_y - height / 2, x + 5, centre_y + height / 2],
            radius=3,
            fill=colour,
        )

    # Accent rule above the wordmark.
    draw.rounded_rectangle([40, 92, 148, 97], radius=3, fill=primary)

    brand_font = _load_font(26)
    draw.text((40, 46), "SABERMAPPER AI", font=brand_font, fill=(226, 232, 246))

    title_text, title_font = _fit_text(draw, title, COVER_SIZE - 80, 52, 24)
    draw.text((40, 386), title_text, font=title_font, fill=(255, 255, 255))

    artist_text, artist_font = _fit_text(draw, artist, COVER_SIZE - 80, 28, 16)
    draw.text((40, 386 + title_font.size + 10), artist_text, font=artist_font, fill=(150, 160, 185))

    # Difficulty chip, bottom right.
    chip_font = _load_font(22)
    label = difficulty_label.upper()
    chip_width = int(draw.textlength(label, font=chip_font)) + 32
    chip_box = [COVER_SIZE - 40 - chip_width, 44, COVER_SIZE - 40, 84]
    draw.rounded_rectangle(chip_box, radius=20, fill=primary)
    draw.text(
        (chip_box[0] + 16, 52),
        label,
        font=chip_font,
        fill=(10, 10, 18),
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)
    logger.info("cover_generated", extra={"path": destination.name})
    return destination
