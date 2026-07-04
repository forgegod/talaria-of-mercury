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

    Band math: the ribbon overlay MUST clip to the same y-range
    as the wordmark band so the amber strip is continuous across
    both halves of the lockup.  Pass `band_top_lockup` and
    `band_height` in LOCKUP coordinates (default values match
    the wordmark band: top at lockup y = 152.2928, height =
    43.5456).  In glyph-local coordinates, the band top is
    `band_top_lockup - origin_y`.

    The standalone mark (`render_mark_only`) uses the same band
    math by default, which keeps the glyph self-consistent.
    """
    left, right, base_left, base_right, base_top, base_bottom = (
        _solid_3_paths()
    )

    base_w = base_right - base_left
    base_h = base_bottom - base_top

    # Ribbon overlay: clipPath rect in LOCKUP coordinates.  Defaults
    # align with the wordmark band so the amber strip is continuous.
    # Matches render_wordmark() math:
    # cap = WORDMARK_FONT * 0.72 (= 120.96)
    # band_top_lockup = word_baseline_y - cap + cap * 0.68
    #                  = 191 - 120.96 + 82.2528 = 152.2928
    # band_height = cap * 0.36 = 43.5456
    _cap = 168 * 0.72
    if band_top_lockup is None:
        band_top_lockup = 191 - _cap + _cap * 0.68
    if band_height is None:
        band_height = _cap * 0.36

    # Convert lockup band coords to glyph-local coords (subtract
    # origin_y).  The clipPath rect spans the full glyph viewBox
    # width; vertical extent is the band region in local coords.
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

    The mark uses the standard glyph-bbox band rule (bottom 36%),
    not the wordmark-aligned band (no wordmark in the mark).
    """
    pad = 48
    w = GLYPH_W + 2 * pad
    h = GLYPH_H + 2 * pad
    glyph = render_glyph(origin_x=pad, origin_y=pad)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {w} {h}" role="img" aria-labelledby="title">\n'
        f'  <title id="title">Talaria mark</title>\n'
        f'{glyph}\n'
        f'</svg>\n'
    )


# ---------------------------------------------------------------------------
# Draft variants — exploratory redesigns on transparent background
# ---------------------------------------------------------------------------
# Each draft swaps the glyph geometry while keeping the bicolour wordmark,
# the Georgia/serif font, and the WORDMARK_FONT size.  Drafts are written
# to assets/drafts/ for comparison; they are NOT the production lockup.

