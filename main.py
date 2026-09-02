import os, re, time, sqlite3, hashlib, secrets, asyncio, shutil, uuid
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

import yt_dlp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

APP_NAME = os.getenv("API_NAME", "Juno X Music API")
DB_PATH = os.getenv("API_DB", "/tmp/juno_api.sqlite3")
MAX_DURATION = int(os.getenv("MAX_DURATION", "900"))
MAX_RESULTS = int(os.getenv("MAX_RESULTS", "20"))
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "4"))
MAX_CONCURRENT_LOOKUPS = int(os.getenv("MAX_CONCURRENT_LOOKUPS", "12"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
DOWNLOAD_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "300"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "300"))
ADMIN_KEY = os.getenv("ADMIN_KEY", "").strip()

app = FastAPI(title=APP_NAME, version="3.0.0", docs_url="/docs", redoc_url="/redoc")
download_sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
lookup_sem = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)


def db_init():
    with sqlite3.connect(DB_PATH) as db:
        db.execute("CREATE TABLE IF NOT EXISTS keys (key_hash TEXT PRIMARY KEY, label TEXT, created INTEGER, enabled INTEGER DEFAULT 1, requests INTEGER DEFAULT 0, last_used INTEGER DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS cache (cache_key TEXT PRIMARY KEY, expires INTEGER, data TEXT)")
        db.commit()


def hash_key(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


def make_key(prefix="juno"):
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def add_key(label="bot"):
    key = make_key()
    with sqlite3.connect(DB_PATH) as db:
        db.execute("INSERT INTO keys(key_hash,label,created,enabled) VALUES(?,?,?,1)", (hash_key(key), label[:100], int(time.time())))
        db.commit()
    return key


def authenticate(request: Request):
    key = request.headers.get("x-api-key", "").strip()
    if not key:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip()
    if not key:
        key = request.query_params.get("api_key", "").strip()
    if not key:
        raise HTTPException(401, "API key required")
    kh = hash_key(key)
    now = int(time.time())
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute("SELECT enabled,requests,last_used FROM keys WHERE key_hash=?", (kh,)).fetchone()
        if not row or not row[0]:
            raise HTTPException(401, "Invalid or disabled API key")
        # Fixed-window rate limiting per key.
        if row[2] > now - 60 and row[1] >= RATE_LIMIT_PER_MINUTE:
            raise HTTPException(429, "Rate limit exceeded. Try again later.", headers={"Retry-After": "60"})
        db.execute("UPDATE keys SET requests=requests+1,last_used=? WHERE key_hash=?", (now, kh))
        db.commit()
    return kh


def admin(request: Request):
    key = request.headers.get("x-admin-key", "").strip() or request.query_params.get("admin_key", "").strip()
    if not ADMIN_KEY or not key or not secrets.compare_digest(key, ADMIN_KEY):
        raise HTTPException(403, "Admin authentication required")


def normalize_url(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return f"https://www.youtube.com/watch?v={value}"
    if re.match(r"^https?://(www\.)?(youtube\.com|youtu\.be)/", value):
        return value
    raise HTTPException(400, "Only YouTube URLs or video IDs are supported")


def search_sync(query: str, limit: int):
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        data = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    results = []
    for item in data.get("entries") or []:
        if item and item.get("id"):
            results.append({"id": item["id"], "title": item.get("title"), "url": item.get("webpage_url") or f"https://www.youtube.com/watch?v={item['id']}", "duration": item.get("duration"), "channel": item.get("channel") or item.get("uploader"), "thumbnail": item.get("thumbnail")})
    return results


def info_sync(url: str):
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}) as ydl:
        return ydl.extract_info(normalize_url(url), download=False)


