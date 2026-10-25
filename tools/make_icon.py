"""Generate the application icon (static/app.ico) used by the packaged exe.

The mark is a rounded indigo tile holding three subtitle lines, each prefixed by
a coloured dot standing in for a different speaker. Everything is solid shapes —
no strokes — so the icon survives being scaled down to 16x16 in the taskbar.

Two constraints drove the colours, both verified by rendering the icon at real
pixel sizes against actual taskbar colours:

  * The tile stays indigo rather than a dark neutral. Dark neutral tiles look
    great on light taskbars but lose their outline entirely against Windows 11's
    default dark taskbar (#202020), leaving the bars floating with no icon
    behind them.
  * Indigo also separates cleanly from the Windows 10 accent-colour taskbar
    (#0078D7), which a brand-blue tile does not.

Kept in the repo (rather than only committing the .ico) so the artwork can be
tweaked without a graphics editor and without adding a Pillow build dependency.

Usage:
    python tools/make_icon.py            # writes static/app.ico
    python tools/make_icon.py out.ico    # writes to an explicit path
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

# Sizes embedded in the .ico. 256 is stored as 0 in the directory entry, which
# is what Windows expects; the rest fit in a byte as-is.
SIZES = (16, 32, 48, 64, 256)

# Supersampling factor used for anti-aliasing. Each output pixel averages
# SS*SS boolean samples, giving SS*SS+1 coverage levels.
SS = 4

BACKGROUND = (79, 70, 229)  # #4F46E5 indigo
FOREGROUND = (255, 255, 255)

# Geometry as fractions of the canvas edge.
TILE_MARGIN = 0.02
TILE_RADIUS = 0.22

BAR_THICKNESS = 0.13
DOT_RADIUS = 0.065
DOT_CENTRE_X = 0.22
BAR_START_X = 0.36

# One entry per subtitle line: (centre y, bar end x, dot colour). The ragged
# right edges are what make the block read as text rather than as a "=" sign.
LINES = (
    (0.255, 0.82, (251, 191, 36)),   # #FBBF24 amber
    (0.500, 0.66, (125, 211, 252)),  # #7DD3FC sky
    (0.745, 0.86, (52, 211, 153)),   # #34D399 emerald
)


def _in_rounded_rect(x: float, y: float, x0: float, y0: float, x1: float, y1: float, radius: float) -> bool:
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    radius = min(radius, (x1 - x0) / 2, (y1 - y0) / 2)
    # Clamp the point to the inner rectangle spanned by the corner centres; if
    # it moved on both axes we are in a corner and need the circular test.
    cx = min(max(x, x0 + radius), x1 - radius)
    cy = min(max(y, y0 + radius), y1 - radius)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= radius * radius


def _render(size: int) -> bytes:
    """Render one RGBA bitmap, returned as raw rows of 4-byte pixels."""
    tile = (
        TILE_MARGIN * size,
        TILE_MARGIN * size,
        (1 - TILE_MARGIN) * size,
        (1 - TILE_MARGIN) * size,
    )
    tile_radius = TILE_RADIUS * size

    half = BAR_THICKNESS * size / 2
    dot_radius = DOT_RADIUS * size
    dot_x = DOT_CENTRE_X * size
    bar_x0 = BAR_START_X * size
    # Pre-resolve each line to pixel space: the bar as a fully rounded capsule
    # and the dot as a centre/radius pair.
    shapes = [
        ((bar_x0, cy * size - half, end_x * size, cy * size + half), (dot_x, cy * size), colour)
        for cy, end_x, colour in LINES
    ]

    samples = SS * SS
    step = 1.0 / SS
    offset = step / 2
    out = bytearray()

    for py in range(size):
        for px in range(size):
            # Accumulate premultiplied alpha so transparent samples do not
            # darken the edge pixels.
            acc_r = acc_g = acc_b = acc_a = 0
            for sy in range(SS):
                y = py + offset + sy * step
                for sx in range(SS):
                    x = px + offset + sx * step
                    if not _in_rounded_rect(x, y, *tile, tile_radius):
                        continue
                    colour = BACKGROUND
                    for bar, (cx, cy), dot_colour in shapes:
                        if _in_rounded_rect(x, y, *bar, (bar[3] - bar[1]) / 2):
                            colour = FOREGROUND
                            break
                        dx, dy = x - cx, y - cy
                        if dx * dx + dy * dy <= dot_radius * dot_radius:
                            colour = dot_colour
                            break
                    acc_r += colour[0]
                    acc_g += colour[1]
                    acc_b += colour[2]
                    acc_a += 255
            if acc_a == 0:
                out += b"\x00\x00\x00\x00"
                continue
            alpha = acc_a // samples
            # Un-premultiply: the colour accumulators only summed covered
            # samples, so dividing by the covered count restores the hue.
            covered = acc_a // 255
            out += bytes((acc_r // covered, acc_g // covered, acc_b // covered, alpha))
    return bytes(out)


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _to_png(size: int, rgba: bytes) -> bytes:
    stride = size * 4
    # Filter type 0 (None) in front of every scanline.
    raw = b"".join(b"\x00" + rgba[row * stride:(row + 1) * stride] for row in range(size))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def build_ico() -> bytes:
    images = [_to_png(size, _render(size)) for size in SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = bytearray()
    for size, png in zip(SIZES, images):
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII",
            dimension,  # width
            dimension,  # height
            0,  # palette size (0 = truecolour)
            0,  # reserved
            1,  # colour planes
            32,  # bits per pixel
            len(png),
            offset,
        )
        offset += len(png)
    return bytes(header + entries) + b"".join(images)


def main() -> None:
    default = Path(__file__).resolve().parent.parent / "static" / "app.ico"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(build_ico())
    print(f"Wrote {target} ({target.stat().st_size} bytes, sizes: {', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    main()
