"""
main.py — CopperKing Clipper API (FastAPI backend)

Endpoints:
  POST   /clip/stream          Start monitoring a live stream
  POST   /clip/upload          Upload + process a video file
  DELETE /clip/stream/{job_id} Stop a running stream job
  GET    /jobs                 List all jobs
  GET    /jobs/{job_id}        Get job status + logs
  GET    /clips                List all saved clips
  GET    /clips/{filename}     Download a clip file
  POST   /layout               Save drag & drop layout
  GET    /layout               Get current layout
  GET    /health               Health check
"""

import os
import json
import asyncio
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from config import CLIPS_DIR, TEMP_DIR, load_layout, save_layout, CLIP_SCORE_THRESHOLD
from jobs import jobs, JobStatus

app = FastAPI(
    title="CopperKing Clipper API",
    description="AI-powered highlight clipping for streamers",
    version="2.0.0"
)

# ── CORS ───────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Lock down to your domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS_DIR = TEMP_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# ── Models ─────────────────────────────────────────────────

class StreamRequest(BaseModel):
    url:  str
    game: Optional[str] = "gaming"

class LayoutRequest(BaseModel):
    stream_width:    Optional[int]  = 1920
    stream_height:   Optional[int]  = 1080
    facecam:         Optional[dict] = None
    gameplay:        Optional[dict] = None
    include_facecam: Optional[bool] = True
    gameplay_height: Optional[int]  = 1320


# ── Background Workers ─────────────────────────────────────

