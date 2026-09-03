import os
import re
import secrets
import time
import asyncio
import sqlite3
import hashlib
import base64
import shutil
from pathlib import Path
from typing import Optional

import yt_dlp
from fastapi import FastAPI, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import StreamingResponse

APP_NAME = os.getenv("API_NAME", "Juno X Music API")
ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()
LEGACY_API_KEY = os.getenv("API_KEY", "").strip()
API_DB = os.getenv("API_DB", "/tmp/juno_api.sqlite3").strip()
MAX_DURATION = int(os.getenv("MAX_DURATION", "900"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "20"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))
YOUTUBE_COOKIES_B64 = os.getenv("YOUTUBE_COOKIES_B64", "").strip()
YOUTUBE_COOKIES_FILE = os.getenv("YOUTUBE_COOKIES_FILE", "/tmp/juno_youtube_cookies.txt").strip()
# Ordered fallback list. Keep the default conservative; override with a comma-separated list.
YOUTUBE_PLAYER_CLIENTS = [x.strip() for x in os.getenv(
    "YOUTUBE_PLAYER_CLIENTS", "web_safari,mweb,tv,android_vr,web_embedded"
).split(",") if x.strip()]

app = FastAPI(title=APP_NAME, version="4.0.0", docs_url="/docs", redoc_url="/redoc")
admin_bearer = HTTPBearer(auto_error=False, scheme_name="AdminKey")


def db():
    conn = sqlite3.connect(API_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS api_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, key_hash TEXT UNIQUE NOT NULL, label TEXT NOT NULL, created_at INTEGER NOT NULL, last_used INTEGER, active INTEGER NOT NULL DEFAULT 1)")
    conn.commit()
    return conn


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def prepare_youtube_cookies() -> Optional[str]:
    if not YOUTUBE_COOKIES_B64:
        return None
    try:
        data = base64.b64decode(YOUTUBE_COOKIES_B64, validate=True)
        if not data or b"# Netscape HTTP Cookie File" not in data[:2000] and b"# HTTP Cookie File" not in data[:2000]:
            raise ValueError("cookie data is not a Netscape/Mozilla cookie file")
        path = Path(YOUTUBE_COOKIES_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return str(path)
    except Exception as exc:
        raise RuntimeError("YOUTUBE_COOKIES_B64 is not valid Netscape-format base64") from exc


def yt_base_opts(client: Optional[str] = None, use_cookies: bool = True) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "extractor_args": {"youtube": {"player_client": [client]}} if client else {},
    }
    if use_cookies:
        cookie_file = prepare_youtube_cookies()
        if cookie_file:
            opts["cookiefile"] = cookie_file
    return opts


def youtube_attempts():
    # Cookies are intentionally omitted for clients that do not support account cookies reliably.
    for client in YOUTUBE_PLAYER_CLIENTS:
        use_cookies = client not in {"android", "android_vr", "ios", "tv_simply", "tv_downgraded", "web_embedded"}
        yield client, use_cookies


def check_admin(request: Request, credentials: HTTPAuthorizationCredentials | None = None):
    key = request.headers.get("x-admin-key", "").strip()
    if not key and credentials and credentials.scheme.lower() == "bearer":
        key = credentials.credentials.strip()
    if not key:
        key = request.query_params.get("admin_key", "").strip()
    if not ADMIN_KEY:
        raise HTTPException(500, "ADMIN_KEY is not configured on the server")
    if not key or not secrets.compare_digest(key, ADMIN_KEY):
        raise HTTPException(403, "Admin authentication required")


def require_admin(request: Request, credentials: HTTPAuthorizationCredentials | None = Security(admin_bearer)):
    check_admin(request, credentials)
    return True


def check_key(request: Request):
    key = request.headers.get("x-api-key", "").strip()
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key:
        key = request.query_params.get("api_key", "").strip()
    if not key:
        raise HTTPException(401, "API key required")
    if LEGACY_API_KEY and secrets.compare_digest(key, LEGACY_API_KEY):
        return
    conn = db()
    row = conn.execute("SELECT id, active FROM api_keys WHERE key_hash=?", (hash_key(key),)).fetchone()
    if not row or not row["active"]:
        conn.close()
        raise HTTPException(401, "Invalid or revoked API key")
    conn.execute("UPDATE api_keys SET last_used=? WHERE id=?", (int(time.time()), row["id"]))
    conn.commit()
    conn.close()


