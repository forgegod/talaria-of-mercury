#!/usr/bin/env python3
"""Render the Talaria lock-up as a coloured SVG (v7 — vector path glyph).

Design language:

  - Dark navy background (#1a1f3a) matching the Hermes Agent mark.
  - Golden-yellow primary fill (#ffc72c) with an amber bottom band
    (#f9a23a) — same bicolour as the Hermes Agent letter.
  - Flame gradient (#ff6a3d → #ca1a0f → #7a0d06) on the trail of the
    sandal, inspired by the Hephaistos flames palette.

Glyph — drawn as a single <path>, not ASCII pixel art:

  * A horizontal sole slab at the bottom, with a small toe notch.
  * A central heel cup rising from the centre of the sole.
  * Two large symmetric wings flaring out from the heel cup, each
    composed of three scalloped feathers.  The wings are smooth
    Bezier curves so they read clearly at any size.

  The colour gradient is achieved by layering three <path> variants:
  the bottom 28% of the silhouette in amber, the top 72% in gold, and
  a flame-coloured trail below.
"""

from __future__ import annotations

import math
from pathlib import Path

# ---------------------------------------------------------------------------
# Colour palette
# ---------------------------------------------------------------------------
GOLD = "#ffc72c"
AMBER = "#f9a23a"


# ---------------------------------------------------------------------------
# Glyph geometry — vector path, not ASCII
# ---------------------------------------------------------------------------
# All coordinates in a 240×184 viewBox.  The glyph is centred at x=120.
# Wings extend up to y=22, slim base bar bottom at y=184.  Each side
# (left + right) is a single closed path that flows 3 wing feathers
# into the slim base bar with no internal seam.
GLYPH_VB_W = 240
GLYPH_VB_H = 184
GLYPH_W = 240
GLYPH_H = 184


# ---------------------------------------------------------------------------
# SVG rendering — glyph
# ---------------------------------------------------------------------------
def _solid_3_paths() -> tuple[str, str, float, float, float, float]:
    """Return (left_path, right_path, base_left, base_right, base_top,
    base_bottom) for the solid_3 winged-sandal glyph.

    Each half is a SINGLE closed path that flows 3 wing feathers
    down to the BASE BAR TOP (y = base_top = 170), then continues
    along the base bar bottom (y = 184) and back up the centreline.
    The wings + base bar are ONE integral piece — there is no
    separate pedestal between them.

    The wing path therefore includes the base bar geometry
    directly, so the feather-curl tapers smoothly INTO the base
    bar without a stripeline.  The base bar IS the lower part of
    the glyph.

    Glyph viewBox: 240 wide x 184 tall.  Wing tips at y=22, base
    bar from y=170 to y=184, x=22 to x=218.  Smooth cubic
    Béziers only — no stair-stepping.
    """
    base_top = 170
    base_bottom = 184
    base_left = 22
    base_right = 218
    left = (
        f"M 120 70 "
        f"C 90 30, 50 22, 22 50 "
        f"C 38 60, 60 65, 78 70 "
        f"C 50 70, 18 82, 12 110 "
        f"C 38 100, 64 95, 80 92 "
        f"C 50 102, 24 120, 22 150 "
        f"C 50 145, 70 138, 84 130 "
        # Q-curve sweeps the lower feather tip down to the base
        # bar's outer corner.  The base bar (from y=170 to y=184)
        # is part of the same path, so the wing tapers seamlessly
        # into the bar without a visible flat edge or seam.
        f"Q 60 164, {base_left} {base_top} "
        f"L {base_left} {base_bottom} "
        f"L 120 {base_bottom} "
        f"Z"
    )
    right = (
        f"M 120 70 "
        f"C 150 30, 190 22, 218 50 "
        f"C 202 60, 180 65, 162 70 "
        f"C 190 70, 222 82, 228 110 "
        f"C 202 100, 176 95, 160 92 "
        f"C 190 102, 216 120, 218 150 "
        f"C 190 145, 170 138, 156 130 "
        f"Q 180 164, {base_right} {base_top} "
        f"L {base_right} {base_bottom} "
        f"L 120 {base_bottom} "
        f"Z"
    )
    return left, right, base_left, base_right, base_top, base_bottom


