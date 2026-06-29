"""
clipper.py — CopperKing highlight clipper (v2).

Two modes:
  stream  — Monitor a live stream URL (Twitch, YouTube, Kick, TikTok, custom RTMP)
  upload  — Process a local or uploaded video file

Usage:
  python clipper.py stream <url>
  python clipper.py upload <file_path> [--game "Dead by Daylight"]
  python clipper.py stream --interactive     # prompts for URL
"""

import os
import sys
import json
import time
import argparse
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from faster_whisper import WhisperModel
from anthropic import Anthropic

from config import (
    CLIPS_DIR, TEMP_AUDIO, BUFFER_DIR,
    CHUNK_DURATION, CLIP_DURATION, CLIP_SCORE_THRESHOLD,
    WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE, CLAUDE_MODEL,
    load_layout
)
from formatter import create_portrait_clip
from metadata import generate_metadata, burn_title_into_clip
from audio_analyzer import analyze_audio

# ── Init ───────────────────────────────────────────────────
print("🎙️  Loading Whisper model...")
whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
client  = Anthropic()
print("✅ Models ready!")

used_titles: set = set()


# ── Stream Buffer ──────────────────────────────────────────

class StreamBuffer:
    """Rolling HLS buffer — keeps the last 60s of a live stream in memory."""

    def __init__(self, stream_url: str):
        self.stream_url  = stream_url
        self.segment_dir = str(BUFFER_DIR)
        self.playlist    = str(BUFFER_DIR / "list.m3u8")
        self.process     = None
        self._start()

    def _start(self):
        cmd = [
            "ffmpeg", "-y",
            "-i", self.stream_url,
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "15",
            "-hls_list_size", "4",
            "-hls_flags", "delete_segments+omit_endlist",
            "-hls_segment_filename", str(BUFFER_DIR / "seg%03d.ts"),
            self.playlist
        ]
        self.process = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        print("📼 Rolling buffer started (60s window)")

    def clip_last_n_seconds(self, output_path: str, seconds: int = 60) -> bool:
        time.sleep(1)
        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", self.playlist,
            "-t", str(seconds),
            "-c", "copy",
            output_path
        ], capture_output=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait()
            print("📼 Buffer stopped")


# ── Stream URL Resolution ──────────────────────────────────

def resolve_stream_url(url: str) -> str | None:
    """Use Streamlink to resolve any platform URL to a direct stream URL."""
    result = subprocess.run(
        ["streamlink", url, "best", "--stream-url"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return result.stdout.strip()
    print(f"⚠️  Streamlink could not resolve URL: {result.stderr.strip()[:200]}")
    return None


# ── Audio Capture ──────────────────────────────────────────

def capture_audio_from_stream(stream_url: str) -> bool:
    """Capture a CHUNK_DURATION audio slice from a live stream."""
    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", stream_url,
        "-t", str(CHUNK_DURATION),
        "-q:a", "0",
        "-map", "a",
        str(TEMP_AUDIO)
    ], capture_output=True)
    return TEMP_AUDIO.exists()


def extract_audio_from_file(video_path: str, start: int, duration: int, out_path: str) -> bool:
    """Extract an audio slice from an uploaded video file."""
    result = subprocess.run([
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(duration),
        "-q:a", "0",
        "-map", "a",
        out_path
    ], capture_output=True)
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


# ── Transcribe & Score ─────────────────────────────────────

def transcribe(filepath: str) -> str:
    segments, _ = whisper.transcribe(str(filepath))
    return " ".join([s.text for s in segments]).strip()


