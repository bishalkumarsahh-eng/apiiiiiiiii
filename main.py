import os
import re
import secrets
import time
import asyncio
from pathlib import Path
from typing import Optional

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

APP_NAME = os.getenv("API_NAME", "Juno X Music API")
API_KEY = os.getenv("API_KEY", "").strip()
MAX_DURATION = int(os.getenv("MAX_DURATION", "900"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "10"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))

app = FastAPI(title=APP_NAME, version="2.0.0", docs_url="/docs", redoc_url="/redoc")


def check_key(request: Request):
    key = request.headers.get("x-api-key", "").strip()
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key:
        key = request.query_params.get("api_key", "").strip()
    if not API_KEY:
        raise HTTPException(500, "API_KEY is not configured on the server")
    if not key or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(401, "Invalid API key")


def normalize_url(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return f"https://www.youtube.com/watch?v={value}"
    if "youtube.com" in value or "youtu.be" in value:
        return value
    raise HTTPException(400, "Only YouTube URLs or video IDs are supported")


def search_sync(query: str, limit: int):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for item in data.get("entries") or []:
        if not item or not item.get("id"):
            continue
        results.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "url": item.get("webpage_url") or f"https://www.youtube.com/watch?v={item.get('id')}",
            "duration": item.get("duration"),
            "channel": item.get("channel") or item.get("uploader"),
            "thumbnail": item.get("thumbnail"),
        })
    return results


def info_sync(url: str):
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(normalize_url(url), download=False)
    duration = info.get("duration")
    if duration and duration > MAX_DURATION:
        raise ValueError(f"Duration exceeds {MAX_DURATION} seconds")
    return info


def download_sync(url: str, media_type: str, workdir: str):
    Path(workdir).mkdir(parents=True, exist_ok=True)
    common = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": str(Path(workdir) / "%(id)s.%(ext)s"),
        "retries": 3,
        "socket_timeout": 30,
    }
    if media_type == "audio":
        common.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
        extension = ".mp3"
    else:
        common.update({
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best[height<=1080]",
            "merge_output_format": "mp4",
        })
        extension = ".mp4"

    with yt_dlp.YoutubeDL(common) as ydl:
        info = ydl.extract_info(normalize_url(url), download=True)
    video_id = info.get("id")
    if not video_id:
        raise RuntimeError("Unable to determine video ID")
    target = Path(workdir) / f"{video_id}{extension}"
    if not target.exists():
        matches = [p for p in Path(workdir).glob(f"{video_id}.*") if p.is_file() and p.suffix not in {".part", ".ytdl"}]
        if not matches:
            raise FileNotFoundError("Downloaded file not found")
        target = matches[0]
    return target, info


@app.get("/")
async def root():
    return {"ok": True, "service": APP_NAME, "version": "2.0.0", "time": int(time.time())}


@app.get("/health")
async def health():
    return {"ok": True, "service": APP_NAME}


@app.get("/search")
async def search(request: Request, q: str, limit: int = 5):
    check_key(request)
    q = q.strip()
    if not q or len(q) > 200:
        raise HTTPException(400, "Query must contain 1-200 characters")
    limit = max(1, min(limit, MAX_RESULTS))
    try:
        results = await asyncio.to_thread(search_sync, q, limit)
        return {"success": True, "results": results}
    except Exception:
        raise HTTPException(502, "YouTube search failed")


@app.get("/info")
async def info(request: Request, url: str):
    check_key(request)
    try:
        data = await asyncio.to_thread(info_sync, url)
        return {
            "success": True,
            "id": data.get("id"),
            "title": data.get("title"),
            "duration": data.get("duration"),
            "channel": data.get("channel") or data.get("uploader"),
            "thumbnail": data.get("thumbnail"),
            "webpage_url": data.get("webpage_url"),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(502, "Unable to fetch media information")


@app.get("/download")
async def download(request: Request, url: str, type: str = "audio"):
    check_key(request)
    if type not in {"audio", "video"}:
        raise HTTPException(400, "type must be audio or video")
    workdir = str(Path("/tmp") / "juno_api" / secrets.token_hex(10))
    try:
        path, info = await asyncio.wait_for(
            asyncio.to_thread(download_sync, url, type, workdir),
            timeout=DOWNLOAD_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "Download timed out")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(502, "Download failed")

    title = re.sub(r"[\\\"\r\n]", "", info.get("title") or "audio")[:120]
    media_type = "audio/mpeg" if path.suffix.lower() == ".mp3" else "video/mp4"

    def stream():
        try:
            with path.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            try:
                path.unlink(missing_ok=True)
                Path(workdir).rmdir()
            except Exception:
                pass

    return StreamingResponse(
        stream(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{title}{path.suffix}"',
            "X-API-Version": "2.0.0",
        },
    )