def _palm_frond_paths() -> tuple[str, str, float, float, float, float]:
    """Return (left_path, right_path, base_left, base_right, base_top,
    base_bottom) for the palm-frond silhouette.

    The design is a multi-layered, petal-like plant silhouette
    with 3 distinct frond layers on each side, separated by small
    inward dips that create visible negative-space gaps.  Each
    half closes at a CLEAN LOWER EDGE above the base bar
    (y = wing_bottom = 140, above the wordmark-aligned band start
    at y=145.29).  The base bar is rendered as a SEPARATE amber
    `<rect>` LAYER under the palm.  Smooth cubic Béziers only —
    no stair-stepping.

    Glyph viewBox: 240 wide x 184 tall.  Fronds extend up to
    y=5, palm closes at y=140, base bar from y=170 to y=184.
    """
    wing_bottom = 140
    base_top = 170
    base_bottom = 184
    base_left = 22
    base_right = 218
    left = (
        f"M 120 35 "          # start at top of central frond
        # central upright frond — narrow, pointed at top
        f"C 116 25, 112 15, 120 5 "   # left edge of central
        f"C 128 15, 124 25, 120 35 "  # right edge of central
        # GAP between central and upper frond — dip inward
        f"C 118 38, 116 40, 114 42 "
        # upper-left frond — sweeps up and out, then back
        f"C 95 32, 65 22, 38 38 "
        f"C 55 44, 78 50, 100 52 "
        # GAP between upper and middle frond
        f"C 96 55, 94 58, 92 60 "
        # middle-left frond — sweeps out
        f"C 72 56, 42 66, 20 82 "
        f"C 45 80, 72 76, 95 76 "
        # GAP between middle and lower frond
        f"C 92 78, 89 80, 86 82 "
        # lower-left frond — sweeps down and out
        f"C 65 80, 38 96, 20 118 "
        f"C 48 108, 72 98, 92 94 "
        # transition to lower edge of the palm — curve to the
        # palm's lower-left corner at y=wing_bottom, NOT to the
        # base bar.  This keeps the palm and the base bar as two
        # separate layers with a visible white gap between them.
        f"Q 50 130, {base_left} {wing_bottom} "
        f"L 120 {wing_bottom} "
        f"Z"
    )
    right = (
        f"M 120 35 "
        f"C 124 25, 128 15, 120 5 "
        f"C 112 15, 116 25, 120 35 "
        f"C 122 38, 124 40, 126 42 "
        f"C 145 32, 175 22, 202 38 "
        f"C 185 44, 162 50, 140 52 "
        f"C 144 55, 146 58, 148 60 "
        f"C 168 56, 198 66, 220 82 "
        f"C 195 80, 168 76, 145 76 "
        f"C 148 78, 151 80, 154 82 "
        f"C 175 80, 202 96, 220 118 "
        f"C 192 108, 168 98, 148 94 "
        f"Q 190 130, {base_right} {wing_bottom} "
        f"L 120 {wing_bottom} "
        f"Z"
    )
    return left, right, base_left, base_right, base_top, base_bottom


def _word_baseline_y_in_lockup() -> int:
    """Return the y-coordinate of the wordmark baseline in the lockup.

    Mirrors the math in render_lockup: the wordmark is placed at
    PADDING_X + GLYPH_W + GAP horizontally, and vertically centred
    in the lockup.  The baseline sits WORDMARK_H - 18% of the
    font-size below the wordmark's top.
    """
    _, word_y = _vertical_offsets()
    return word_y + WORDMARK_H - int(WORDMARK_FONT * 0.18)


