"""
config.py — Central configuration for the clipper pipeline.
All hardcoded values live here. Web UI writes to layout.json at runtime.
"""

import os
import json
import tempfile
import platform
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────
TEMP_DIR       = Path(tempfile.gettempdir()) / "ck_clipper"
BUFFER_DIR     = TEMP_DIR / "buffer"
TEMP_AUDIO     = TEMP_DIR / "chunk.wav"
CLIPS_DIR      = Path(os.environ.get("CLIPS_DIR", Path.home() / "CopperKing" / "clips"))
LAYOUT_CONFIG  = TEMP_DIR / "layout.json"

TEMP_DIR.mkdir(parents=True, exist_ok=True)
BUFFER_DIR.mkdir(parents=True, exist_ok=True)

# ── Clip Settings ──────────────────────────────────────────
CLIP_SCORE_THRESHOLD = 8
CHUNK_DURATION       = 60   # seconds of audio analyzed per cycle
CLIP_DURATION        = 60   # seconds saved per highlight clip

# ── Output Format ──────────────────────────────────────────
OUTPUT_WIDTH   = 1080
OUTPUT_HEIGHT  = 1920

# ── Default Layout (overridden by layout.json from web UI) ─
DEFAULT_LAYOUT = {
    "stream_width":   1920,
    "stream_height":  1080,
    "facecam": {
        "x": 1298,
        "y": 730,
        "w": 622,
        "h": 350
    },
    "gameplay": {
        "x": 480,
        "w": 960
    },
    "include_facecam":     True,
    "gameplay_height":     1320,   # px in output (rest goes to facecam)
}

# ── Scoring ────────────────────────────────────────────────
WHISPER_MODEL       = "small"
WHISPER_DEVICE      = "cpu"
WHISPER_COMPUTE     = "int8"
CLAUDE_MODEL        = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS   = 500


# ── Font Discovery (cross-platform) ───────────────────────
def find_font():
    """Find a usable bold/impact font across Windows, macOS, Linux."""
    candidates = []

    if platform.system() == "Windows":
        candidates = [
            r"C:\Windows\Fonts\impact.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ]
    elif platform.system() == "Darwin":
        candidates = [
            "/Library/Fonts/Impact.ttf",
            "/System/Library/Fonts/Supplemental/Impact.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    else:  # Linux
        candidates = [
            "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            return path

    return None  # Pillow will fall back to default


# ── Layout Loader ──────────────────────────────────────────
def load_layout():
    """Load layout from file if set by web UI, else use defaults."""
    if LAYOUT_CONFIG.exists():
        try:
            with open(LAYOUT_CONFIG) as f:
                saved = json.load(f)
                merged = {**DEFAULT_LAYOUT, **saved}
                merged["facecam"]   = {**DEFAULT_LAYOUT["facecam"],   **saved.get("facecam", {})}
                merged["gameplay"]  = {**DEFAULT_LAYOUT["gameplay"],  **saved.get("gameplay", {})}
                return merged
        except Exception:
            pass
    return DEFAULT_LAYOUT.copy()


def save_layout(layout: dict):
    """Save layout config from web UI drag & drop."""
    with open(LAYOUT_CONFIG, "w") as f:
        json.dump(layout, f, indent=2)
