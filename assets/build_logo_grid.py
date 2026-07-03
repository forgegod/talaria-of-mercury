#!/usr/bin/env python3
"""Build a minimalist Talaria logo as a grid of solid squares.

Design language inspired by the Nous Research mark on
hermes-agent.nousresearch.com: a sparse, monochrome, grid-based glyph
on a transparent background. No gradients, no glow, no decoration.

The glyph is an ABSTRACT mark, not a literal depiction of a winged
sandal. The block arrangement suggests a winged object — wider in the
upper half (the wing) and weighted at the base (the foot) — without
trying to render straps or feathers at this resolution.

Grid: 9 columns x 14 rows of cells. Each cell = 8px in the source PNG.
Coordinates are (col, row), 0-indexed from the top-left.
"""

from PIL import Image, ImageDraw

CELL = 8
COLS = 9
ROWS = 14
W = COLS * CELL
H = ROWS * CELL

# Hand-tuned grid. The mark is roughly diamond-shaped: an upper "wing"
# cluster (rows 0-8) and a lower "foot/base" cluster (rows 9-13).
# Both are small, irregular, and separated by a row of negative space.
FILLED = [
    # ---- Upper cluster (wing) ----
    # Top apex: a single block.
    (4, 0),
    # Two blocks forming a small top.
    (3, 1), (4, 1), (5, 1),
    # Widening outward.
    (2, 2), (3, 2), (4, 2), (5, 2), (6, 2),
    # Mid-band: the widest row of the upper cluster.
    (1, 3), (2, 3), (3, 3), (4, 3), (5, 3), (6, 3), (7, 3),
    # Tucking back in, asymmetric for character.
    (1, 4), (2, 4), (3, 4), (4, 4), (5, 4), (6, 4), (7, 4),
    (2, 5), (3, 5), (4, 5), (5, 5), (6, 5),
    (2, 6), (3, 6), (4, 6), (5, 6),
    # Lower edge of the wing: trailing off.
    (3, 7), (4, 7), (5, 7),
    (4, 8),

    # ---- Negative-space row at row 9 ----
    # (intentionally empty)

    # ---- Lower cluster (foot/base) ----
    # A chunky horizontal bar.
    (2, 10), (3, 10), (4, 10), (5, 10), (6, 10), (7, 10),
    # Second row, slightly inset.
    (3, 11), (4, 11), (5, 11), (6, 11), (7, 11),
    # Tiny notch suggesting a heel.
    (7, 12),
    # Tiny notch suggesting a toe.
    (2, 12),
]

# Normalise: drop duplicates, drop out-of-range cells.
filled = {(c, r) for c, r in FILLED if 0 <= c < COLS and 0 <= r < ROWS}


def render(size: int = 8) -> Image.Image:
    """Render the glyph as a transparent-background PNG at the given cell size."""
    cell = size
    w = COLS * cell
    h = ROWS * cell
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for c, r in filled:
        x0 = c * cell
        y0 = r * cell
        draw.rectangle([x0, y0, x0 + cell - 1, y0 + cell - 1], fill=(0, 0, 0, 255))
    return img


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "logo-grid.png"
    render().save(out)
    print(f"wrote {out} ({W}x{H})")