def run_stream_job(job_id: str, url: str, game: str):
    """Background thread: monitor a live stream and clip highlights."""
    from faster_whisper import WhisperModel
    from anthropic import Anthropic
    from audio_analyzer import analyze_audio
    from formatter import create_portrait_clip
    from metadata import generate_metadata, burn_title_into_clip
    import time

    job = jobs.get(job_id)
    if not job:
        return

    job.status = JobStatus.RUNNING
    job.log(f"Starting stream monitor: {url}")

    try:
        from config import (
            WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE,
            CHUNK_DURATION, CLIP_DURATION, BUFFER_DIR
        )

        whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
        client  = Anthropic()
        layout  = load_layout()
        used_titles: set = set()

        # Resolve stream URL
        job.log("Resolving stream URL via Streamlink...")
        result = subprocess.run(
            ["streamlink", url, "best", "--stream-url"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            job.status = JobStatus.FAILED
            job.error  = "Stream is offline or URL is invalid"
            job.log(f"Streamlink error: {result.stderr[:200]}")
            return

        stream_url = result.stdout.strip()
        job.log("Stream resolved. Starting rolling buffer...")

        # Start HLS buffer
        playlist = str(BUFFER_DIR / "list.m3u8")
        buffer_cmd = [
            "ffmpeg", "-y", "-i", stream_url,
            "-c", "copy", "-f", "hls",
            "-hls_time", "15", "-hls_list_size", "4",
            "-hls_flags", "delete_segments+omit_endlist",
            "-hls_segment_filename", str(BUFFER_DIR / "seg%03d.ts"),
            playlist
        ]
        buffer_proc = subprocess.Popen(
            buffer_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        job.process = buffer_proc

        job.log("Buffer started. Waiting 15s to fill...")
        time.sleep(15)

        failures    = 0
        temp_audio  = str(TEMP_DIR / f"chunk_{job_id}.wav")

        while job.status == JobStatus.RUNNING:
            job.log(f"Capturing {CHUNK_DURATION}s audio chunk...")

            audio_result = subprocess.run([
                "ffmpeg", "-y", "-i", stream_url,
                "-t", str(CHUNK_DURATION),
                "-q:a", "0", "-map", "a", temp_audio
            ], capture_output=True, timeout=CHUNK_DURATION + 10)

            if audio_result.returncode != 0 or not os.path.exists(temp_audio):
                failures += 1
                job.log(f"Audio capture failed ({failures}/3)")
                if failures >= 3:
                    job.log("Stream appears offline — stopping monitor")
                    job.status = JobStatus.DONE
                    break
                time.sleep(10)
                continue

            failures = 0

            # Transcribe
            segments, _ = whisper.transcribe(temp_audio)
            transcript  = " ".join([s.text for s in segments]).strip()
            job.log(f"Transcript: {transcript[:80]}...")

            # Score via Claude
            score_response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                messages=[{"role": "user", "content": _score_prompt(transcript)}]
            )
            score_text  = score_response.content[0].text.replace("```json","").replace("```","").strip()
            score_data  = json.loads(score_text)
            score       = score_data["score"]
            reason      = score_data["reason"]

            # Audio boost
            audio_analysis = analyze_audio(temp_audio)
            boost          = audio_analysis["boost"]
            final_score    = score + boost

            job.log(f"Score: {score}/10 + boost {boost} = {final_score}/10 — {reason}")

            if final_score >= CLIP_SCORE_THRESHOLD:
                job.log("🎬 Highlight detected! Clipping...")
                timestamp = datetime.now().strftime("%H-%M-%S")
                raw_path  = str(BUFFER_DIR / f"raw_{timestamp}.mp4")

                clip_result = subprocess.run([
                    "ffmpeg", "-y", "-i", playlist,
                    "-t", str(CLIP_DURATION), "-c", "copy", raw_path
                ], capture_output=True)

                if clip_result.returncode == 0 and os.path.exists(raw_path):
                    clip_info = _process_and_save_clip(
                        raw_path, final_score, reason, transcript,
                        game, layout, used_titles, client, job
                    )
                    if clip_info:
                        job.clips.append(clip_info)
                    if os.path.exists(raw_path):
                        os.remove(raw_path)

        buffer_proc.terminate()
        buffer_proc.wait()

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error  = str(e)
        job.log(f"Fatal error: {e}")


def run_upload_job(job_id: str, file_path: str, game: str):
    """Background thread: scan an uploaded video file for highlights."""
    from faster_whisper import WhisperModel
    from anthropic import Anthropic
    from audio_analyzer import analyze_audio
    import time

    job = jobs.get(job_id)
    if not job:
        return

    job.status = JobStatus.RUNNING
    job.log(f"Processing upload: {Path(file_path).name}")

    try:
        from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE, CHUNK_DURATION, CLIP_DURATION

        whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
        client  = Anthropic()
        layout  = load_layout()
        used_titles: set = set()

        # Get duration
        probe = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", file_path
        ], capture_output=True, text=True)

        if probe.returncode != 0:
            job.status = JobStatus.FAILED
            job.error  = "Could not read video file"
            return

        total  = int(float(probe.stdout.strip()))
        chunks = total // CHUNK_DURATION
        job.log(f"Duration: {total}s | Chunks: {chunks}")

        start     = 0
        chunk_num = 0

        while start < total and job.status == JobStatus.RUNNING:
            chunk_num += 1
            duration   = min(CHUNK_DURATION, total - start)
            job.log(f"Chunk {chunk_num}/{chunks} [{start}s–{start+duration}s]")

            chunk_audio = str(TEMP_DIR / f"upload_chunk_{job_id}.wav")
            audio_result = subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(start), "-i", file_path,
                "-t", str(duration),
                "-q:a", "0", "-map", "a", chunk_audio
            ], capture_output=True)

            if audio_result.returncode != 0:
                job.log("Could not extract audio — skipping chunk")
                start += CHUNK_DURATION
                continue

            segments, _ = whisper.transcribe(chunk_audio)
            transcript  = " ".join([s.text for s in segments]).strip()

            score_response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                messages=[{"role": "user", "content": _score_prompt(transcript)}]
            )
            score_text  = score_response.content[0].text.replace("```json","").replace("```","").strip()
            score_data  = json.loads(score_text)
            score       = score_data["score"]
            reason      = score_data["reason"]

            audio_analysis = analyze_audio(chunk_audio)
            boost          = audio_analysis["boost"]
            final_score    = score + boost

            job.log(f"Score: {score} + {boost} = {final_score}/10 — {reason}")

            if final_score >= CLIP_SCORE_THRESHOLD:
                job.log("🎬 Highlight! Extracting clip...")
                timestamp = datetime.now().strftime("%H-%M-%S")
                raw_path  = str(TEMP_DIR / f"upload_raw_{timestamp}.mp4")

                subprocess.run([
                    "ffmpeg", "-y",
                    "-ss", str(max(0, start)),
                    "-i", file_path,
                    "-t", str(CLIP_DURATION),
                    "-c", "copy", raw_path
                ], capture_output=True)

                if os.path.exists(raw_path):
                    clip_info = _process_and_save_clip(
                        raw_path, final_score, reason, transcript,
                        game, layout, used_titles, client, job
                    )
                    if clip_info:
                        job.clips.append(clip_info)
                    if os.path.exists(raw_path):
                        os.remove(raw_path)

            start += CHUNK_DURATION

        job.status = JobStatus.DONE
        job.log(f"Done. {len(job.clips)} clips saved.")

    except Exception as e:
        job.status = JobStatus.FAILED
        job.error  = str(e)
        job.log(f"Fatal error: {e}")


