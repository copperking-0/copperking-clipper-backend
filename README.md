# CopperKing Clipper — Backend API

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in this directory:
```
ANTHROPIC_API_KEY=your_key_here
CLIPS_DIR=/path/to/your/clips/folder   # optional, defaults to ~/CopperKing/clips
```

## Run locally

```bash
uvicorn main:app --reload --port 8000
```

API will be live at: http://localhost:8000
Interactive docs at: http://localhost:8000/docs

## Deploy to Railway / Render

1. Push this folder to a GitHub repo
2. Connect repo to Railway or Render
3. Set environment variables: ANTHROPIC_API_KEY, CLIPS_DIR
4. Deploy — done

Start command: `uvicorn main:app --host 0.0.0.0 --port 8000`

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /health | Health check |
| POST | /clip/stream | Start stream monitor |
| DELETE | /clip/stream/{id} | Stop stream monitor |
| POST | /clip/upload | Upload + process video |
| GET | /jobs | List all jobs |
| GET | /jobs/{id} | Job status + logs |
| GET | /clips | List all clips |
| GET | /clips/{date}/{filename} | Download clip |
| GET | /layout | Get layout config |
| POST | /layout | Save layout config |