def render_glyph(
    *,
    origin_x: int,
    origin_y: int,
    band_top_lockup: float | None = None,
    band_height: float | None = None,
    band_top_local: float | None = None,
    band_id: str = "talaria-glyph-band",
) -> str:
    """Render the production glyph (winged-sandal solid_3) with
    the bicolour ribbon overlay.

    Layered structure (back to front):
      1. Wings + base bar — gold `<path>` (left + right), each a
         single closed path that flows 3 wing feathers down to
         the base bar's outer corner and continues along the
         base bar bottom back to the centreline.  The wings and
         base bar are one integral piece — there is NO separate
         pedestal between them.
      2. Amber ribbon overlay — a clipPath-ambered copy of each
         wing path painted ON TOP of the gold wings, clipped to
         the wordmark-aligned band region.  Produces the
         bicolour ribbon (gold upper wings, amber lower band
         that reads continuously into the base bar).

    Band math:
      - The production lockup passes `band_top_lockup` and
        `band_height` in LOCKUP coordinates (matching the wordmark
        band so the amber strip is continuous across both halves
        of the lockup).  Defaults: band_top_lockup = 152.2928,
        band_height = 43.5456.
      - The standalone mark passes `band_top_local` and reuses
        `band_height` — the standard mark rule is "bottom 36% of
        the glyph bbox", giving band_top_local = 184 - 0.64 *
        184 = 117.76 with band_height = 66.24 (or whatever the
        caller passes via `band_height`).
      - Exactly one of `band_top_lockup` / `band_top_local` should
        be supplied.  When `band_top_local` is provided, the
        LOCKUP-coordinate path is skipped entirely.
    """
    left, right, base_left, base_right, base_top, base_bottom = (
        _solid_3_paths()
    )

    base_w = base_right - base_left
    base_h = base_bottom - base_top

    # Ribbon overlay: clipPath rect.  Two ways to specify the band:
    #   1. `band_top_local` (glyph-local coords) — used by the mark
    #      (the band is the bottom 36% of the glyph bbox).
    #   2. `band_top_lockup` (lockup coords) — used by the lockup
    #      (the band matches the wordmark band so the amber strip
    #      is continuous across both halves).
    # Exactly one of these should be supplied by the caller.  When
    # neither is supplied, default to the lockup rule.
    _cap = 168 * 0.72
    if band_height is None:
        if band_top_local is not None:
            # Mark rule: bottom 36% of the glyph viewBox (184 units).
            band_height = GLYPH_VB_H * 0.36
        else:
            band_height = _cap * 0.36
    if band_top_local is not None:
        pass  # glyph-local override already supplied
    elif band_top_lockup is None:
        band_top_local = (191 - _cap + _cap * 0.68) - origin_y
    else:
        band_top_local = band_top_lockup - origin_y

    return (
        # <defs> as a SIBLING of <g>, not nested — see skill pitfalls.
        f'  <defs>\n'
        f'    <clipPath id="{band_id}">\n'
        f'      <rect x="0" y="{band_top_local:.6f}" '
        f'width="{GLYPH_VB_W}" height="{band_height:.6f}"/>\n'
        f'    </clipPath>\n'
        f'  </defs>\n'
        # Layer 1: gold wings + base bar (one integral piece).
        f'  <g transform="translate({origin_x} {origin_y})">\n'
        f'    <path d="{left}" fill="{GOLD}"/>\n'
        f'    <path d="{right}" fill="{GOLD}"/>\n'
        # Layer 2: ribbon overlay — amber wing copies clipped to
        # the band region.  Painted ON TOP of the gold wings so
        # the lower portion of the wings + the base bar becomes
        # amber, completing the bicolour ribbon continuous with
        # the wordmark band.
        f'  </g>\n'
        f'  <g transform="translate({origin_x} {origin_y})" '
        f'clip-path="url(#{band_id})">\n'
        f'    <path d="{left}" fill="{AMBER}"/>\n'
        f'    <path d="{right}" fill="{AMBER}"/>\n'
        f'  </g>\n'
    )


# ---------------------------------------------------------------------------
# Wordmark
# ---------------------------------------------------------------------------
WORDMARK_FONT = 168
WORDMARK_TEXT = "TALARIA"
WORDMARK_W = 1100
WORDMARK_H = int(WORDMARK_FONT * 0.95)

PADDING_X = 64
GAP = 80
LOCKUP_W = PADDING_X + GLYPH_W + GAP + WORDMARK_W + PADDING_X
LOCKUP_H = max(GLYPH_H, WORDMARK_H) + 100


