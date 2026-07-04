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
# Glyph viewBox: 240 wide x 184 tall (kept as the
# single source of truth for `_solid_3_paths()` and every
# band/top/bottom offset in the lockup composition).
GLYPH_VB_W = 240
GLYPH_VB_H = 184
GLYPH_W = 240
GLYPH_H = 184

# Thin amber band — single geometry used by both the lockup
# ribbon (via cap-derived math that lands here) and the standalone
# mark (passed explicitly).  Tuned so the band:
#   - sits in the lower portion of the wings (catches the lower
#     feathers + the very top of the base bar),
#   - stays well clear of the wing interior so it doesn't read
#     as a banner through the wing middle,
#   - is thin enough (~14% of glyph height) that the wordmark
#     letterforms below stay mostly gold.
# Math: cap_top is at lockup y=70 (cap=120.96, baseline=191).
# band_top_lockup = baseline - cap + cap*0.72 = 157.13.  Glyph
# origin_y in the lockup = 7, so the glyph-local band is at
# y_local = 150.13.  Round to integers for cleaner output.
GLYPH_BAND_TOP_LOCAL = 150
GLYPH_BAND_HEIGHT = 30


# ---------------------------------------------------------------------------
# SVG rendering — glyph
# ---------------------------------------------------------------------------
# Base bar geometry — single source of truth used by every hallux
# variant below.
_BASE_TOP = 170
_BASE_BOTTOM = 184
_BASE_LEFT = 22
_BASE_RIGHT = 218

# Wing curves — IDENTICAL across all hallux variants.  Three
# cubic-Bézier feathers converging on (84, 130) on the left side
# and (156, 130) on the right side, then a Q-curve sweeping the
# lower feather tip down to the base bar's outer corner.
_WING_TOP_LEFT = (
    "M 120 70 "
    "C 90 30, 50 22, 22 50 "
    "C 38 60, 60 65, 78 70 "
    "C 50 70, 18 82, 12 110 "
    "C 38 100, 64 95, 80 92 "
    "C 50 102, 24 120, 22 150 "
    "C 50 145, 70 138, 84 130"
)
_WING_TOP_RIGHT = (
    "M 120 70 "
    "C 150 30, 190 22, 218 50 "
    "C 202 60, 180 65, 162 70 "
    "C 190 70, 222 82, 228 110 "
    "C 202 100, 176 95, 160 92 "
    "C 190 102, 216 120, 218 150 "
    "C 190 145, 170 138, 156 130"
)


def _hallux_trail(*, left: bool, variant: str) -> str:
    """Return the base-bar + hallux trail appended to the wing
    curve.  Mirrored on both sides via the `left` flag.

    Variants:
      "none" (production default) — straight Q-curve → outer
        corner → base bar bottom → centreline.  No hallux.
      "a-outer-small" — small outer toe-knob (~12u wide, ~7u
        tall) past the outer edge of the base bar.  Amber band
        stays continuous.
      "b-outer-large" — larger outer toe-blob (~22u wide, ~14u
        tall) past the outer edge, dipping 4u below the sole so
        the toe is the lowest point on the glyph.  Amber band
        stays continuous.
      "c-inner-medial" — inner hallux on the medial side
        (toward the centreline, anatomically correct big-toe
        side), ~12u wide × 6u tall.  The two halluces meet at
        the centreline, creating a small notch in the amber
        ribbon where the toes kiss.
    """
    if variant == "none":
        if left:
            return (
                f" Q 60 164, {_BASE_LEFT} {_BASE_TOP} "
                f"L {_BASE_LEFT} {_BASE_BOTTOM} "
                f"L 120 {_BASE_BOTTOM} Z"
            )
        return (
            f" Q 180 164, {_BASE_RIGHT} {_BASE_TOP} "
            f"L {_BASE_RIGHT} {_BASE_BOTTOM} "
            f"L 120 {_BASE_BOTTOM} Z"
        )

    if variant == "a-outer-small":
        if left:
            return (
                f" Q 60 164, {_BASE_LEFT} {_BASE_TOP} "
                # toe: round out past x=22, height ~7u
                f"C 14 168, 8 174, 12 180 "
                f"L {_BASE_LEFT} {_BASE_BOTTOM} "
                f"L 120 {_BASE_BOTTOM} Z"
            )
        return (
            f" Q 180 164, {_BASE_RIGHT} {_BASE_TOP} "
            f"C 226 168, 232 174, 228 180 "
            f"L {_BASE_RIGHT} {_BASE_BOTTOM} "
            f"L 120 {_BASE_BOTTOM} Z"
        )

    if variant == "b-outer-large":
        if left:
            return (
                f" Q 60 164, {_BASE_LEFT} {_BASE_TOP} "
                # toe: large round blob, dips 4u below sole
                f"C 12 166, 0 172, 0 180 "
                f"C 0 188, 10 190, 18 188 "
                f"L {_BASE_LEFT} {_BASE_BOTTOM} "
                f"L 120 {_BASE_BOTTOM} Z"
            )
        return (
            f" Q 180 164, {_BASE_RIGHT} {_BASE_TOP} "
            f"C 228 166, 240 172, 240 180 "
            f"C 240 188, 230 190, 222 188 "
            f"L {_BASE_RIGHT} {_BASE_BOTTOM} "
            f"L 120 {_BASE_BOTTOM} Z"
        )

    if variant == "c-inner-medial":
        if left:
            return (
                f" Q 60 164, {_BASE_LEFT} {_BASE_TOP} "
                f"L {_BASE_LEFT} {_BASE_BOTTOM} "
                # bar bottom runs past the centreline to x=134
                f"L 134 {_BASE_BOTTOM} "
                # inner hallux: round toward the centreline
                f"C 132 178, 124 176, 120 182 "
                f"C 116 186, 120 188, 124 188 "
                f"L 120 {_BASE_BOTTOM} Z"
            )
        return (
            f" Q 180 164, {_BASE_RIGHT} {_BASE_TOP} "
            f"L {_BASE_RIGHT} {_BASE_BOTTOM} "
            f"L 106 {_BASE_BOTTOM} "
            f"C 108 178, 116 176, 120 182 "
            f"C 124 186, 120 188, 116 188 "
            f"L 120 {_BASE_BOTTOM} Z"
        )

    raise ValueError(f"unknown hallux variant: {variant!r}")