def score_transcript(transcript: str) -> tuple[int, str]:
    """Send transcript to Claude and get a hype score + reason."""
    if not transcript:
        return 0, "Empty transcript"

    prompt = f"""You are a hype detector for CopperKing, a gaming and reaction streamer on TikTok and YouTube Shorts.

Identify moments of GENUINE hype, excitement, and high energy only.

SCORE 9-10 (Must clip):
- Clutch plays, last-second saves, unexpected wins
- Explosive reactions — screaming, hype, disbelief
- Moments where chat would go absolutely crazy
- Something so unexpected it makes you rewatch it

SCORE 8 (Strong clip):
- Solid hype with good energy and payoff
- Genuine excitement, not mild surprise
- Moments that would make someone stop scrolling

SCORE 5-7 (Skip):
- Interesting but low energy
- Casual conversation or mild reactions
- Gameplay without emotional payoff

SCORE 1-4 (Skip):
- Slow, quiet, or exploratory moments
- Technical talk, dead air, setup

HYPE SIGNALS:
- "LET'S GO", "NO WAY", "BRO", "WHAT", "OH MY GOD"
- Words: clutch, insane, crazy, unbelievable
- Sudden energy spikes, multiple people reacting

Transcript: '{transcript}'

Return ONLY JSON: {{"score": int, "reason": "one sentence", "hook": "one sentence describing the hype moment"}}"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        text  = response.content[0].text
        clean = text.replace("```json", "").replace("```", "").strip()
        data  = json.loads(clean)
        if "hook" in data:
            print(f"🎣 Hook: {data['hook']}")
        return data["score"], data["reason"]
    except Exception as e:
        print(f"⚠️  Scoring error: {e}")
        return 0, "Scoring failed"


# ── Clip Save Pipeline ─────────────────────────────────────

def process_clip(raw_path: str, score: int, reason: str, transcript: str,
                 game: str = "gaming", layout: dict = None):
    """Portrait convert → metadata → burn title → save both versions."""
    clips_dir = CLIPS_DIR / datetime.now().strftime("%Y-%m-%d")
    clips_dir.mkdir(parents=True, exist_ok=True)

    timestamp     = datetime.now().strftime("%H-%M-%S")
    portrait_path = clips_dir / f"score{score}_{timestamp}.mp4"
    titled_path   = clips_dir / f"score{score}_{timestamp}_titled.mp4"

    print("📱 Converting to portrait...")
    if not create_portrait_clip(raw_path, str(portrait_path), layout=layout):
        print("⚠️  Portrait conversion failed — skipping")
        return

    print("🤖 Generating metadata...")
    meta = generate_metadata(transcript, reason, game=game, used_titles=used_titles)
    used_titles.add(meta["burned_title"])

    print(f"📝 Burned title:    {meta['burned_title']}")
    print(f"📝 YouTube title:   {meta['yt_title']}")
    print(f"📝 TikTok caption:  {meta['tiktok_title']}")

    # Save metadata sidecar JSON
    meta_path = clips_dir / f"score{score}_{timestamp}_meta.json"
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    burn_title_into_clip(str(portrait_path), str(titled_path), meta["burned_title"])

    print(f"\n✅ Untitled: {portrait_path.name}")
    print(f"✅ Titled:   {titled_path.name}")
    print(f"✅ Metadata: {meta_path.name}\n")


# ── STREAM MODE ────────────────────────────────────────────

def monitor_stream(url: str, game: str = "gaming"):
    """Continuously monitor a live stream and auto-clip highlights."""
    layout = load_layout()
    print(f"\n🔴 Monitoring: {url}")
    print(f"   Game: {game}")
    print(f"   Threshold: {CLIP_SCORE_THRESHOLD}/10")
    print(f"   Facecam: {'on' if layout.get('include_facecam') else 'off'}")

    while True:
        stream_url = resolve_stream_url(url)
        if not stream_url:
            print("⏸️  Stream offline — retrying in 60s...")
            time.sleep(60)
            continue

        print("✅ Stream live! Starting buffer...\n")
        buffer = StreamBuffer(stream_url)

        print("⏳ Filling buffer (15s)...")
        time.sleep(15)

        failures = 0

        try:
            while True:
                print(f"🎤 Capturing {CHUNK_DURATION}s audio...")

                if not capture_audio_from_stream(stream_url):
                    failures += 1
                    print(f"⚠️  Audio capture failed ({failures}/3)")
                    if failures >= 3:
                        print("🔄 Stream ended — restarting monitor...")
                        break
                    time.sleep(10)
                    continue

                failures = 0

                transcript = transcribe(TEMP_AUDIO)
                preview = transcript[:100] + "..." if len(transcript) > 100 else transcript
                print(f"📝 {preview}")

                score, reason = score_transcript(transcript)
                print(f"⭐ Score: {score}/10 — {reason}")

                audio = analyze_audio(str(TEMP_AUDIO))
                if audio["boost"] > 0:
                    print(f"🔊 Audio boost: +{audio['boost']} ({', '.join(audio['reasons'])})")

                final = score + audio["boost"]
                print(f"🎯 Final: {final}/10")

                if final >= CLIP_SCORE_THRESHOLD:
                    timestamp = datetime.now().strftime("%H-%M-%S")
                    raw_path  = str(BUFFER_DIR / f"raw_{timestamp}.mp4")
                    print(f"\n🎬 CLIPPING (score {final}/10)")
                    if buffer.clip_last_n_seconds(raw_path, seconds=CLIP_DURATION):
                        process_clip(raw_path, final, reason, transcript, game=game, layout=layout)
                        if os.path.exists(raw_path):
                            os.remove(raw_path)

                print()

        except KeyboardInterrupt:
            print("\n⏹️  Stopping monitor...")
        finally:
            buffer.stop()
            break


# ── UPLOAD MODE ────────────────────────────────────────────

def process_upload(file_path: str, game: str = "gaming"):
    """Scan an uploaded video file and extract all highlight clips."""
    layout = load_layout()

    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return

    # Get video duration
    probe = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        file_path
    ], capture_output=True, text=True)

    if probe.returncode != 0:
        print("❌ Could not read video file")
        return

    total_duration = int(float(probe.stdout.strip()))
    print(f"\n📂 Processing: {os.path.basename(file_path)}")
    print(f"   Duration: {total_duration}s ({total_duration // 60}m {total_duration % 60}s)")
    print(f"   Chunk size: {CHUNK_DURATION}s")
    print(f"   Estimated chunks: {total_duration // CHUNK_DURATION}\n")

    clips_dir = CLIPS_DIR / datetime.now().strftime("%Y-%m-%d")
    clips_dir.mkdir(parents=True, exist_ok=True)

    chunk_audio = str(TEMP_DIR / "upload_chunk.wav")
    start       = 0
    chunk_num   = 0

    while start < total_duration:
        chunk_num += 1
        remaining  = total_duration - start
        duration   = min(CHUNK_DURATION, remaining)

        print(f"🎤 Chunk {chunk_num} [{start}s – {start + duration}s]")

        if not extract_audio_from_file(file_path, start, duration, chunk_audio):
            print("⚠️  Could not extract audio chunk — skipping")
            start += CHUNK_DURATION
            continue

        transcript = transcribe(chunk_audio)
        preview    = transcript[:100] + "..." if len(transcript) > 100 else transcript
        print(f"📝 {preview}")

        score, reason = score_transcript(transcript)
        print(f"⭐ Score: {score}/10 — {reason}")

        audio = analyze_audio(chunk_audio)
        if audio["boost"] > 0:
            print(f"🔊 Audio boost: +{audio['boost']} ({', '.join(audio['reasons'])})")

        final = score + audio["boost"]
        print(f"🎯 Final: {final}/10")

        if final >= CLIP_SCORE_THRESHOLD:
            # Extract the video chunk
            timestamp = datetime.now().strftime("%H-%M-%S")
            raw_path  = str(TEMP_DIR / f"upload_raw_{timestamp}.mp4")

            extract_result = subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(max(0, start)),
                "-i", file_path,
                "-t", str(CLIP_DURATION),
                "-c", "copy",
                raw_path
            ], capture_output=True)

            if extract_result.returncode == 0 and os.path.exists(raw_path):
                print(f"\n🎬 CLIPPING chunk {chunk_num} (score {final}/10)")
                process_clip(raw_path, final, reason, transcript, game=game, layout=layout)
                if os.path.exists(raw_path):
                    os.remove(raw_path)

        start += CHUNK_DURATION
        print()

    print(f"✅ Upload processing complete. Clips saved to: {clips_dir}")


# ── Entry Point ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CopperKing Clipper v2")
    parser.add_argument("mode", choices=["stream", "upload"], help="Mode: stream or upload")
    parser.add_argument("target", nargs="?", help="Stream URL or video file path")
    parser.add_argument("--game", default="gaming", help="Game name for metadata (default: gaming)")
    parser.add_argument("--interactive", action="store_true", help="Prompt for URL interactively")

    args = parser.parse_args()

    target = args.target
    if not target and args.interactive:
        target = input("🔴 Enter stream URL (or Q to quit): ").strip()
        if target.upper() == "Q":
            print("👋 Exiting.")
            return

    if not target:
        print("❌ No target provided. Use --interactive or pass a URL/path.")
        parser.print_help()
        return

    if args.mode == "stream":
        monitor_stream(target, game=args.game)
    elif args.mode == "upload":
        process_upload(target, game=args.game)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Clipper stopped cleanly.")
