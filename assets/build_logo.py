#!/usr/bin/env python3
"""Render the Talaria lock-up as a monochrome SVG.

Design language: a CLI-app glyph drawn as ASCII art, baked into vector
<rect> primitives. The glyph is a winged sandal silhouette:
  - a tapered upper "wing" cluster
  - a row of negative space separating wing from foot
  - a chunky lower "sole" cluster with a single strap line

ASCII source makes the design easy to tweak and self-documents the
silhouette in plain text. The SVG output is font-independent — no
<text> glyphs, just rects — so it renders identically everywhere.
"""

from __future__ import annotations

from pathlib import Path

# ---------- ASCII source ----------
# Any non-space char renders as a filled cell. Each line is one row.
# Read top-to-bottom: a diamond-shaped wing (rows 0-7), a blank row 8
# separating wing from foot, and a tapered sole (rows 9-10).
ASCII_GLYPH = """\
    ██
   ████
  ██████
 ████████
██████████
 ████████
  ██████
   ████



██████████
 ██████
"""


def _parse_ascii_glyph_centered(source: str) -> list[tuple[int, int]]:
    """Parse *source* into ``(cells)``.

    Each non-space character becomes one filled cell at ``(col, row)``.
    Lines are centred around a common vertical axis so the silhouette is
    visually balanced. Trailing spaces are stripped from each line, but
    *blank lines* are preserved so designers can carve visible gaps
    between sub-shapes (e.g. between wing and sole).
    """
    # Preserve blank lines as zero-cell rows.
    raw_rows = source.splitlines()
    # Compute the column width from the widest non-empty line.
    non_empty = [ln.rstrip() for ln in raw_rows if ln.strip()]
    cols = max((len(ln) for ln in non_empty), default=0)

    cells: list[tuple[int, int]] = []
    for r, line in enumerate(raw_rows):
        stripped = line.strip()
        if not stripped:
            continue
        w = len(stripped)
        offset = (cols - w) // 2
        for c, ch in enumerate(stripped):
            if ch != " ":
                cells.append((offset + c, r))
    return cells


def _glyph_dims(source: str) -> tuple[int, int]:
    """Return ``(cols, rows)`` for the centred glyph.

    Includes blank lines in the row count so designers can shape the
    silhouette with explicit gap rows.
    """
    raw_rows = source.splitlines()
    if not raw_rows:
        return (0, 0)
    non_empty = [ln.rstrip() for ln in raw_rows if ln.strip()]
    cols = max((len(ln) for ln in non_empty), default=0)
    return (cols, len(raw_rows))


# ---------- Layout ----------
CELL = 22
COLS, ROWS = _glyph_dims(ASCII_GLYPH)
GLYPH_W = COLS * CELL
GLYPH_H = ROWS * CELL
PADDING_X = 48
GAP = 56
WORDMARK_X = PADDING_X + GLYPH_W + GAP
WORDMARK_FONT = 116
# TALARIA in Georgia 116px with letter-spacing 6 occupies ~570px.
# Right margin must clear that even when a wider fallback serif is used.
RIGHT_MARGIN = 720
LOCKUP_W = WORDMARK_X + RIGHT_MARGIN
LOCKUP_H = GLYPH_H + 80


# ---------- SVG builders ----------
def render_glyph_svg(*, origin_x: int, origin_y: int, fill: str) -> str:
    """Return SVG <rect> elements for every cell of the ASCII glyph."""
    cells = _parse_ascii_glyph_centered(ASCII_GLYPH)
    parts = []
    for c, r in cells:
        x = origin_x + c * CELL
        y = origin_y + r * CELL
        parts.append(
            f'  <rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" fill="{fill}"/>'
        )
    return "\n".join(parts)


def render_lockup(*, fill: str = "#000") -> str:
    """Render the horizontal lock-up (glyph + wordmark)."""
    glyph_origin_y = (LOCKUP_H - GLYPH_H) // 2
    glyph = render_glyph_svg(
        origin_x=PADDING_X,
        origin_y=glyph_origin_y,
        fill=fill,
    )

    # Vertically align the wordmark cap-height centre with the glyph centre.
    word_cap = WORDMARK_FONT * 0.72
    glyph_center_y = glyph_origin_y + GLYPH_H // 2
    word_baseline_y = glyph_center_y + word_cap // 2

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {LOCKUP_W} {LOCKUP_H}" role="img" '
        f'aria-labelledby="title">\n'
        f'  <title id="title">Talaria — winged sandals for the Hermes Agent</title>\n'
        f'{glyph}\n'
        f'  <text x="{WORDMARK_X}" y="{word_baseline_y}" '
        f'font-family="Georgia, \'Times New Roman\', serif" '
        f'font-size="{WORDMARK_FONT}" font-weight="700" letter-spacing="6" '
        f'fill="{fill}">TALARIA</text>\n'
        f'</svg>\n'
    )


def render_mark_only(*, fill: str = "#000") -> str:
    """Square mark-only variant — just the glyph, centred with padding."""
    pad = 28
    w = COLS * CELL + 2 * pad
    h = ROWS * CELL + 2 * pad
    glyph = render_glyph_svg(origin_x=pad, origin_y=pad, fill=fill)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {w} {h}" role="img" aria-labelledby="title">\n'
        f'  <title id="title">Talaria mark</title>\n'
        f'{glyph}\n'
        f'</svg>\n'
    )


def main() -> None:
    out_dir = Path(__file__).parent
    (out_dir / "logo.svg").write_text(render_lockup())
    (out_dir / "logo-mark.svg").write_text(render_mark_only())
    (out_dir / "logo-inverse.svg").write_text(render_lockup(fill="#fff"))
    print(f"glyph: {COLS} cols x {ROWS} rows, {len(_parse_ascii_glyph_centered(ASCII_GLYPH))} cells")
    print(f"lockup: {LOCKUP_W} x {LOCKUP_H}")
    print("wrote logo.svg, logo-mark.svg, logo-inverse.svg")


if __name__ == "__main__":
    main()