# assets

## Purpose

Brand assets for the Talaria project — a coloured winged-sandal
lockup (horizontal) and a square standalone mark. Both pair a
vector-path glyph (a 3-feather winged sandal: 3 smooth
cubic-Bézier feathers per side flowing into a single base bar)
with a serif wordmark. The bicolour design uses gold on the
upper portion and amber on the lower portion of both the glyph
and the wordmark, with the glyph band and the wordmark band on
the same horizontal line and the glyph base bar bottom sitting
on the wordmark baseline. The lockup renders on a transparent
background so it can be placed on any surface.

## Ownership

- The glyph is built from vector `<path>` primitives in
  `build_logo.py`.  Path data is generated programmatically from
  the geometry constants at the top of the file.
- The wordmark uses an SVG `<text>` element (Georgia / Times
  New Roman serif fallback) with a clipPath-based bicolour fill
  (gold top, amber bottom band) — no font is bundled.
- Source SVGs are the source of truth: `logo.svg` (lockup,
  transparent bg) and `logo-mark.svg` (square mark only,
  transparent bg).  Both render with a transparent background
  so the lockup can be placed on any surface.  Raster PNGs exist
  only for the lockup (`logo-256.png`, `logo-512.png`,
  `logo-1024.png`) — the mark is SVG-only.
- `build_logo.py` regenerates the SVGs from the geometry
  constants.
- `build_logo.py` MUST be the only place where the glyph
  geometry is defined.  Do not hand-edit the rendered SVG files.

## Local Contracts

- **Palette** — the design uses the Hermes Agent palette: gold
  `#ffc72c` primary fill; amber `#f9a23a` bottom band.  All
  renders use a transparent background (no navy, no other
  background colour) so the lockup can be placed on any surface.
- **Bicolour** — the production glyph AND the wordmark both
  carry a thin amber bicolour band (gold on top, amber on the
  bottom).  The wordmark uses gold-on-top with a clipPath-painted
  amber strip of height `GLYPH_BAND_HEIGHT = 30` glyph-local units
  (= `cap * 0.25`) anchored at `band_top = cap * 0.72` from the
  cap top.  The glyph achieves the same bicolour via a clipPath
  overlay: amber copies of each wing `<path>` are painted ON TOP
  of the gold wings and clipped to the same glyph-local band
  region (`GLYPH_BAND_TOP_LOCAL = 150`, `GLYPH_BAND_HEIGHT = 30`).
  The band sits at the lower edge of the wings, catching the
  lower feathers + the top of the base bar.  The clipPath rect
  matches the band rect — never a full-viewBox clip — so the
  gold wings remain visible above the strip.
- **Geometry** — production glyph is the `solid_3` winged-sandal:
  two symmetric half-silhouettes (left + right) that meet at the
  centreline x=120.  Each half is a SINGLE closed `<path>` whose
  curves form 3 smooth cubic-Bézier feathers flowing down to the
  BASE BAR TOP (y_local=170), then continuing along the base bar
  bottom (y_local=184) and back up the centreline.  The wings +
  base bar are ONE integral piece — the base bar is part of the
  wing path, not a separate `<rect>`.  There is NO pedestal
  between the wings and the base bar — the lower feathers taper
  smoothly into the bar with no visible flat edge or seam.  Path
  data is in `_solid_3_paths()` (also returns the base bar
  geometry) in `build_logo.py`.  The standalone mark uses the
  same path data and the same combined-silhouette structure.
