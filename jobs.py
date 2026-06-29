"""
jobs.py — In-memory job tracker for background clipping tasks.
Tracks status, progress, logs, and results for each job.
"""

import uuid
from datetime import datetime
from typing import Optional
from enum import Enum


class JobStatus(str, Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    DONE       = "done"
    FAILED     = "failed"
    STOPPED    = "stopped"


class Job:
    def __init__(self, job_type: str, target: str, game: str = "gaming"):
        self.id         = str(uuid.uuid4())
        self.type       = job_type   # "stream" or "upload"
        self.target     = target
        self.game       = game
        self.status     = JobStatus.PENDING
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = self.created_at
        self.logs       = []
        self.clips      = []         # list of clip metadata dicts
        self.error      = None
        self.process    = None       # for stream jobs (subprocess handle)

    def log(self, message: str):
        entry = f"[{datetime.utcnow().strftime('%H:%M:%S')}] {message}"
        self.logs.append(entry)
        self.updated_at = datetime.utcnow().isoformat()
        print(entry)

    def to_dict(self) -> dict:
        return {
            "id":         self.id,
            "type":       self.type,
            "target":     self.target,
            "game":       self.game,
            "status":     self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "logs":       self.logs[-50:],   # last 50 log lines
            "clips":      self.clips,
            "error":      self.error,
        }


# ── Job Store ──────────────────────────────────────────────

class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create(self, job_type: str, target: str, game: str = "gaming") -> Job:
        job = Job(job_type, target, game)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def all(self) -> list[dict]:
        return [j.to_dict() for j in sorted(
            self._jobs.values(),
            key=lambda j: j.created_at,
            reverse=True
        )]

    def active_stream_job(self) -> Optional[Job]:
        """Return the currently running stream job if any."""
        for job in self._jobs.values():
            if job.type == "stream" and job.status == JobStatus.RUNNING:
                return job
        return None


jobs = JobStore()
