import os
import re
import time
import asyncio
import sqlite3
import logging
import secrets
import urllib.request
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Header, Depends, Security, Request
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import yt_dlp
from ytmusicapi import YTMusic

load_dotenv()

DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
DB_FILE = os.getenv("DB_FILE", "cache.db")
CACHE_EXPIRE_HOURS = float(os.getenv("CACHE_EXPIRE_HOURS", "24"))
MAX_VIDEO_QUALITY = int(os.getenv("MAX_VIDEO_QUALITY", "720"))
PORT = int(os.getenv("PORT", "8000"))
COOKIE_URL = os.getenv("COOKIE_URL", "").strip()
YOUTUBE_USE_COOKIES = os.getenv("YOUTUBE_USE_COOKIES", "true").lower() in ("1", "true", "yes", "on")
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")
YOUTUBE_PLAYER_CLIENTS = os.getenv("YOUTUBE_PLAYER_CLIENTS", "web_embedded").strip()
CONCURRENT_FRAGMENT_DOWNLOADS = int(os.getenv("CONCURRENT_FRAGMENT_DOWNLOADS", "10"))
HTTP_CHUNK_SIZE = int(os.getenv("HTTP_CHUNK_SIZE", "10485760"))
SOCKET_TIMEOUT = int(os.getenv("SOCKET_TIMEOUT", "20"))
RETRIES = int(os.getenv("RETRIES", "5"))
FRAGMENT_RETRIES = int(os.getenv("FRAGMENT_RETRIES", "5"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "60"))
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "60"))
MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "20"))
PUBLIC_HEALTH = os.getenv("PUBLIC_HEALTH", "true").lower() in ("1", "true", "yes", "on")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")
API_KEY = os.getenv("API_KEY", "").strip()
# Accept one or many keys. API_KEYS is comma/newline separated and takes precedence.
_raw_keys = os.getenv("API_KEYS", "").replace("\n", ",")
API_KEYS = {k.strip() for k in _raw_keys.split(",") if k.strip()}
if API_KEY:
    API_KEYS.add(API_KEY)
# Backward compatibility with older deployments.
legacy_key = os.getenv("BOT_API_KEY", "").strip()
if legacy_key:
    API_KEYS.add(legacy_key)
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
if ADMIN_API_KEY:
    API_KEYS.add(ADMIN_API_KEY)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("juno-api")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
rate_lock = asyncio.Lock()
rate_buckets: Dict[str, list[float]] = {}