def _score_prompt(transcript: str) -> str:
    return f"""You are a hype detector for CopperKing, a gaming streamer.
Score this transcript for clip-worthiness. Only high energy, hype, clutch, or shocking moments score 8+.
Transcript: '{transcript}'
Return ONLY JSON: {{"score": int 1-10, "reason": "one sentence", "hook": "one sentence"}}"""


def _process_and_save_clip(raw_path, score, reason, transcript,
                            game, layout, used_titles, client, job) -> Optional[dict]:
    """Portrait convert → metadata → burn title → return clip info dict."""
    from formatter import create_portrait_clip
    from metadata import generate_metadata, burn_title_into_clip

    clips_dir = CLIPS_DIR / datetime.now().strftime("%Y-%m-%d")
    clips_dir.mkdir(parents=True, exist_ok=True)

    timestamp     = datetime.now().strftime("%H-%M-%S")
    portrait_path = clips_dir / f"score{score}_{timestamp}.mp4"
    titled_path   = clips_dir / f"score{score}_{timestamp}_titled.mp4"
    meta_path     = clips_dir / f"score{score}_{timestamp}_meta.json"

    job.log("Converting to portrait...")
    if not create_portrait_clip(raw_path, str(portrait_path), layout=layout):
        job.log("Portrait conversion failed")
        return None

    job.log("Generating metadata...")
    meta = generate_metadata(transcript, reason, game=game, used_titles=used_titles)
    used_titles.add(meta["burned_title"])

    job.log(f"Title: {meta['burned_title']}")
    job.log(f"YouTube: {meta['yt_title']}")
    job.log(f"TikTok: {meta['tiktok_title']}")

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    burn_title_into_clip(str(portrait_path), str(titled_path), meta["burned_title"])

    return {
        "score":        score,
        "timestamp":    timestamp,
        "portrait":     portrait_path.name,
        "titled":       titled_path.name,
        "meta":         meta,
        "date":         datetime.now().strftime("%Y-%m-%d")
    }


# ── Routes ─────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.post("/clip/stream")
def start_stream(req: StreamRequest, background_tasks: BackgroundTasks):
    """Start monitoring a live stream URL."""
    active = jobs.active_stream_job()
    if active:
        raise HTTPException(400, f"Stream job already running: {active.id}")

    job = jobs.create("stream", req.url, req.game)
    background_tasks.add_task(run_stream_job, job.id, req.url, req.game)
    return {"job_id": job.id, "status": job.status}


@app.delete("/clip/stream/{job_id}")
def stop_stream(job_id: str):
    """Stop a running stream monitor job."""
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.type != "stream":
        raise HTTPException(400, "Not a stream job")

    job.status = JobStatus.STOPPED
    job.log("Stop requested by user")
    if job.process:
        job.process.terminate()

    return {"job_id": job_id, "status": job.status}


@app.post("/clip/upload")
async def upload_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    game: str = Form(default="gaming")
):
    """Upload a video file and process it for highlights."""
    allowed = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
    ext     = Path(file.filename).suffix.lower()

    if ext not in allowed:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {allowed}")

    # Save upload
    save_path = UPLOADS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename}"
    with open(save_path, "wb") as f:
        content = await file.read()
        f.write(content)

    job = jobs.create("upload", str(save_path), game)
    background_tasks.add_task(run_upload_job, job.id, str(save_path), game)
    return {"job_id": job.id, "status": job.status, "filename": file.filename}


@app.get("/jobs")
def list_jobs():
    return jobs.all()


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/clips")
def list_clips():
    """List all saved clips across all dates."""
    result = []
    if not CLIPS_DIR.exists():
        return result

    for date_dir in sorted(CLIPS_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for meta_file in sorted(date_dir.glob("*_meta.json"), reverse=True):
            try:
                with open(meta_file) as f:
                    meta = json.load(f)
                base = meta_file.stem.replace("_meta", "")
                result.append({
                    "date":         date_dir.name,
                    "base":         base,
                    "portrait":     f"{base}.mp4",
                    "titled":       f"{base}_titled.mp4",
                    "meta":         meta,
                })
            except Exception:
                continue

    return result


@app.get("/clips/{date}/{filename}")
def download_clip(date: str, filename: str):
    """Download a specific clip file."""
    clip_path = CLIPS_DIR / date / filename
    if not clip_path.exists():
        raise HTTPException(404, "Clip not found")
    return FileResponse(str(clip_path), media_type="video/mp4", filename=filename)


@app.get("/layout")
def get_layout():
    return load_layout()


@app.post("/layout")
def update_layout(req: LayoutRequest):
    """Save layout config from web UI drag & drop."""
    layout = load_layout()
    layout.update(req.dict(exclude_none=True))
    save_layout(layout)
    return {"status": "saved", "layout": layout}
