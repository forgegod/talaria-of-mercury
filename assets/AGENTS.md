# assets

## Purpose

Brand assets for the Talaria project — a puristic ASCII-source flat-mark
logo and its derived sizes.

## Ownership

- The glyph is authored as ASCII art (`ASCII_GLYPH` in `build_logo.py`)
  and baked into vector `<rect>` primitives at build time. The SVG output
  is font-independent and renders identically on every host.
- Source SVGs are the source of truth: `logo.svg` (lock-up),
  `logo-mark.svg` (square, no wordmark), `logo-inverse.svg` (white fill
  for dark backgrounds).
- `build_logo.py` regenerates the SVGs from the ASCII source.
- `build_logo_grid.py` is the legacy standalone grid-glyph renderer; it
  has been superseded by `build_logo.py` and can be deleted on the next
  clean-up pass.

## Local Contracts

- The logo is single-color (default black, optionally white via
  `fill=`). No gradients, no glow, no background — visually consistent
  with the Nous Research mark on hermes-agent.nousresearch.com.
- The glyph silhouette is editable by changing `ASCII_GLYPH` in
  `build_logo.py` and re-running the script. Blank lines in the source
  carve visible gaps between sub-shapes.
- `ASCII_GLYPH` MUST be the only place where the glyph geometry is
  defined. Do not hand-edit the rendered SVG files.

## Work Guidance

- Regenerate SVGs: `python3 build_logo.py`.
- Regenerate PNGs:
  `inkscape logo.svg --export-type=png --export-filename=logo-NNN.png -w NNN -h NNN`
  (lockup aspect ratio is roughly 1044:366).
- To redesign the silhouette, edit `ASCII_GLYPH` directly. Block
  characters (`█`) fill cells; spaces are transparent; blank lines add
  vertical gaps.

## Verification

- Visually inspect `logo-1024.png` before commit: TALARIA must be fully
  visible (no right-edge clipping), glyph + wordmark vertically centred,
  silhouette single-colour, no decoration.
- After any ASCII source edit, regenerate and inspect both the lockup
  PNG and the standalone mark PNG.

## Child DOX Index

- `logo.svg`, `logo-256.png`, `logo-512.png`, `logo-1024.png` — primary lock-up.
- `logo-mark.svg`, `logo-mark-128.png`, `logo-mark-256.png` — square mark only.
- `logo-inverse.svg` — white-on-transparent for dark backgrounds.
- `build_logo.py` — SVG source generator (single source of truth).
- `build_logo_grid.py` — legacy grid-glyph renderer (superseded).