def init_db():
    with sqlite3.connect(DB_FILE, timeout=15) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, title TEXT,
            file_name TEXT, file_path TEXT, file_type TEXT, file_size INTEGER,
            duration INTEGER, created_time REAL, thumbnail TEXT,
            UNIQUE(video_id, file_type))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)""")
        conn.commit()


def stat_inc(key: str, amount: int = 1):
    try:
        with sqlite3.connect(DB_FILE, timeout=15) as conn:
            conn.execute("INSERT INTO stats(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value", (key, amount))
            conn.commit()
    except Exception:
        pass


def get_stats():
    try:
        with sqlite3.connect(DB_FILE, timeout=15) as conn:
            return dict(conn.execute("SELECT key,value FROM stats").fetchall())
    except Exception:
        return {}


async def require_api_key(request: Request, x_api_key: Optional[str] = Security(api_key_header), authorization: Optional[str] = Header(None), api_key: Optional[str] = Query(None, description="API key; legacy compatibility"), key: Optional[str] = Query(None, description="API key alias")):
    if not API_KEYS:
        raise HTTPException(503, "API authentication is not configured on the server.")
    # Accept headers, bearer auth, and both common query aliases.
    supplied = (x_api_key or api_key or key or "").strip()
    if not supplied and authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            supplied = token.strip()
    valid = any(secrets.compare_digest(supplied, candidate) for candidate in API_KEYS) if supplied else False
    if not valid:
        stat_inc("auth_failures")
        raise HTTPException(401, "Invalid or missing API key.")
    stat_inc("authenticated_requests")
    return True


def require_admin_key(request: Request, x_api_key: Optional[str] = Security(api_key_header), authorization: Optional[str] = Header(None), api_key: Optional[str] = Query(None), admin_key: Optional[str] = Query(None)):
    supplied = (x_api_key or admin_key or api_key or "").strip()
    if not supplied and authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer": supplied = token.strip()
    if not ADMIN_API_KEY or not supplied or not secrets.compare_digest(supplied, ADMIN_API_KEY):
        raise HTTPException(403, "Invalid or missing admin API key.")
    return True


async def apply_rate_limit(request: Request):
    key = request.headers.get("X-API-Key", "") or request.client.host if request.client else "unknown"
    now = time.time()
    async with rate_lock:
        bucket = [t for t in rate_buckets.get(key, []) if now - t < RATE_WINDOW]
        if len(bucket) >= RATE_LIMIT:
            raise HTTPException(429, f"Rate limit exceeded. Try again in {RATE_WINDOW} seconds.")
        bucket.append(now)
        rate_buckets[key] = bucket
    return True


async def guard(request: Request, auth: bool = Depends(require_api_key), rate: bool = Depends(apply_rate_limit)):
    return True


def cached(video_id: str, file_type: str):
    try:
        with sqlite3.connect(DB_FILE, timeout=15) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM downloads WHERE video_id=? AND file_type=?", (video_id, file_type)).fetchone()
            if row and os.path.isfile(row["file_path"]) and os.path.getsize(row["file_path"]) > 0:
                if time.time() - row["created_time"] <= CACHE_EXPIRE_HOURS * 3600:
                    return dict(row)
            if row:
                conn.execute("DELETE FROM downloads WHERE id=?", (row["id"],)); conn.commit()
    except Exception as e:
        logger.warning("cache read failed: %s", e)
    return None


def save_cache(data: Dict[str, Any], file_type: str):
    with sqlite3.connect(DB_FILE, timeout=15) as conn:
        conn.execute("""INSERT OR REPLACE INTO downloads
        (video_id,title,file_name,file_path,file_type,file_size,duration,created_time,thumbnail)
        VALUES(?,?,?,?,?,?,?,?,?)""", (data["videoId"], data["title"], data["filename"], data["path"], file_type,
                                        data.get("filesize", 0), data.get("duration", 0), time.time(), data.get("thumbnail", "")))
        conn.commit()


def extract_video_id(url: str) -> Optional[str]:
    if not url: return None
    if re.fullmatch(r"[0-9A-Za-z_-]{11}", url): return url
    m = re.search(r"(?:youtu\.be/|v=|/shorts/|/embed/|/v/)([0-9A-Za-z_-]{11})", url)
    return m.group(1) if m else None


def normalize_url(url: str) -> str:
    vid = extract_video_id(url)
    if vid and not re.match(r"^https?://", url): return f"https://www.youtube.com/watch?v={vid}"
    return url


def ydl_base():
    opts = {
        "outtmpl": f"{DOWNLOAD_DIR}/%(title).150s_%(id)s.%(ext)s",
        "restrictfilenames": True, "noplaylist": True, "retries": RETRIES,
        "fragment_retries": FRAGMENT_RETRIES, "socket_timeout": SOCKET_TIMEOUT,
        "continuedl": True, "js_runtimes": {"node": {}}, "remote_components": ["ejs:github"],
        "quiet": True, "no_warnings": True, "noprogress": True,
        "concurrent_fragment_downloads": CONCURRENT_FRAGMENT_DOWNLOADS,
        "http_chunk_size": HTTP_CHUNK_SIZE,
    }
    if YOUTUBE_USE_COOKIES and os.path.exists(COOKIES_FILE): opts["cookiefile"] = COOKIES_FILE
    return opts


def info_sync(url: str) -> Dict[str, Any]:
    with yt_dlp.YoutubeDL({**ydl_base(), "skip_download": True}) as ydl:
        return ydl.extract_info(normalize_url(url), download=False)


def info_public(info: Dict[str, Any]) -> Dict[str, Any]:
    thumbs = info.get("thumbnails") or []
    thumb = info.get("thumbnail") or (thumbs[-1].get("url") if thumbs else "")
    return {"status": True, "title": info.get("title", ""), "videoId": info.get("id", ""),
            "uploader": info.get("uploader", ""), "channel": info.get("channel", ""),
            "duration": info.get("duration", 0), "thumbnail": thumb,
            "webpage_url": info.get("webpage_url", ""), "view_count": info.get("view_count", 0),
            "upload_date": info.get("upload_date", "")}


def download_sync(url: str, kind: str) -> Dict[str, Any]:
    url = normalize_url(url); vid = extract_video_id(url)
    file_type = "mp3" if kind == "audio" else "mp4"
    if vid:
        c = cached(vid, file_type)
        if c:
            stat_inc("cache_hits")
            return {"status": True, "title": c["title"], "duration": c["duration"], "thumbnail": c["thumbnail"],
                    "filename": c["file_name"], "path": c["file_path"], "download_url": file_url(c["file_name"]),
                    "videoId": vid, "uploader": "Cached", "filesize": c["file_size"]}
    stat_inc(f"{kind}_downloads")
    opts = ydl_base()
    opts["extractor_args"] = {"youtube": [f"player_client={YOUTUBE_PLAYER_CLIENTS}"]}
    opts["nocheckcertificate"] = True
    if kind == "audio":
        opts.update({"format": "bestaudio/best", "postprocessors": [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}],
                     "postprocessor_args": ["-threads", "0", "-vn"]})
    else:
        opts.update({"format": f"bestvideo[height<={MAX_VIDEO_QUALITY}]+bestaudio/best[height<={MAX_VIDEO_QUALITY}]/best",
                     "merge_output_format": "mp4", "postprocessor_args": ["-threads", "0"]})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            prepared = ydl.prepare_filename(info)
            base, _ = os.path.splitext(prepared)
            if kind == "audio":
                path = base + ".mp3"
            else:
                path = next((base + ext for ext in (".mp4", ".webm", ".mkv") if os.path.isfile(base + ext)), base + ".mp4")
            if not os.path.isfile(path) or os.path.getsize(path) == 0: raise RuntimeError("Downloaded file is missing or empty.")
            data = {"status": True, "title": info.get("title", ""), "duration": info.get("duration", 0),
                    "thumbnail": info.get("thumbnail", ""), "filename": os.path.basename(path), "path": path,
                    "download_url": file_url(os.path.basename(path)), "videoId": info.get("id", vid or ""),
                    "uploader": info.get("uploader", ""), "filesize": os.path.getsize(path)}
            save_cache(data, file_type)
            return data
    except yt_dlp.utils.DownloadError as e:
        stat_inc("download_failures")
        raise RuntimeError(f"Download failed: {e}")


def file_url(filename: str) -> str:
    path = f"/files/{quote(filename)}"
    return f"{BASE_URL}{path}" if BASE_URL else path


async def cleanup_task():
    while True:
        try:
            expiry = time.time() - CACHE_EXPIRE_HOURS * 3600
            for entry in os.scandir(DOWNLOAD_DIR):
                if entry.is_file() and entry.stat().st_mtime < expiry:
                    try: os.remove(entry.path)
                    except OSError: pass
            with sqlite3.connect(DB_FILE, timeout=15) as conn:
                conn.execute("DELETE FROM downloads WHERE file_path NOT IN (SELECT file_path FROM downloads)")
                conn.commit()
        except Exception as e: logger.warning("cleanup: %s", e)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if COOKIE_URL:
        try: urllib.request.urlretrieve(COOKIE_URL, COOKIES_FILE)
        except Exception as e: logger.warning("COOKIE_URL failed: %s", e)
    task = asyncio.create_task(cleanup_task())
    yield
    task.cancel()


app = FastAPI(title="Juno X Music API", version="3.1.0", description="Advanced YouTube/YouTube Music API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.get("/")
async def root():
    return {"name":"Juno X Music API", "version":"3.1.0", "status":"online", "docs":"/docs", "health":"/health"}


@app.get("/health")
async def health():
    result = {"status":"healthy", "version":"3.1.0", "yt_dlp_version":yt_dlp.version.__version__,
              "cache_expiry_hours":CACHE_EXPIRE_HOURS, "authentication": bool(API_KEYS), "api_keys_configured": len(API_KEYS), "stats":get_stats()}
    return result


@app.get("/stats")
async def stats(_: bool = Depends(guard)):
    return {"status": True, "version":"3.1.0", "stats":get_stats(), "storage_files":len(os.listdir(DOWNLOAD_DIR))}




@app.get("/admin/keys")
async def admin_keys(_: bool = Depends(require_admin_key)):
    return {"status": True, "configured_keys": len(API_KEYS), "admin_key_configured": bool(ADMIN_API_KEY),
            "hint": "Use API_KEYS for comma-separated client keys; API_KEY remains supported."}

@app.get("/admin/stats")
async def admin_stats(_: bool = Depends(require_admin_key)):
    return {"status": True, "version": "3.1.0", "stats": get_stats(),
            "storage_files": len(os.listdir(DOWNLOAD_DIR)), "configured_keys": len(API_KEYS)}

@app.get("/search")
async def search(q: str = Query(..., min_length=1), limit: int = Query(1, ge=1, le=20), _: bool = Depends(guard)):
    stat_inc("searches")
    try:
        results = await asyncio.to_thread(lambda: YTMusic().search(q, filter="songs", limit=min(limit, MAX_SEARCH_RESULTS)))
        out=[]
        for r in results:
            artists=", ".join(a.get("name","") for a in r.get("artists",[])); th=r.get("thumbnails",[])
            out.append({"title":r.get("title"),"artist":artists,"videoId":r.get("videoId"),"duration":r.get("duration"),"thumbnail":th[-1].get("url") if th else None})
        return out[0] if limit == 1 and out else ({} if limit == 1 else out)
    except Exception as e: raise HTTPException(502, {"error":"Search failed","message":str(e)})


@app.get("/info")
async def info(url: str = Query(...), _: bool = Depends(guard)):
    try: return info_public(await asyncio.to_thread(info_sync, url))
    except Exception as e: raise HTTPException(502, {"error":"Info lookup failed","message":str(e)})


@app.get("/formats")
async def formats(url: str = Query(...), _: bool = Depends(guard)):
    try:
        data = await asyncio.to_thread(info_sync, url)
        formats=[]
        for f in data.get("formats",[]):
            formats.append({"format_id":f.get("format_id"),"ext":f.get("ext"),"resolution":f.get("resolution"),"height":f.get("height"),"fps":f.get("fps"),"vcodec":f.get("vcodec"),"acodec":f.get("acodec"),"filesize":f.get("filesize")})
        return {**info_public(data), "formats":formats}
    except Exception as e: raise HTTPException(502, {"error":"Format lookup failed","message":str(e)})


@app.get("/thumbnail")
async def thumbnail(url: str = Query(...), _: bool = Depends(guard)):
    try:
        data = await asyncio.to_thread(info_sync, url)
        return {"title":data.get("title"),"videoId":data.get("id"),"thumbnail":data.get("thumbnail")}
    except Exception as e: raise HTTPException(502, {"error":"Thumbnail fetch failed","message":str(e)})


@app.get("/download")
async def download(url: str = Query(...), type: Optional[str] = Query(None), _: bool = Depends(guard)):
    kind=(type or "audio").lower().strip()
    if kind not in ("audio","video"): raise HTTPException(400, "type must be audio or video")
    try:
        result=await asyncio.to_thread(download_sync, url, kind)
        if type:
            return FileResponse(result["path"], filename=result["filename"], media_type="audio/mpeg" if kind=="audio" else "video/mp4")
        return result
    except HTTPException: raise
    except Exception as e: raise HTTPException(502, {"error":f"{kind} download failed","message":str(e)})


@app.get("/audio")
async def audio(url: str = Query(...), _: bool = Depends(guard)):
    try: return await asyncio.to_thread(download_sync, url, "audio")
    except Exception as e: raise HTTPException(502, {"error":"Audio download failed","message":str(e)})


@app.get("/video")
async def video(url: str = Query(...), _: bool = Depends(guard)):
    try: return await asyncio.to_thread(download_sync, url, "video")
    except Exception as e: raise HTTPException(502, {"error":"Video download failed","message":str(e)})


@app.get("/files/{filename:path}")
async def get_file(filename: str, _: bool = Depends(guard)):
    safe=os.path.basename(filename); path=os.path.join(DOWNLOAD_DIR,safe)
    if not os.path.isfile(path): raise HTTPException(404,"File not found")
    return FileResponse(path, filename=safe)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