def _draught_glyph(*, origin_x: int, origin_y: int, style: str) -> str:
    """Render the glyph in one of five draft styles, no flame trail.

    All drafts share the same 240x230 viewBox so the wordmark alignment
    stays identical.  The bottom amber band is applied uniformly.

    Geometry model: each draft emits a single combined silhouette per
    side (left half + right half).  The wing curves flow seamlessly into
    the base bar — no separate heel-cup or sole path, no internal seams.
    The two halves meet at the centreline x=120, so the full glyph is
    exactly two filled shapes (plus a stroke for monoline and a circle
    frame for emblem).
    """
    band_top = 15 + (216 - 15) * 0.64
    band_height = (216 - 15) * 0.36

    # Base bar geometry, shared by every style.
    base_top = 170
    base_bottom = 184
    base_left = 22
    base_right = 218

    # Initialise so static analysers can see every branch defines them.
    left = ""
    right = ""
    left_outline = ""
    right_outline = ""

    # Each style provides (left_path, right_path) where each path
    # describes a SINGLE half-silhouette (wing + base flowing together).
    # Paths are closed; they must connect seamlessly at the centreline
    # x=120 and at the base bar (y=base_top..base_bottom).
    if style == "minimal":
        left = (
            # Start at the inner-top of the wing (centreline, above base).
            f"M 120 70 "
            # Curve out and up to the wing tip.
            f"Q 60 30, 18 50 "
            # Curve back along the upper feather.
            f"Q 35 80, 65 95 "
            # Curve out to the lower feather tip.
            f"Q 30 110, 22 150 "
            # Curve back toward the base, ending at the inner edge of
            # the base bar on the left side.
            f"Q 55 160, {base_left} {base_top} "
            # Walk along the top of the base bar to the centreline.
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom} "
            # Climb back up the centreline to close.
            f"Z"
        )
        right = (
            f"M 120 70 "
            f"Q 180 30, 222 50 "
            f"Q 205 80, 175 95 "
            f"Q 210 110, 218 150 "
            f"Q 185 160, {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom} "
            f"Z"
        )
    elif style == "bold":
        # Wider, more geometric wings — chunky feather blocks.
        left = (
            f"M 120 70 "
            f"L 24 22 "
            f"L 38 60 "
            f"L 6 90 "
            f"L 36 110 "
            f"L 10 150 "
            f"L {base_left} {base_top} "
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom} "
            f"Z"
        )
        right = (
            f"M 120 70 "
            f"L 216 22 "
            f"L 202 60 "
            f"L 234 90 "
            f"L 204 110 "
            f"L 230 150 "
            f"L {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom} "
            f"Z"
        )
    elif style == "ornate":
        # Heraldic / filigree — four sweeping feathers per side.
        left = (
            f"M 120 70 "
            f"Q 70 20, 30 30 "
            f"Q 60 50, 70 70 "
            f"Q 30 55, 8 80 "
            f"Q 40 95, 70 100 "
            f"Q 25 100, 14 130 "
            f"Q 50 130, 80 120 "
            f"Q 30 130, 30 158 "
            f"Q 70 160, {base_left} {base_top} "
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom} "
            f"Z"
        )
        right = (
            f"M 120 70 "
            f"Q 170 20, 210 30 "
            f"Q 180 50, 170 70 "
            f"Q 210 55, 232 80 "
            f"Q 200 95, 170 100 "
            f"Q 215 100, 226 130 "
            f"Q 190 130, 160 120 "
            f"Q 210 130, 210 158 "
            f"Q 170 160, {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom} "
            f"Z"
        )
    elif style == "monoline":
        # Single stroke outline — combined silhouette drawn as one
        # open contour per side (no internal seams; no base rectangle).
        left_outline = (
            f"M 120 70 "
            f"Q 60 25, 20 50 "
            f"Q 40 80, 70 95 "
            f"Q 30 105, 24 150 "
            f"Q 55 160, {base_left} {base_top} "
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom}"
        )
        right_outline = (
            f"M 120 70 "
            f"Q 180 25, 220 50 "
            f"Q 200 80, 170 95 "
            f"Q 210 105, 216 150 "
            f"Q 185 160, {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom}"
        )
    elif style == "monoline_3":
        # 3 wings per side — top, middle, lower.  Smooth cubic Béziers.
        left_outline = (
            f"M 120 70 "
            # top feather
            f"C 90 30, 50 22, 22 50 "
            f"C 38 60, 60 65, 78 70 "
            # middle feather
            f"C 50 70, 18 82, 12 110 "
            f"C 38 100, 64 95, 80 92 "
            # lower feather
            f"C 50 102, 24 120, 22 150 "
            f"C 50 145, 70 138, 84 130 "
            # base bar
            f"Q 60 164, {base_left} {base_top} "
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom}"
        )
        right_outline = (
            f"M 120 70 "
            f"C 150 30, 190 22, 218 50 "
            f"C 202 60, 180 65, 162 70 "
            f"C 190 70, 222 82, 228 110 "
            f"C 202 100, 176 95, 160 92 "
            f"C 190 102, 216 120, 218 150 "
            f"C 190 145, 170 138, 156 130 "
            f"Q 180 164, {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom}"
        )
    elif style == "monoline_4":
        # 4 wings per side — top pair (split upper feather), middle,
        # trailing lower feather.  All smooth curves.
        left_outline = (
            f"M 120 70 "
            # topmost feather
            f"C 92 28, 56 18, 26 38 "
            f"C 40 50, 58 56, 72 60 "
            # upper-inner feather
            f"C 60 50, 36 50, 14 70 "
            f"C 36 78, 56 82, 72 82 "
            # middle feather
            f"C 50 90, 22 102, 14 130 "
            f"C 40 122, 60 116, 80 110 "
            # trailing lower feather
            f"C 56 120, 30 138, 28 156 "
            f"C 52 150, 70 142, 84 132 "
            # base bar
            f"Q 62 164, {base_left} {base_top} "
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom}"
        )
        right_outline = (
            f"M 120 70 "
            f"C 148 28, 184 18, 214 38 "
            f"C 200 50, 182 56, 168 60 "
            f"C 180 50, 204 50, 226 70 "
            f"C 204 78, 184 82, 168 82 "
            f"C 190 90, 218 102, 226 130 "
            f"C 200 122, 180 116, 160 110 "
            f"C 184 120, 210 138, 212 156 "
            f"C 188 150, 170 142, 156 132 "
            f"Q 178 164, {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom}"
        )
    elif style == "monoline_5":
        # 5 wings per side — three upper feathers (top, top-inner,
        # top-outer), middle feather, trailing lower feather.  All
        # smooth cubic Béziers arranged symmetrically.
        left_outline = (
            f"M 120 70 "
            # topmost feather
            f"C 96 24, 60 14, 28 32 "
            f"C 42 46, 60 52, 74 56 "
            # top-inner feather
            f"C 70 44, 46 42, 20 60 "
            f"C 38 70, 56 74, 72 76 "
            # top-outer feather
            f"C 52 70, 24 84, 8 100 "
            f"C 32 100, 54 96, 74 92 "
            # middle feather
            f"C 48 96, 22 110, 14 134 "
            f"C 38 128, 58 122, 78 116 "
            # trailing lower feather
            f"C 52 124, 28 142, 26 158 "
            f"C 50 150, 70 144, 84 134 "
            # base bar
            f"Q 62 164, {base_left} {base_top} "
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom}"
        )
        right_outline = (
            f"M 120 70 "
            f"C 144 24, 180 14, 212 32 "
            f"C 198 46, 180 52, 166 56 "
            f"C 170 44, 194 42, 220 60 "
            f"C 202 70, 184 74, 168 76 "
            f"C 188 70, 216 84, 232 100 "
            f"C 208 100, 186 96, 166 92 "
            f"C 192 96, 218 110, 226 134 "
            f"C 202 128, 182 122, 162 116 "
            f"C 188 124, 212 142, 214 158 "
            f"C 190 150, 170 144, 156 134 "
            f"Q 178 164, {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom}"
        )
    elif style == "monoline_6":
        # 6 wings per side — densest fan, all smooth curves.  Three
        # upper tiers, two mid tiers, one trailing lower feather.
        left_outline = (
            f"M 120 70 "
            # tier 1: topmost
            f"C 96 20, 60 10, 26 28 "
            f"C 40 42, 58 48, 72 52 "
            # tier 1: top-inner
            f"C 72 40, 50 36, 24 50 "
            f"C 38 60, 56 66, 70 68 "
            # tier 2: upper-outer
            f"C 58 60, 32 72, 10 86 "
            f"C 32 92, 54 90, 72 88 "
            # tier 2: mid
            f"C 52 86, 24 96, 14 116 "
            f"C 36 112, 56 108, 74 104 "
            # tier 3: lower-mid
            f"C 50 110, 24 122, 16 142 "
            f"C 38 138, 58 132, 78 126 "
            # trailing lower feather
            f"C 52 134, 30 148, 28 160 "
            f"C 50 154, 70 146, 84 136 "
            # base bar
            f"Q 62 164, {base_left} {base_top} "
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom}"
        )
        right_outline = (
            f"M 120 70 "
            f"C 144 20, 180 10, 214 28 "
            f"C 200 42, 182 48, 168 52 "
            f"C 168 40, 190 36, 216 50 "
            f"C 202 60, 184 66, 170 68 "
            f"C 182 60, 208 72, 230 86 "
            f"C 208 92, 186 90, 168 88 "
            f"C 188 86, 216 96, 226 116 "
            f"C 204 112, 184 108, 166 104 "
            f"C 190 110, 216 122, 224 142 "
            f"C 202 138, 182 132, 162 126 "
            f"C 188 134, 210 148, 212 160 "
            f"C 190 154, 170 146, 156 136 "
            f"Q 178 164, {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom}"
        )
    elif style == "solid_3":
        # Solid filled variant of the monoline_3 wing count (3 feathers
        # per side).  Each half is a single closed path (wing+base
        # flowing together) so there are no internal seams.  Smooth
        # cubic Béziers only — no stair-stepping.
        left = (
            f"M 120 70 "
            # top feather — tip
            f"C 90 30, 50 22, 22 50 "
            # top feather — return
            f"C 38 60, 60 65, 78 70 "
            # middle feather — tip
            f"C 50 70, 18 82, 12 110 "
            # middle feather — return
            f"C 38 100, 64 95, 80 92 "
            # lower feather — tip
            f"C 50 102, 24 120, 22 150 "
            # lower feather — return toward the base bar
            f"C 50 145, 70 138, 84 130 "
            # base bar — drop to the left edge, across the bottom,
            # back up the centreline.
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
    elif style == "emblem":
        # Circular emblem — combined silhouettes inside a roundel frame.
        left = (
            f"M 120 70 "
            f"Q 65 35, 35 55 "
            f"Q 55 80, 78 92 "
            f"Q 45 95, 32 130 "
            f"Q 65 164, {base_left} {base_top} "
            f"L {base_left} {base_bottom} "
            f"L 120 {base_bottom} "
            f"Z"
        )
        right = (
            f"M 120 70 "
            f"Q 175 35, 205 55 "
            f"Q 185 80, 162 92 "
            f"Q 195 95, 208 130 "
            f"Q 175 164, {base_right} {base_top} "
            f"L {base_right} {base_bottom} "
            f"L 120 {base_bottom} "
            f"Z"
        )
    else:
        raise ValueError(f"unknown draft style: {style}")

    def _top_half_paths(fill: str, stroke: str | None) -> str:
        """Return the visible shapes for the top half of the glyph."""
        if style.startswith("monoline"):
            sw = "6"
            return (
                f'    <path d="{left_outline}" fill="none" '
                f'stroke="{stroke or fill}" stroke-width="{sw}" '
                f'stroke-linejoin="round"/>\n'
                f'    <path d="{right_outline}" fill="none" '
                f'stroke="{stroke or fill}" stroke-width="{sw}" '
                f'stroke-linejoin="round"/>\n'
            )
        if style == "emblem":
            return (
                f'    <circle cx="120" cy="120" r="108" fill="none" '
                f'stroke="{fill}" stroke-width="6"/>\n'
                f'    <path d="{left}" fill="{fill}"/>\n'
                f'    <path d="{right}" fill="{fill}"/>\n'
            )
        return (
            f'    <path d="{left}" fill="{fill}"/>\n'
            f'    <path d="{right}" fill="{fill}"/>\n'
        )

    # Top half: full-colour gold (with optional stroke for monoline).
    top = (
        f'  <g transform="translate({origin_x} {origin_y})">\n'
        + _top_half_paths(GOLD, None)
        + f'  </g>\n'
    )
    # Bottom band: same shapes re-drawn in amber, clipped to the band rect.
    band = (
        f'  <defs>\n'
        f'    <clipPath id="glyph-bottom-band-{style}">\n'
        f'      <rect x="0" y="{band_top}" '
        f'width="{GLYPH_VB_W}" height="{band_height}"/>\n'
        f'    </clipPath>\n'
        f'  </defs>\n'
        f'  <g transform="translate({origin_x} {origin_y})" '
        f'clip-path="url(#glyph-bottom-band-{style})">\n'
        + _top_half_paths(AMBER, AMBER)
        + f'  </g>\n'
    )
    return top + band


def render_draft(style: str) -> str:
    """Render a single draft lockup with transparent background."""
    glyph_y, word_y = _vertical_offsets()
    glyph = _draught_glyph(
        origin_x=PADDING_X, origin_y=glyph_y, style=style,
    )
    word_baseline_y = word_y + WORDMARK_H - int(WORDMARK_FONT * 0.18)
    word = render_wordmark(
        origin_x=PADDING_X + GLYPH_W + GAP,
        origin_y=word_baseline_y,
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {LOCKUP_W} {LOCKUP_H}" role="img" '
        f'aria-labelledby="title">\n'
        f'  <title id="title">Talaria draft {style} — '
        f'winged sandals for the Hermes Agent</title>\n'
        f'  <!-- winged-sandal glyph ({style}) -->\n'
        f'{glyph}\n'
        f'  <!-- TALARIA wordmark -->\n'
        f'{word}\n'
        f'</svg>\n'
    )


DRAFT_STYLES = (
    "minimal",
    "bold",
    "ornate",
    "monoline",
    "monoline_3",
    "monoline_4",
    "monoline_5",
    "monoline_6",
    "solid_3",
    "solid_3_aligned",
    "solid_3_baseless",
    "solid_3_moved",
    "palm_frond",
    "emblem",
)


def render_draft_aligned(style: str) -> str:
    """Render an aligned lockup where the glyph band matches the
    wordmark band AND the glyph bottom matches the wordmark baseline.

    The glyph is kept at FULL size (no scale) — the only change vs.
    the standard `solid_3` lockup is that the bicolour band is
    rendered as a custom band rect sized to match the wordmark band
    thickness in lockup units, so the visible amber strip aligns
    perfectly across both halves of the lockup.  The glyph is then
    translated so its bottom edge sits exactly on the wordmark
    baseline.

    The wordmark, font, and palette are unchanged.
    """
    # Wordmark geometry — same constants as render_wordmark.
    cap = WORDMARK_FONT * 0.72
    band_top_offset_from_baseline = cap * (1 - 0.68)  # 0.32 * cap
    word_baseline_y = (
        _vertical_offsets()[1] + WORDMARK_H - int(WORDMARK_FONT * 0.18)
    )
    word_band_top_y = word_baseline_y - band_top_offset_from_baseline
    word_band_height = cap * 0.36

    # Place the glyph so its bottom (local y=184) sits on the
    # wordmark baseline.  This makes the ornament and font share
    # the same base level.
    glyph_origin_y = word_baseline_y - 184
    glyph_origin_x = PADDING_X

    # Initialise band_height_local so Pyright sees it as bound.
    # The solid_3_thinband branch overwrites this to 10.
    band_height_local = word_band_height

    # Path data — identical to solid_3, with the basement dropped
    # (path closes at the lower feather return, no base bar block).
    if style == "solid_3_aligned":
        left = (
            f"M 120 70 "
            f"C 90 30, 50 22, 22 50 "
            f"C 38 60, 60 65, 78 70 "
            f"C 50 70, 18 82, 12 110 "
            f"C 38 100, 64 95, 80 92 "
            f"C 50 102, 24 120, 22 150 "
            f"C 50 145, 70 138, 84 130 "
            f"Q 60 164, 22 170 "
            f"L 22 184 "
            f"L 120 184 "
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
            f"Q 180 164, 218 170 "
            f"L 218 184 "
            f"L 120 184 "
            f"Z"
        )
    elif style == "solid_3_baseless":
        # OPTION-A-LIKE baseless variant: same 3-feather upper
        # geometry, but the lower feather is a small pointed tail
        # (not a wide sweep) so the bottom doesn't read as a block
        # when the amber band fills the lower portion.  The lowest
        # point of the silhouette is the lower feather tip at local
        # y=132; placing this on the wordmark baseline makes the
        # glyph bottom = baseline and the wing top extend slightly
        # above the cap-top.  Bicolour is preserved (gold top + amber
        # band matching the wordmark band thickness and position).
        # NOTE: this is the "earlier solid_3_baseless" attempt.
        # Superseded by solid_3_thinband / solid_3_narrowtail /
        # solid_3_goldshoe which are the three real options.
        left = (
            f"M 120 70 "
            f"C 90 30, 50 22, 22 50 "
            f"C 38 60, 60 65, 78 70 "
            f"C 50 70, 18 82, 12 110 "
            f"C 38 100, 64 95, 80 92 "
            f"C 70 108, 60 122, 70 132 "
            f"C 82 128, 95 125, 105 125 "
            f"Q 114 98, 120 70 "
            f"Z"
        )
        right = (
            f"M 120 70 "
            f"C 150 30, 190 22, 218 50 "
            f"C 202 60, 180 65, 162 70 "
            f"C 190 70, 222 82, 228 110 "
            f"C 202 100, 176 95, 160 92 "
            f"C 170 108, 180 122, 170 132 "
            f"C 158 128, 145 125, 135 125 "
            f"Q 126 98, 120 70 "
            f"Z"
        )
        glyph_origin_y = word_baseline_y - 132

    elif style == "solid_3_moved":
        # SIMPLE TRANSLATE — no geometry changes.
        # The exact solid_3 path data (wings + base bar, 3 feathers
        # per side, bicolour) is used unchanged.  The glyph is
        # simply translated UPWARD so the base bar bottom (local
        # y=184) lands exactly on the wordmark baseline.  No
        # scaling, no band math changes, no overlays, no redesign.
        # The bicolour band uses the standard solid_3 rule
        # (bottom 36% of the glyph viewBox).  The bands are at
        # different y positions (glyph band higher than wordmark
        # band) — that's the natural consequence of the two band
        # rules being different.  This is the simplest possible
        # answer to "just move the glyph up so the baseline
        # aligns with the font".
        left = (
            f"M 120 70 "
            f"C 90 30, 50 22, 22 50 "
            f"C 38 60, 60 65, 78 70 "
            f"C 50 70, 18 82, 12 110 "
            f"C 38 100, 64 95, 80 92 "
            f"C 50 102, 24 120, 22 150 "
            f"C 50 145, 70 138, 84 130 "
            f"Q 60 164, 22 170 "
            f"L 22 184 "
            f"L 120 184 "
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
            f"Q 180 164, 218 170 "
            f"L 218 184 "
            f"L 120 184 "
            f"Z"
        )
        # Base bar bottom at local y=184 lands on the baseline.
        glyph_origin_y = word_baseline_y - 184
        # Band uses the standard solid_3 rule: bottom 36% of the
        # glyph viewBox content (y=15..184).  This is DIFFERENT
        # from the wordmark band (bottom 36% of cap-height), so
        # the two bands are at different y positions.  This is
        # the natural consequence of the two band rules being
        # different.

    elif style == "palm_frond":
        # Palm-frond silhouette draft — uses the same path data
        # as the production glyph.  See _palm_frond_paths() and
        # render_glyph() for the geometry.  The draft is placed
        # on a transparent background so it can be reviewed on
        # any surface.
        # Discard the trailing base-bar geometry — the aligned
        # renderer emits its own band rect, not a separate base
        # bar layer.
        left, right, _, _, _, _ = _palm_frond_paths()
        glyph_origin_y = _glyph_origin_y_for_baseline()

    else:
        raise ValueError(f"unknown aligned style: {style}")

    # Glyph band rect in glyph-local coords, chosen so that after
    # the translate above, the band top in lockup coords equals
    # word_band_top_y AND the band thickness matches the wordmark
    # band thickness.  Both alignments fall out of this.
    band_top_local = word_band_top_y - glyph_origin_y
    band_height_local = word_band_height

    word = render_wordmark(
        origin_x=PADDING_X + GLYPH_W + GAP,
        origin_y=word_baseline_y,
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {LOCKUP_W} {LOCKUP_H}" role="img" '
        f'aria-labelledby="title">\n'
        f'  <title id="title">Talaria draft {style} — '
        f'winged sandals for the Hermes Agent (aligned)</title>\n'
        f'  <defs>\n'
        f'    <clipPath id="glyph-bottom-band-{style}">\n'
        f'      <rect x="0" y="{band_top_local}" '
        f'width="{GLYPH_VB_W}" height="{band_height_local}"/>\n'
        f'    </clipPath>\n'
        f'  </defs>\n'
        # Top half: full gold, no transform, no scaling.
        f'  <g transform="translate({glyph_origin_x} {glyph_origin_y})">\n'
        f'    <path d="{left}" fill="{GOLD}"/>\n'
        f'    <path d="{right}" fill="{GOLD}"/>\n'
        f'  </g>\n'
        # Bottom band: a rect of EXACTLY the wordmark band thickness,
        # positioned so its top lands on the wordmark band top.
        # The clipPath restricts the band group to the band region
        # ONLY (so the amber paths don't cover the gold above).
        f'  <g transform="translate({glyph_origin_x} {glyph_origin_y})" '
        f'clip-path="url(#glyph-bottom-band-{style})">\n'
        f'    <rect x="0" y="{band_top_local}" '
        f'width="{GLYPH_VB_W}" height="{band_height_local}" '
        f'fill="{AMBER}"/>\n'
        f'    <path d="{left}" fill="{AMBER}"/>\n'
        f'    <path d="{right}" fill="{AMBER}"/>\n'
        f'  </g>\n'
        f'  <!-- TALARIA wordmark -->\n'
        f'{word}\n'
        f'</svg>\n'
    )


def render_drafts() -> dict[str, str]:
    """Render every draft and return a {style: svg} dict."""
    out = {}
    for style in DRAFT_STYLES:
        # All solid_3_* and palm_frond variants use the aligned
        # renderer because they need custom glyph placement.
        if (
            style.endswith("_aligned")
            or style.endswith("_baseless")
            or style.endswith("_moved")
            or style == "palm_frond"
        ):
            out[style] = render_draft_aligned(style)
        else:
            out[style] = render_draft(style)
    return out


def main() -> None:
    out_dir = Path(__file__).parent
    (out_dir / "logo.svg").write_text(render_lockup())
    (out_dir / "logo-mark.svg").write_text(render_mark_only())

    drafts_dir = out_dir / "drafts"
    drafts_dir.mkdir(exist_ok=True)
    for style, svg in render_drafts().items():
        (drafts_dir / f"logo-draft-{style}.svg").write_text(svg)

    print(f"lockup: {LOCKUP_W} x {LOCKUP_H}")
    print("wrote logo.svg, logo-mark.svg")
    print(f"wrote {len(DRAFT_STYLES)} drafts to {drafts_dir}/")


if __name__ == "__main__":
    main()