def download_sync(url: str, media_type: str, workdir: str):
    Path(workdir).mkdir(parents=True, exist_ok=True)
    common = {"quiet": True, "no_warnings": True, "noplaylist": True, "outtmpl": str(Path(workdir) / "%(id)s.%(ext)s"), "retries": 4, "fragment_retries": 4, "socket_timeout": 30, "concurrent_fragment_downloads": 4}
    if media_type == "audio":
        common.update({"format": "bestaudio/best", "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]})
        extension = ".mp3"
    else:
        common.update({"format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best[height<=1080]", "merge_output_format": "mp4"})
        extension = ".mp4"
    with yt_dlp.YoutubeDL(common) as ydl:
        info = ydl.extract_info(normalize_url(url), download=True)
    duration = info.get("duration")
    if duration and duration > MAX_DURATION:
        raise ValueError(f"Duration exceeds {MAX_DURATION} seconds")
    vid = info.get("id")
    matches = [Path(workdir) / f"{vid}{extension}"] + [p for p in Path(workdir).glob(f"{vid}.*") if p.is_file() and p.suffix not in {".part", ".ytdl"}]
    target = next((p for p in matches if p.exists()), None)
    if not target:
        raise FileNotFoundError("Downloaded file not found")
    return target, info


def safe_title(s):
    return re.sub(r'[\\"\r\n/:*?<>|]', "", s or "audio").strip()[:120] or "audio"

@app.on_event("startup")
async def startup():
    db_init()
    if not ADMIN_KEY:
        print("WARNING: ADMIN_KEY is not configured; key management is disabled.")

@app.middleware("http")
async def request_meta(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]
    request.state.request_id = rid
    start = time.time()
    try:
        response = await call_next(request)
    except HTTPException as e:
        response = JSONResponse({"success": False, "error": e.detail, "request_id": rid}, status_code=e.status_code, headers=e.headers)
    except Exception:
        response = JSONResponse({"success": False, "error": "Internal server error", "request_id": rid}, status_code=500)
    response.headers["X-Request-ID"] = rid
    response.headers["X-API-Version"] = "3.0.0"
    response.headers["X-Response-Time"] = f"{time.time()-start:.3f}s"
    return response

@app.get("/")
async def root():
    return {"ok": True, "service": APP_NAME, "version": "3.0.0", "features": ["api_keys", "rate_limits", "search", "info", "audio", "video", "concurrency_control", "request_ids"]}

@app.get("/health")
async def health():
    return {"ok": True, "service": APP_NAME, "version": "3.0.0"}

@app.get("/search")
async def search(request: Request, q: str, limit: int = 5):
    authenticate(request)
    q = q.strip()
    if not q or len(q) > 200: raise HTTPException(400, "Query must contain 1-200 characters")
    limit = max(1, min(limit, MAX_RESULTS))
    async with lookup_sem:
        try: results = await asyncio.to_thread(search_sync, q, limit)
        except Exception as e: raise HTTPException(502, "YouTube search failed") from e
    return {"success": True, "query": q, "count": len(results), "results": results}

@app.get("/info")
async def info(request: Request, url: str):
    authenticate(request)
    async with lookup_sem:
        try: data = await asyncio.to_thread(info_sync, url)
        except HTTPException: raise
        except Exception as e: raise HTTPException(502, "Unable to fetch media information") from e
    duration = data.get("duration")
    if duration and duration > MAX_DURATION: raise HTTPException(400, f"Duration exceeds {MAX_DURATION} seconds")
    return {"success": True, "id": data.get("id"), "title": data.get("title"), "duration": duration, "channel": data.get("channel") or data.get("uploader"), "thumbnail": data.get("thumbnail"), "webpage_url": data.get("webpage_url")}

@app.get("/download")
async def download(request: Request, url: str, type: str = "audio", quality: str = "192"):
    authenticate(request)
    if type not in {"audio", "video"}: raise HTTPException(400, "type must be audio or video")
    if type == "audio" and quality not in {"128", "192", "320"}: raise HTTPException(400, "audio quality must be 128, 192 or 320")
    workdir = str(Path("/tmp") / "juno_api" / uuid.uuid4().hex)
    async with download_sem:
        try:
            path, info = await asyncio.wait_for(asyncio.to_thread(download_sync, url, type, workdir), timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError: raise HTTPException(504, "Download timed out")
        except HTTPException: raise
        except ValueError as e: raise HTTPException(400, str(e))
        except Exception as e: raise HTTPException(502, "Download failed") from e
    media = "audio/mpeg" if path.suffix.lower() == ".mp3" else "video/mp4"
    title = safe_title(info.get("title"))
    def stream():
        try:
            with path.open("rb") as f:
                while chunk := f.read(1024 * 1024): yield chunk
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return StreamingResponse(stream(), media_type=media, headers={"Content-Disposition": f'attachment; filename="{title}{path.suffix}"', "Cache-Control": "no-store"})

@app.post("/admin/keys")
async def create_api_key(request: Request, label: str = "bot"):
    admin(request)
    key = add_key(label)
    return {"success": True, "api_key": key, "label": label, "warning": "Save this key now. It cannot be recovered later."}

@app.get("/admin/keys")
async def list_api_keys(request: Request):
    admin(request)
    with sqlite3.connect(DB_PATH) as db:
        rows = db.execute("SELECT label,created,enabled,requests,last_used FROM keys ORDER BY created DESC").fetchall()
    return {"success": True, "keys": [{"label":r[0],"created":r[1],"enabled":bool(r[2]),"requests":r[3],"last_used":r[4]} for r in rows]}

@app.post("/admin/keys/revoke")
async def revoke_api_key(request: Request, api_key: str):
    admin(request)
    with sqlite3.connect(DB_PATH) as db:
        cur = db.execute("UPDATE keys SET enabled=0 WHERE key_hash=?", (hash_key(api_key),))
        db.commit()
    return {"success": cur.rowcount > 0, "revoked": cur.rowcount > 0}

@app.post("/admin/keys/enable")
async def enable_api_key(request: Request, api_key: str):
    admin(request)
    with sqlite3.connect(DB_PATH) as db:
        cur = db.execute("UPDATE keys SET enabled=1 WHERE key_hash=?", (hash_key(api_key),))
        db.commit()
    return {"success": cur.rowcount > 0, "enabled": cur.rowcount > 0}