def normalize_url(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return f"https://www.youtube.com/watch?v={value}"
    if "youtube.com" in value or "youtu.be" in value:
        return value
    raise HTTPException(400, "Only YouTube URLs or video IDs are supported")


def is_auth_error(exc: Exception) -> bool:
    text = str(exc).lower()
    markers = (
        "sign in to confirm", "not a bot", "confirm you're not a bot",
        "cookies", "po token", "proof of origin", "http error 403", "sabr",
        "requested format is not available", "botguard"
    )
    return any(m in text for m in markers)


def search_sync(query: str, limit: int):
    last = None
    for client, use_cookies in youtube_attempts():
        try:
            opts = yt_base_opts(client, use_cookies)
            opts.update({"skip_download": True, "extract_flat": True})
            with yt_dlp.YoutubeDL(opts) as ydl:
                data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
            results = []
            for item in data.get("entries") or []:
                if item and item.get("id"):
                    results.append({
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "url": item.get("webpage_url") or f"https://www.youtube.com/watch?v={item.get('id')}",
                        "duration": item.get("duration"),
                        "channel": item.get("channel") or item.get("uploader"),
                        "thumbnail": item.get("thumbnail"),
                    })
            return results
        except Exception as exc:
            last = exc
            if not is_auth_error(exc):
                break
    raise last or RuntimeError("YouTube search failed")


def info_sync(url: str):
    last = None
    for client, use_cookies in youtube_attempts():
        try:
            with yt_dlp.YoutubeDL(yt_base_opts(client, use_cookies)) as ydl:
                info = ydl.extract_info(normalize_url(url), download=False)
            duration = info.get("duration")
            if duration and duration > MAX_DURATION:
                raise ValueError(f"Duration exceeds {MAX_DURATION} seconds")
            return info
        except ValueError:
            raise
        except Exception as exc:
            last = exc
            if not is_auth_error(exc):
                break
    raise last or RuntimeError("Unable to fetch media information")


def download_once(url: str, media_type: str, workdir: str, client: str, use_cookies: bool):
    Path(workdir).mkdir(parents=True, exist_ok=True)
    common = yt_base_opts(client, use_cookies)
    common.update({"outtmpl": str(Path(workdir) / "%(id)s.%(ext)s")})
    if media_type == "audio":
        common.update({
            "format": "bestaudio/best",
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
        })
        extension = ".mp3"
    else:
        common.update({
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best[height<=1080]/best",
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


def download_sync(url: str, media_type: str, workdir: str):
    last = None
    for client, use_cookies in youtube_attempts():
        try:
            return download_once(url, media_type, workdir, client, use_cookies)
        except Exception as exc:
            last = exc
            # Clean partial files before trying another client.
            for p in Path(workdir).glob("*"):
                try:
                    if p.is_file():
                        p.unlink()
                except OSError:
                    pass
            if not is_auth_error(exc):
                break
    raise last or RuntimeError("Download failed")


@app.post("/admin/keys/create")
async def create_api_key(request: Request, label: str = "bot", _: bool = Security(require_admin)):
    label = label.strip()[:100] or "bot"
    raw = "jx_live_" + secrets.token_urlsafe(36)
    conn = db()
    cur = conn.execute("INSERT INTO api_keys(key_hash,label,created_at,active) VALUES(?,?,?,1)", (hash_key(raw), label, int(time.time())))
    conn.commit()
    key_id = cur.lastrowid
    conn.close()
    return {"success": True, "id": key_id, "label": label, "api_key": raw, "warning": "Save this key now. It will not be shown again."}


@app.get("/admin/keys")
async def list_api_keys(request: Request, _: bool = Security(require_admin)):
    conn = db()
    rows = conn.execute("SELECT id,label,created_at,last_used,active FROM api_keys ORDER BY id DESC").fetchall()
    conn.close()
    return {"success": True, "keys": [dict(r) for r in rows]}


@app.post("/admin/keys/revoke")
async def revoke_api_key(request: Request, api_key: Optional[str] = None, key_id: Optional[int] = None, _: bool = Security(require_admin)):
    if not api_key and key_id is None:
        raise HTTPException(400, "Provide api_key or key_id")
    conn = db()
    if key_id is not None:
        cur = conn.execute("UPDATE api_keys SET active=0 WHERE id=?", (key_id,))
    else:
        cur = conn.execute("UPDATE api_keys SET active=0 WHERE key_hash=?", (hash_key(api_key.strip()),))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "API key not found")
    return {"success": True, "message": "API key revoked"}


@app.get("/")
async def root():
    return {"ok": True, "service": APP_NAME, "version": "4.0.0", "time": int(time.time())}


@app.get("/health")
async def health():
    return {
        "ok": True,
        "service": APP_NAME,
        "version": "4.0.0",
        "youtube_cookies_configured": bool(YOUTUBE_COOKIES_B64),
        "youtube_player_clients": YOUTUBE_PLAYER_CLIENTS,
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
    }


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
            asyncio.to_thread(download_sync, url, type, workdir), timeout=DOWNLOAD_TIMEOUT
        )
    except asyncio.TimeoutError:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(504, "Download timed out")
    except ValueError as e:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(400, str(e))
    except Exception as e:
        shutil.rmtree(workdir, ignore_errors=True)
        msg = str(e)
        if is_auth_error(e):
            raise HTTPException(502, "YouTube rejected the request. Refresh YOUTUBE_COOKIES_B64 and/or adjust YOUTUBE_PLAYER_CLIENTS; PO-token enforcement can also affect some clients.")
        raise HTTPException(502, "Download failed")

    title = re.sub(r"[\\\"\r\n]", "", info.get("title") or "audio")[:120]
    media_type = "audio/mpeg" if path.suffix.lower() == ".mp3" else "video/mp4"

    def stream():
        try:
            with path.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    yield chunk
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return StreamingResponse(
        stream(), media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{title}{path.suffix}"',
            "X-API-Version": "4.0.0",
        },
    )
