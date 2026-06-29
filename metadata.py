"""
metadata.py — AI-powered metadata generation for clips.
Generates platform-specific titles for YouTube (SEO) and TikTok (viral).
Also handles burned-in title overlay via Pillow + ffmpeg.
"""

import json
import os
import tempfile
import subprocess
from pathlib import Path
from anthropic import Anthropic
from PIL import Image, ImageDraw, ImageFont
from config import CLAUDE_MODEL, find_font

client = Anthropic()


# ── Metadata Generation ────────────────────────────────────

def generate_metadata(transcript: str, reason: str, game: str = "gaming", used_titles: set = None) -> dict:
    """
    Generate platform-specific metadata for a highlight clip.

    Returns:
        {
            "burned_title":    str,   # short ALL CAPS overlay (max 5 words)
            "yt_title":        str,   # SEO YouTube title (max 70 chars)
            "yt_description":  str,   # YouTube description with keywords
            "yt_tags":         list,  # YouTube tags (15-20)
            "tiktok_title":    str,   # Punchy TikTok caption (max 100 chars)
            "tiktok_hashtags": list,  # TikTok hashtags (8-12)
            "hook":            str,   # First-line hook for captions
        }
    """
    used = list(used_titles) if used_titles else []

    prompt = f"""You are a viral content strategist for CopperKing, a gaming and reaction streamer on YouTube and TikTok.

Analyze this clip transcript and generate platform-optimized metadata.

Game: {game}
Clip summary: {reason}
Full transcript: {transcript}
Already used titles (DO NOT reuse): {used}

RULES:
- Every title MUST reference something SPECIFIC from the transcript
- No generic titles like "YOU WON'T BELIEVE THIS" or "NOBODY SAW THIS COMING"
- Match energy to what actually happened (funny=playful, hype=intense, scary=tense)
- burned_title must be punchable at a glance on a phone screen

YOUTUBE SEO RULES:
- yt_title: Include the game name, emotional hook, max 70 chars, 1 emoji OK
- yt_description: 2-3 sentences, first person as CopperKing, include game name and key moment naturally
- yt_tags: Mix of broad (gaming, clips) and specific (game name, moment type). 15-20 tags, no # symbol

TIKTOK VIRAL RULES:
- tiktok_title: Under 100 chars, punchy, starts with a hook word or action ("POV:", "When", "Bro", "I can't believe")
- tiktok_hashtags: 8-12 tags, mix of #gaming broad tags and niche streamer tags, no # symbol
- First 3 seconds matter — the hook should make someone stop scrolling

TONE GUIDE:
- Funny/chaotic moment → playful, self-aware ("I actually cannot" energy)
- Hype/clutch moment → intense, triumphant ("we were NOT dying today" energy)  
- Horror/scary moment → tense, reactive ("why would they DO that" energy)
- Fail moment → self-deprecating, relatable ("it's giving disaster" energy)

Return ONLY valid JSON with these exact keys:
{{
  "burned_title": "MAX 5 WORDS ALL CAPS",
  "yt_title": "YouTube Title With Emoji 🎮",
  "yt_description": "First person description as CopperKing...",
  "yt_tags": ["tag1", "tag2", "tag3"],
  "tiktok_title": "Punchy TikTok caption under 100 chars",
  "tiktok_hashtags": ["gaming", "copperking"],
  "hook": "One sentence that captures the moment for captions"
}}"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        text  = response.content[0].text
        clean = text.replace("```json", "").replace("```", "").strip()
        data  = json.loads(clean)

        # Enforce constraints
        data["burned_title"] = data.get("burned_title", "COPPER KING MOMENT")[:40].upper()
        data["yt_title"]     = data.get("yt_title", "CopperKing Clip")[:70]
        data["tiktok_title"] = data.get("tiktok_title", data["yt_title"])[:100]

        return data

    except Exception as e:
        print(f"⚠️  Metadata generation error: {e}")
        return _fallback_metadata(game)


def _fallback_metadata(game: str = "gaming") -> dict:
    return {
        "burned_title":    "COPPER KING MOMENT",
        "yt_title":        f"CopperKing {game.title()} Clip 🎮",
        "yt_description":  f"Check out this wild moment from my {game} stream!",
        "yt_tags":         ["gaming", "clips", "streamer", "copperking", game.lower()],
        "tiktok_title":    f"Bro this actually happened 💀 #{game}",
        "tiktok_hashtags": ["gaming", "clips", "streamer", "copperking", "streamclips", "viral", "fyp", game.lower()],
        "hook":            "You won't want to miss this moment."
    }


# ── Title Overlay ──────────────────────────────────────────

def burn_title_into_clip(input_path: str, output_path: str, title: str, position_y_ratio: float = 0.31) -> bool:
    """
    Burn a white rounded-rectangle title overlay onto a clip.

    Args:
        input_path:      Source video path
        output_path:     Output video path
        title:           Text to burn in (ALL CAPS recommended)
        position_y_ratio: Vertical position as ratio of height (default 0.31 = seam between facecam/gameplay)
    """
    # Probe dimensions
    probe = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(input_path)
    ], capture_output=True, text=True)

    if probe.returncode != 0:
        print("⚠️  Could not probe video dimensions")
        return False

    width, height = map(int, probe.stdout.strip().split(","))

    # Font sizing — scale with title length
    char_count = len(title)
    font_size  = max(42, int(72 - ((char_count / 35) * 30)))

    font_path = find_font()
    try:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # Measure and draw
    dummy  = Image.new("RGBA", (width, height))
    draw   = ImageDraw.Draw(dummy)
    bbox   = draw.textbbox((0, 0), title, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    padding = 12
    radius  = 14
    box_w   = text_w + (padding * 2)
    box_h   = text_h + (padding * 2)
    box_x   = (width - box_w) // 2
    box_y   = int(height * position_y_ratio) - (box_h // 2)
    text_x  = box_x + (box_w - text_w) // 2
    text_y  = box_y + (box_h - text_h) // 2 - (bbox[1] // 2)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        [box_x, box_y, box_x + box_w, box_y + box_h],
        radius=radius, fill=(255, 255, 255, 255)
    )
    draw.text((text_x, text_y), title, font=font, fill=(0, 0, 0, 255))

    overlay_path = tempfile.mktemp(suffix=".png")
    overlay.save(overlay_path)

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-i", overlay_path,
        "-filter_complex", "overlay=0:0",
        "-c:a", "copy",
        str(output_path)
    ], capture_output=True)

    os.remove(overlay_path)

    if result.returncode != 0:
        print(f"⚠️  Title burn failed: {result.stderr.decode()[-300:]}")
        return False

    print(f"✍️  Title burned: \"{title}\" (font: {font_size}px)")
    return True
