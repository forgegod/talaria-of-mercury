"""Generate the vision-capability benchmark fixture images.

Each image has a deterministic ground-truth answer that the
``test_vision_capability`` benchmark asserts on. Run this script
to (re)generate the PNGs::

    uv run python tests/fixtures/generate_vision_fixtures.py

The images are checked into the repo so the benchmark does not
depend on Pillow at test-collection time — only the ground-truth
metadata (``VISION_FIXTURES`` in ``test_diagnose_aux_models.py``)
is read.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

#: Output directory for the fixture PNGs (this directory itself).
OUT = Path(__file__).resolve().parent

#: Canvas size for every fixture (landscape, high enough resolution
#: for vision models to read small text).
W, H = 800, 500


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Try a few common font paths; fall back to the default bitmap."""
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _center_text(
    draw: ImageDraw.ImageDraw, text: str, y: int, size: int, fill: str,
) -> None:
    font = _font(size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, y), text, font=font, fill=fill)


def fixture_count_grid() -> dict[str, str]:
    """Image 1: a grid of coloured shapes with a count question.

    Ground truth: 10 circles, 4 red.
    """
    img = Image.new("RGB", (W, H), "#1a1a2e")
    draw = ImageDraw.Draw(img)
    _center_text(draw, "Count the circles. How many are red?", 20, 24, "#e0e0e0")

    circles = [
        (150, 200, "#e74c3c"),  # red
        (300, 200, "#3498db"),  # blue
        (450, 200, "#e74c3c"),  # red
        (600, 200, "#2ecc71"),  # green
        (150, 350, "#f39c12"),  # orange
        (300, 350, "#e74c3c"),  # red
        (450, 350, "#9b59b6"),  # purple
        (600, 350, "#3498db"),  # blue
        (375, 460, "#2ecc71"),  # green
        (375, 120, "#e74c3c"),  # red  (this one is subtle — top center)
    ]
    for cx, cy, color in circles:
        r = 35
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color, outline="#ffffff", width=2)

    # Squares as distractors.
    for sx, sy in [(50, 100), (740, 100), (50, 440), (740, 440)]:
        draw.rectangle([sx - 20, sy - 20, sx + 20, sy + 20], fill="#7f8c8d")

    img.save(OUT / "count_grid.png")
    return {
        "total_circles": "10",
        "red_circles": "4",
        "blue_circles": "2",
        "green_circles": "2",
        "orange_circles": "1",
        "purple_circles": "1",
        "total_squares": "4",
        "description": "10 circles (4 red, 2 blue, 2 green, 1 orange, 1 purple), 4 grey squares",
    }


def fixture_text_extract() -> dict[str, str]:
    """Image 2: an error-message card the model must read verbatim.

    Ground truth: the error code is ERR_4042 and the module is
    ``agent.compression``.
    """
    img = Image.new("RGB", (W, H), "#0d1117")
    draw = ImageDraw.Draw(img)

    # Error card border.
    draw.rounded_rectangle([50, 50, W - 50, H - 50], radius=16, outline="#f85149", width=3)

    _center_text(draw, "RUNTIME ERROR", 80, 28, "#f85149")
    _center_text(draw, "Error Code: ERR_4042", 150, 36, "#e6edf3")
    _center_text(draw, "Module: agent.compression", 210, 28, "#e6edf3")
    _center_text(draw, "Detail: Context window exceeded during merge.", 270, 22, "#8b949e")
    _center_text(draw, "Detail: 3 retries failed. Last attempt at 2026-07-05T14:23:01Z.", 310, 22, "#8b949e")

    # A table-like structure the model must parse.
    _center_text(draw, "Retry | Tokens | Status", 370, 20, "#d2a8ff")
    _center_text(draw, "  1   | 28512  | truncated", 400, 18, "#e6edf3")
    _center_text(draw, "  2   | 31044  | truncated", 425, 18, "#e6edf3")
    _center_text(draw, "  3   | 32768  | failed", 450, 18, "#f85149")

    img.save(OUT / "error_card.png")
    return {
        "error_code": "ERR_4042",
        "module": "agent.compression",
        "retry_count": "3",
        "last_status": "failed",
        "description": "Runtime error card with code ERR_4042 in agent.compression, 3 retries",
    }


def fixture_spatial_reasoning() -> dict[str, str]:
    """Image 3: a spatial layout the model must reason about.

    Ground truth: the arrow points to the box labeled "B", which
    is to the right of box "A" and above box "C".
    """
    img = Image.new("RGB", (W, H), "#f0f0f0")
    draw = ImageDraw.Draw(img)
    _center_text(draw, "Which box does the arrow point to?", 20, 24, "#333333")

    # Three labelled boxes in a layout.
    boxes = {
        "A": (120, 250, "#3498db"),
        "B": (400, 180, "#e74c3c"),
        "C": (400, 350, "#2ecc71"),
        "D": (680, 250, "#f39c12"),
    }
    for label, (cx, cy, color) in boxes.items():
        x0, y0, x1, y1 = cx - 50, cy - 40, cx + 50, cy + 40
        draw.rectangle([x0, y0, x1, y1], fill=color, outline="#333333", width=2)
        font = _font(32)
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((cx - tw // 2, cy - th // 2 - 4), label, font=font, fill="#ffffff")

    # Arrow pointing from left toward box B.
    arrow_start = (200, 210)
    arrow_end = (345, 195)
    draw.line([arrow_start, arrow_end], fill="#e74c3c", width=4)
    # Arrowhead.
    angle = math.atan2(arrow_end[1] - arrow_start[1], arrow_end[0] - arrow_start[0])
    head_len = 18
    for da in (2.6, -2.6):
        hx = arrow_end[0] - head_len * math.cos(angle + da)
        hy = arrow_end[1] - head_len * math.sin(angle + da)
        draw.line([arrow_end, (hx, hy)], fill="#e74c3c", width=4)

    img.save(OUT / "spatial_arrow.png")
    return {
        "pointed_box": "B",
        "box_a_position": "left",
        "box_b_position": "center-top",
        "box_c_position": "center-bottom",
        "box_d_position": "right",
        "description": "Arrow points to box B; A is left, C is below B, D is right",
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    results = {
        "count_grid": fixture_count_grid(),
        "error_card": fixture_text_extract(),
        "spatial_arrow": fixture_spatial_reasoning(),
    }

    # The logo fixture is a real brand asset, not a Pillow-generated image.
    # It is copied from assets/ (see tests/AGENTS.md). Record its ground
    # truth so the metadata file stays the single source for all fixtures.
    logo_dir = OUT / "logo"
    logo_dir.mkdir(parents=True, exist_ok=True)
    results["logo"] = {
        "wordmark": "TALARIA",
        "icon": "winged sandals / wings",
        "dominant_colour": "gold (#ffc72c)",
        "secondary_colour": "amber (#f9a23a)",
        "image_used": "logo/logo-512.png",
        "source_svg": "logo/logo.svg",
        "description": (
            "Talaria brand lockup: winged-sandal glyph + TALARIA wordmark. "
            "Gold primary fill, amber bicolour band. The SVG is the source "
            "of truth but vision models cannot read SVG, so the benchmark "
            "sends the rasterised logo-512.png."
        ),
    }

    meta_path = OUT / "ground_truth.json"
    meta_path.write_text(json.dumps(results, indent=2))
    print(f"Wrote {len(results)} fixtures to {OUT}")
    print(f"Ground truth: {meta_path}")


if __name__ == "__main__":
    main()