def render_wordmark(*, origin_x: int, origin_y: int) -> str:
    """Render TALARIA with a gold top and amber bottom band."""
    x = origin_x
    y = origin_y
    cap = WORDMARK_FONT * 0.72
    band_top = y - cap + cap * 0.68
    band_height = cap * 0.36
    band_width = WORDMARK_W

    return (
        f'  <defs>\n'
        f'    <clipPath id="talaria-band-clip">\n'
        f'      <text x="{x}" y="{y}" '
        f'font-family="Georgia, \'Times New Roman\', serif" '
        f'font-size="{WORDMARK_FONT}" font-weight="700" '
        f'letter-spacing="12">{WORDMARK_TEXT}</text>\n'
        f'    </clipPath>\n'
        f'  </defs>\n'
        f'  <text x="{x}" y="{y}" '
        f'font-family="Georgia, \'Times New Roman\', serif" '
        f'font-size="{WORDMARK_FONT}" font-weight="700" '
        f'letter-spacing="12" fill="{GOLD}">{WORDMARK_TEXT}</text>\n'
        f'  <rect x="{x - 4}" y="{band_top}" '
        f'width="{band_width}" height="{band_height}" '
        f'fill="{AMBER}" clip-path="url(#talaria-band-clip)"/>\n'
    )


# ---------------------------------------------------------------------------
# Lockup assembly
# ---------------------------------------------------------------------------
def _vertical_offsets():
    block_top = (LOCKUP_H - max(GLYPH_H, WORDMARK_H)) // 2
    if GLYPH_H >= WORDMARK_H:
        glyph_origin_y = block_top
        wordmark_origin_y = block_top + (GLYPH_H - WORDMARK_H) // 2
    else:
        glyph_origin_y = block_top + (WORDMARK_H - GLYPH_H) // 2
        wordmark_origin_y = block_top
    return glyph_origin_y, wordmark_origin_y


def _glyph_origin_y_for_baseline() -> int:
    """Return the glyph origin_y so the base bar bottom (local y=184)
    sits exactly on the wordmark baseline in the lockup.

    Used by the production lockup so the palm-frond glyph bottom
    aligns with the TALARIA baseline.
    """
    word_baseline_y = _word_baseline_y_in_lockup()
    return word_baseline_y - 184


def render_lockup() -> str:
    """Production lockup: winged-sandal solid_3 glyph + TALARIA
    wordmark on a transparent background.

    The glyph is placed so its base bar bottom (local y=184) sits
    on the wordmark baseline.  The wordmark is placed horizontally
    after the glyph with a GAP of 80px and vertically centred in
    the lockup.  No background fill — the SVG is transparent so it
    can be placed on any surface.  The glyph is single-colour
    (gold) with a separate pedestal + base bar (no bicolour band).
    """
    glyph_origin_y = _glyph_origin_y_for_baseline()
    glyph = render_glyph(
        origin_x=PADDING_X,
        origin_y=glyph_origin_y,
    )
    word_baseline_y = _word_baseline_y_in_lockup()
    word = render_wordmark(
        origin_x=PADDING_X + GLYPH_W + GAP,
        origin_y=word_baseline_y,
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {LOCKUP_W} {LOCKUP_H}" role="img" '
        f'aria-labelledby="title">\n'
        f'  <title id="title">Talaria — winged sandals for the Hermes Agent</title>\n'
        f'  <!-- solid_3 winged-sandal glyph (band-aligned) -->\n'
        f'{glyph}\n'
        f'  <!-- TALARIA wordmark -->\n'
        f'{word}\n'
        f'</svg>\n'
    )


def render_mark_only() -> str:
    """Square mark-only: winged-sandal solid_3 glyph on a transparent
    background, centred with padding.

    Band rule: the mark uses the standard glyph-bbox rule (bottom
    36%) rather than the wordmark-aligned rule (which makes sense
    only when a wordmark is present to anchor the strip).  In glyph-
    local coords the band starts at y = 184 - 0.36 * 184 = 117.76 and
    extends 66.24 units down to the bbox bottom.  This puts the
    amber ribbon visually across the lower portion of the wings
    and the base bar, mirroring the lockup ribbon position.
    """
    pad = 48
    w = GLYPH_W + 2 * pad
    h = GLYPH_H + 2 * pad
    glyph = render_glyph(
        origin_x=pad,
        origin_y=pad,
        band_top_local=GLYPH_VB_H * 0.64,  # bottom 36% — same rule
                                          # as the original mark
    )
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

    print(f"lockup: {LOCKUP_W} x {LOCKUP_H}")
    print("wrote logo.svg, logo-mark.svg")


if __name__ == "__main__":
    main()
