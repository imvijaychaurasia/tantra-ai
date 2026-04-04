"""
tantra-media — Image generation
तंत्र  ·  Styled tech-slide images for YouTube scenes

Generates 1920×1080 dark-themed slide images for each scene.
No GPU required — pure Pillow (CPU).

Design language:
  - Dark gradient background (#0a0e1a → #1a1f35, tech-dark)
  - Scene type badge (HOOK / CONTENT / CTA / OUTRO)
  - Large scene title (top third)
  - Visual description as body text (middle)
  - On-screen text highlight box (if present)
  - Scene number indicator
  - Tantra AI / तंत्र watermark (bottom right)
  - Cyan accent lines for tech feel

The visual_prompt is rendered as on-screen descriptive text so the
viewer can understand the intended b-roll even before real footage is cut.
"""
from __future__ import annotations

import logging
import os
import textwrap
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("tantra-media.imagegen")

# ── Constants ──────────────────────────────────────────────────────────────
W, H = 1920, 1080

# Colour palette — dark tech theme
BG_TOP    = (10, 14, 26)      # #0a0e1a
BG_BOT    = (26, 31, 53)      # #1a1f35
ACCENT    = (0, 210, 255)     # cyan
WHITE     = (255, 255, 255)
MUTED     = (160, 170, 190)
DIM       = (80, 90, 110)
BADGE_COLORS = {
    "hook":    (255, 80,  80),   # red
    "content": (0, 210, 255),    # cyan
    "cta":     (80, 255, 130),   # green
    "outro":   (180, 80, 255),   # purple
}