def _solid_3_paths(
    hallux_variant: str = "a-outer-small",
) -> tuple[str, str, float, float, float, float]:
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

    Hallux variants: "none" (production default, no toe),
    "a-outer-small", "b-outer-large", "c-inner-medial".  See
    `_hallux_trail()` for the geometry of each variant.

    Glyph viewBox: 240 wide x 184 tall.  Wing tips at y=22, base
    bar from y=170 to y=184, x=22 to x=218.  Smooth cubic
    Béziers only — no stair-stepping.
    """
    left = _WING_TOP_LEFT + _hallux_trail(left=True, variant=hallux_variant)
    right = _WING_TOP_RIGHT + _hallux_trail(left=False, variant=hallux_variant)
    return (
        left,
        right,
        _BASE_LEFT,
        _BASE_RIGHT,
        _BASE_TOP,
        _BASE_BOTTOM,
    )


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
    hallux_variant: str = "a-outer-small",
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
        _solid_3_paths(hallux_variant=hallux_variant)
    )

    base_w = base_right - base_left
    base_h = base_bottom - base_top

    # Ribbon overlay: clipPath rect.  Two ways to specify the band:
    #   1. `band_top_local` (glyph-local coords) — used by the mark
    #      (the band sits at the lower edge of the wings).
    #   2. `band_top_lockup` (lockup coords) — used by the lockup
    #      (the band matches the wordmark band so the amber strip
    #      is continuous across both halves).
    # Exactly one of these should be supplied by the caller.  When
    # neither is supplied, default to the lockup rule.
    # Defaults use `GLYPH_BAND_TOP_LOCAL` / `GLYPH_BAND_HEIGHT`
    # (single source of truth, top of this file).  The lockup's
    # cap-derived math in `render_wordmark()` lands at the same
    # glyph-local position.
    if band_height is None:
        if band_top_local is None and band_top_lockup is None:
            band_height = GLYPH_BAND_HEIGHT
        elif band_top_local is not None:
            # Mark rule uses the same height as the lockup ribbon so
            # both artifacts render the same band thickness.
            band_height = GLYPH_BAND_HEIGHT
        else:
            # Lockup rule: band_height comes from the wordmark math
            # (cap * 0.25).
            band_height = 168 * 0.72 * 0.25
    if band_top_local is not None:
        pass  # glyph-local override already supplied
    elif band_top_lockup is None:
        band_top_local = GLYPH_BAND_TOP_LOCAL
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
    """Render TALARIA with a gold top and a thin amber bottom band.

    Band math: a thin strip in the lower portion of the cap-height,
    anchored to match the lockup ribbon position.  Constants are
    taken straight from `GLYPH_BAND_TOP_LOCAL` /
    `GLYPH_BAND_HEIGHT` at the top of this file (single source of
    truth) so the lockup ribbon, the wordmark band, and the mark
    ribbon all sit on the same y line.

    Note: the wordmark uses the cap-derived position
    (band_top = baseline - cap + cap*0.72 = 157.13 in lockup
    coords, glyph-local 150.13) while the mark uses the explicit
    `GLYPH_BAND_TOP_LOCAL = 150`.  The 0.13-unit difference is
    below visual resolution and intentional — keeps the wordmark
    math self-contained (anchored to the cap, no glyph coupling).
    See `references/band-rule-lockup-vs-mark.md` for the recipe.
    """
    x = origin_x
    y = origin_y
    cap = WORDMARK_FONT * 0.72
    band_top = y - cap + cap * 0.72
    band_height = GLYPH_BAND_HEIGHT
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


def render_lockup(*, hallux_variant: str = "a-outer-small") -> str:
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
        hallux_variant=hallux_variant,
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


def render_mark_only(*, hallux_variant: str = "a-outer-small") -> str:
    """Square mark-only: winged-sandal solid_3 glyph on a transparent
    background, centred with padding.

    Band rule: the mark uses the SAME thin-strip band geometry as
    the lockup ribbon (`GLYPH_BAND_TOP_LOCAL` / `GLYPH_BAND_HEIGHT`).
    The amber ribbon lands at the lower edge of the wings,
    catching the lower feathers + the very top of the base bar.
    """
    pad = 48
    w = GLYPH_W + 2 * pad
    h = GLYPH_H + 2 * pad
    glyph = render_glyph(
        origin_x=pad,
        origin_y=pad,
        band_top_local=GLYPH_BAND_TOP_LOCAL,
        band_height=GLYPH_BAND_HEIGHT,
        hallux_variant=hallux_variant,
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {w} {h}" role="img" aria-labelledby="title">\n'
        f'  <title id="title">Talaria mark</title>\n'
        f'{glyph}\n'
        f'</svg>\n'
    )


def main() -> None:
    """CLI entry point.

    Default behaviour (no flags): render the production
    `logo.svg` and `logo-mark.svg` with the production
    hallux variant `"a-outer-small"` — a small outer toe-knob
    on each end of the base bar (the chosen production
    geometry).

    `--variant NAME` selects an alternate hallux variant for
    both outputs:
      a-outer-small   — PRODUCTION default.  Small outer
        toe-knob, ~12u × ~7u past the outer end of the base
        bar.  Amber band stays clean.
      none            — No hallux (the pre-2026-07-04
        production geometry, kept for comparison only).
      b-outer-large   — Larger outer toe-blob, ~22u × ~14u,
        dipping 4u below the sole.  Reference only.
      c-inner-medial  — Inner hallux toward the centreline.
        Reference only.

    `--drafts` writes the four comparison sets
    (`none` + `a-outer-small` + `b-outer-large` +
    `c-inner-medial`) under `assets/drafts/` for inspection.
    The canonical `logo.svg` / `logo-mark.svg` are NOT
    touched in drafts mode.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Render the Talaria winged-sandal logo SVGs."
    )
    parser.add_argument(
        "--variant",
        choices=["a-outer-small", "none", "b-outer-large", "c-inner-medial"],
        default="a-outer-small",
        help="Hallux variant to apply to the glyph (default: a-outer-small, the production geometry).",
    )
    parser.add_argument(
        "--drafts",
        action="store_true",
        help=(
            "Write the four comparison sets (none + a + b + c) "
            "under assets/drafts/ for operator inspection.  The "
            "canonical logo.svg / logo-mark.svg are NOT touched."
        ),
    )
    args = parser.parse_args()

    out_dir = Path(__file__).parent

    if args.drafts:
        drafts_dir = out_dir / "drafts"
        drafts_dir.mkdir(exist_ok=True)
        for label in ["none", "a-outer-small", "b-outer-large", "c-inner-medial"]:
            suffix = "" if label == "none" else f"-{label}"
            (drafts_dir / f"logo{suffix}.svg").write_text(
                render_lockup(hallux_variant=label)
            )
            (drafts_dir / f"logo-mark{suffix}.svg").write_text(
                render_mark_only(hallux_variant=label)
            )
        print(f"lockup: {LOCKUP_W} x {LOCKUP_H}")
        print(f"wrote 4 lockup+mark pairs under {drafts_dir}/")
        return

    (out_dir / "logo.svg").write_text(render_lockup(hallux_variant=args.variant))
    (out_dir / "logo-mark.svg").write_text(
        render_mark_only(hallux_variant=args.variant)
    )

    print(f"lockup: {LOCKUP_W} x {LOCKUP_H}")
    print(
        f"wrote logo.svg, logo-mark.svg (hallux_variant={args.variant!r})"
    )


if __name__ == "__main__":
    main()