- **Band rule — lockup vs. mark (same geometry)** — the two
  artifacts share the SAME thin band geometry:
    - Band height: `GLYPH_BAND_HEIGHT = 30` glyph-local units
      (= `cap * 0.25`, ~14% of glyph height).
    - Band top in glyph-local coords: `GLYPH_BAND_TOP_LOCAL = 150`
      (just below the lower-feather inner endpoint, catching the
      outer wing tips + the top of the base bar).
    - The lockup derives this from cap-derived math:
      `band_top_lockup = word_baseline_y - cap + cap*0.72`
      (157.13 in lockup coords; = glyph-local 150.13 with the
      7-unit glyph origin offset).  `band_height = 30`.
    - The mark passes `band_top_local = GLYPH_BAND_TOP_LOCAL` and
      `band_height = GLYPH_BAND_HEIGHT` directly to
      `render_glyph()`.
    - Both lockup ribbon and wordmark band sit on the same y line
      (lockup y=157..187).  The cap-derived position of 157.13 is
      rounded to 150 glyph-local for the mark rule (the 0.13-unit
      difference is below visual resolution).
    - Do not increase the band height or move it higher — earlier
      wider bands put a strip floating through the wing middle.
      Earlier narrower bands weren't tried yet; the user accepted
      "smaller part of the glyph" overlap.
    - See `references/band-rule-lockup-vs-mark.md` for the recipe
      and the explicit failure modes (banner-through-middle,
      misaligned halos, full-viewBox clip merge).
- **Alignment** — in the lockup, the glyph is translated so the
  base bar bottom (local y=184) sits exactly on the wordmark
  baseline.  Position is via `_glyph_origin_y_for_baseline()`.
- **Wordmark** — Georgia / Times New Roman serif, `WORDMARK_FONT
  = 168`, letter-spacing 12, weight 700, fully visible (no
  right-edge clipping) in every render.

## Work Guidance

- Regenerate SVGs (production lockup + mark):
  `python3 build_logo.py`.
- Regenerate lockup PNGs:
  `convert -density 200 -background none logo.svg -resize 256x logo-256.png`
  (repeat for `512x` and `1024x`).
  Or via Inkscape: `inkscape logo.svg --export-type=png
  --export-filename=logo-NNN.png -w NNN -h NNN` (lockup aspect
  ratio is 1548:284).
- To redesign the glyph, edit the path data in
  `_solid_3_paths()` (and/or the band constants in `render_glyph`)
  in `build_logo.py`, then re-run the script.

## Verification

- Visually inspect `logo-1024.png` before commit: TALARIA must
  be fully visible (no right-edge clipping), glyph + wordmark
  vertically centred, three feathers per side clearly readable,
  the GLYPH MUST carry the THIN BICOLOUR RIBBON (gold upper
  wings, amber strip cutting across the lower edge of the wings
  and the top of the base bar), the amber strip on the glyph MUST
  sit on the SAME y line as the amber band on TALARIA
  (lockup y=157..187), the base bar bottom must sit on the
  wordmark baseline.
- Open `logo-mark.svg` in any SVG viewer to confirm: glyph reads
  as a winged-sandal silhouette on its own (without the wordmark),
  with gold upper wings, a thin amber bicolour ribbon at the
  lower edge of the wings (glyph-local y=150..180, the same
  geometry as the lockup ribbon), and the base bar at the bottom
  of the bbox.  The amber strip MUST sit at the lower edge of
  the wings — NOT through the middle of the wing area (banner
  failure mode).

## Child DOX Index

- `logo.svg`, `logo-256.png`, `logo-512.png`, `logo-1024.png` —
  primary lockup (transparent background).  Glyph is the
  `solid_3` winged-sandal: a single closed `<path>` per side
  that flows 3 feathers down into the base bar as one integral
  piece.  Gold wings + amber bicolour ribbon overlay (clipPath)
  cutting across the lower portion of the wings and the base
  bar.  The ribbon aligns with the band on TALARIA; the base
  bar bottom sits on the wordmark baseline.
- `logo-mark.svg` — square mark only (transparent background,
  SVG-only — no raster children).  Uses the same `solid_3`
  winged-sandal glyph with the bottom-36% band rule (no
  wordmark to anchor the strip).
- `build_logo.py` — SVG source generator (single source of
  truth).  Generates `logo.svg` and `logo-mark.svg` from the
  geometry constants.