# Font paths — DejaVu comes with the Docker image
FONT_REGULAR = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
FONT_BOLD    = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_FALLBACK_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _load_font(bold: bool = False, size: int = 36) -> ImageFont.FreeTypeFont:
    paths = [FONT_BOLD if bold else FONT_REGULAR,
             FONT_FALLBACK_BOLD if bold else FONT_FALLBACK]
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_gradient_bg(draw: ImageDraw.ImageDraw) -> None:
    """Fill the background with a vertical dark gradient."""
    for y in range(H):
        t = y / H
        r = int(BG_TOP[0] + t * (BG_BOT[0] - BG_TOP[0]))
        g = int(BG_TOP[1] + t * (BG_BOT[1] - BG_TOP[1]))
        b = int(BG_TOP[2] + t * (BG_BOT[2] - BG_TOP[2]))
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def _draw_accent_lines(draw: ImageDraw.ImageDraw) -> None:
    """Draw subtle horizontal accent lines at top and bottom."""
    draw.rectangle([0, 0, W, 4], fill=ACCENT)
    draw.rectangle([0, H - 4, W, H], fill=ACCENT)
    # Vertical thin left stripe
    draw.rectangle([0, 0, 3, H], fill=(*ACCENT, 80))


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""
    dummy_img = Image.new("RGB", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)

    for word in words:
        test = f"{current} {word}".strip()
        bbox = dummy_draw.textbbox((0, 0), test, font=font)
        if bbox[2] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    color: tuple,
    max_width: int,
    line_spacing: int = 8,
) -> int:
    """Draw a wrapped text block. Returns y position after last line."""
    lines = _wrap_text(text, font, max_width)
    dummy_img = Image.new("RGB", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    for line in lines:
        draw.text((x, y), line, font=font, fill=color)
        bbox = dummy_draw.textbbox((0, 0), line, font=font)
        y += (bbox[3] - bbox[1]) + line_spacing
    return y


def generate_scene_image(
    scene: dict,
    output_path: Path,
    video_title: str = "",
    scene_index: int = 0,
    total_scenes: int = 0,
) -> None:
    """
    Generate a 1920×1080 styled slide image for a YouTube script scene.

    Args:
        scene:        Scene dict from script JSON (id, type, narration, visual_prompt, etc.)
        output_path:  Where to write the PNG file.
        video_title:  Overall video title for watermark context.
        scene_index:  0-based index (for scene counter).
        total_scenes: Total number of scenes (for progress indicator).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (W, H), BG_TOP)
    draw = ImageDraw.Draw(img)

    # ── Background ─────────────────────────────────────────────────────────
    _draw_gradient_bg(draw)
    _draw_accent_lines(draw)

    # ── Scene type badge ───────────────────────────────────────────────────
    scene_type = scene.get("type", "content").lower()
    badge_color = BADGE_COLORS.get(scene_type, ACCENT)
    badge_text = scene_type.upper()
    badge_font = _load_font(bold=True, size=28)
    badge_padding = 16
    dummy_img = Image.new("RGB", (1, 1))
    dummy_draw = ImageDraw.Draw(dummy_img)
    bbox = dummy_draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_w = bbox[2] - bbox[0] + badge_padding * 2
    badge_h = bbox[3] - bbox[1] + badge_padding
    badge_x, badge_y = 80, 80
    draw.rounded_rectangle(
        [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
        radius=6, fill=badge_color,
    )
    draw.text((badge_x + badge_padding, badge_y + badge_padding // 2),
              badge_text, font=badge_font, fill=(10, 10, 10))

    # ── Scene counter ──────────────────────────────────────────────────────
    if total_scenes:
        counter_font = _load_font(bold=False, size=28)
        counter_text = f"{scene_index + 1} / {total_scenes}"
        draw.text((W - 160, 88), counter_text, font=counter_font, fill=DIM)

    # ── Main title / visual prompt (large) ─────────────────────────────────
    visual_prompt = scene.get("visual_prompt", "")
    on_screen = scene.get("on_screen_text", "")
    narration = scene.get("narration", "")

    # Large visual description in the upper-centre area
    title_font = _load_font(bold=True, size=56)
    body_font = _load_font(bold=False, size=38)
    small_font = _load_font(bold=False, size=30)

    MARGIN = 100
    content_width = W - MARGIN * 2

    y = 200

    # Visual prompt as hero text
    if visual_prompt:
        y = _draw_text_block(
            draw, visual_prompt, title_font,
            MARGIN, y, WHITE, content_width, line_spacing=12,
        ) + 40

    # Horizontal divider
    draw.rectangle([MARGIN, y, W - MARGIN, y + 2], fill=(*ACCENT, 120))
    y += 30

    # Narration excerpt (first sentence, as a subtitle hint)
    if narration:
        first_sentence = narration.split(".")[0].strip()
        if len(first_sentence) > 120:
            first_sentence = first_sentence[:117] + "…"
        y = _draw_text_block(
            draw, f'"{first_sentence}"', body_font,
            MARGIN, y, MUTED, content_width, line_spacing=10,
        ) + 30

    # On-screen text highlight box
    if on_screen:
        box_padding = 20
        box_font = _load_font(bold=True, size=44)
        box_lines = _wrap_text(on_screen, box_font, content_width - box_padding * 2)
        if box_lines:
            box_text = "\n".join(box_lines)
            # Measure box
            dummy_draw2 = ImageDraw.Draw(Image.new("RGB", (1, 1)))
            line_bboxes = [dummy_draw2.textbbox((0, 0), ln, font=box_font) for ln in box_lines]
            box_h = sum(bb[3] - bb[1] + 8 for bb in line_bboxes) + box_padding * 2
            draw.rounded_rectangle(
                [MARGIN, y, W - MARGIN, y + box_h],
                radius=10, fill=(0, 40, 60),
                outline=ACCENT, width=2,
            )
            ty = y + box_padding
            for line, bb in zip(box_lines, line_bboxes):
                draw.text((MARGIN + box_padding, ty), line, font=box_font, fill=WHITE)
                ty += (bb[3] - bb[1]) + 8
            y = ty + box_padding + 20

    # ── Bottom watermark ───────────────────────────────────────────────────
    wm_font = _load_font(bold=False, size=26)
    wm_text = "Tantra AI  ·  तंत्र"
    draw.text((W - 300, H - 60), wm_text, font=wm_font, fill=DIM)

    # Save
    img.save(str(output_path), "PNG", optimize=True)
    log.info("Slide ✓ %s", output_path.name)


def generate_thumbnail(
    script: dict,
    output_path: Path,
) -> None:
    """
    Generate a YouTube thumbnail (1280×720) from the script's thumbnail_prompt.
    Uses the same dark-tech slide style, optimised for thumbnail readability.
    """
    TW, TH = 1280, 720
    output_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (TW, TH), BG_TOP)
    draw = ImageDraw.Draw(img)

    # Gradient
    for y in range(TH):
        t = y / TH
        r = int(BG_TOP[0] + t * (BG_BOT[0] - BG_TOP[0]))
        g = int(BG_TOP[1] + t * (BG_BOT[1] - BG_TOP[1]))
        b = int(BG_TOP[2] + t * (BG_BOT[2] - BG_TOP[2]))
        draw.line([(0, y), (TW, y)], fill=(r, g, b))

    # Accent lines
    draw.rectangle([0, 0, TW, 6], fill=ACCENT)
    draw.rectangle([0, TH - 6, TW, TH], fill=ACCENT)

    MARGIN = 80

    # Title — very large
    title = script.get("title", "Tantra AI")
    title_font = _load_font(bold=True, size=72)
    body_font = _load_font(bold=False, size=38)

    y = 100
    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    title_lines = _wrap_text(title, title_font, TW - MARGIN * 2)
    for line in title_lines:
        draw.text((MARGIN, y), line, font=title_font, fill=WHITE)
        bbox = dummy_draw.textbbox((0, 0), line, font=title_font)
        y += (bbox[3] - bbox[1]) + 10
    y += 20

    # Thumbnail concept as subtitle
    concept = script.get("thumbnail_concept") or script.get("thumbnail_prompt", "")
    if concept and len(concept) > 80:
        concept = concept[:77] + "…"
    if concept:
        draw.text((MARGIN, y), concept, font=body_font, fill=MUTED)

    # Tantra branding bottom right
    wm_font = _load_font(bold=True, size=32)
    draw.text((TW - 260, TH - 70), "तंत्र · TANTRA AI", font=wm_font, fill=ACCENT)

    img.save(str(output_path), "PNG", optimize=True)
    log.info("Thumbnail ✓ %s", output_path.name)
