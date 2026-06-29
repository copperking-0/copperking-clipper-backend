"""
formatter.py — Portrait clip creation driven by layout config.
Supports facecam toggle and dynamic crop coords from web UI.
"""

import subprocess
from pathlib import Path
from config import load_layout, OUTPUT_WIDTH, OUTPUT_HEIGHT


def create_portrait_clip(input_path: str, output_path: str, layout: dict = None) -> bool:
    """
    Convert a landscape clip to 9:16 portrait format.

    Layout modes:
      - include_facecam=True:  Top = facecam, Bottom = gameplay
      - include_facecam=False: Full output = gameplay only (cropped & scaled)

    Args:
        input_path:  Path to raw landscape .mp4
        output_path: Destination path for portrait .mp4
        layout:      Layout dict (from config or web UI). Uses saved layout if None.
    """
    if layout is None:
        layout = load_layout()

    fc          = layout["facecam"]
    gp          = layout["gameplay"]
    include_fc  = layout.get("include_facecam", True)
    stream_h    = layout["stream_height"]
    gp_h_out    = layout.get("gameplay_height", 1320)
    fc_h_out    = OUTPUT_HEIGHT - gp_h_out

    if include_fc:
        filter_complex = (
            # Facecam: crop from source, zoom slightly, scale to top section
            f"[0:v]crop={fc['w']}:{fc['h']}:{fc['x']}:{fc['y']},"
            f"crop=iw*0.95:ih*0.95:(iw-iw*0.95)/2-30:(ih-ih*0.95)/2,"
            f"scale={OUTPUT_WIDTH}:{fc_h_out}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_WIDTH}:{fc_h_out}[face];"

            # Gameplay: crop center section, scale to bottom section
            f"[0:v]crop={gp['w']}:{stream_h}:{gp['x']}:0,"
            f"scale={OUTPUT_WIDTH}:{gp_h_out}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_WIDTH}:{gp_h_out}[game];"

            f"[face][game]vstack=inputs=2[out]"
        )
    else:
        # Gameplay only — fill entire 9:16 frame
        filter_complex = (
            f"[0:v]crop={gp['w']}:{stream_h}:{gp['x']}:0,"
            f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}[out]"
        )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        str(output_path)
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✅ Portrait clip saved: {Path(output_path).name}")
        return True
    else:
        print(f"❌ Portrait conversion failed:\n{result.stderr[-600:]}")
        return False
