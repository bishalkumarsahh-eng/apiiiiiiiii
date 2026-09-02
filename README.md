# Juno X Music API v3

Standalone API for multiple Telegram/music bots.

## Features
- Multiple independent API keys
- Per-key rate limiting
- Key enable/revoke management
- Search, metadata and audio/video download
- Concurrency protection for many bots
- Request IDs and response timing headers
- FastAPI Swagger docs at `/docs`

## Deploy
Set these environment variables:

`ADMIN_KEY` = a strong secret used only for key management.

Optional tuning:
- `MAX_RESULTS=20`
- `MAX_CONCURRENT_DOWNLOADS=4`
- `MAX_CONCURRENT_LOOKUPS=12`
- `RATE_LIMIT_PER_MINUTE=120`
- `MAX_DURATION=900`
- `DOWNLOAD_TIMEOUT=300`

Install FFmpeg on the server because audio conversion/video merging uses FFmpeg.

## Create an API key
Send an authenticated POST request to `/admin/keys` with `x-admin-key`.

Example:
```bash
curl -X POST 'https://YOUR-API/admin/keys?label=telegram-bot-1' -H 'x-admin-key: YOUR_ADMIN_KEY'
```

Save the returned API key. The server stores only its hash.

## Use from any bot
Header:
```text
X-API-Key: YOUR_API_KEY
```
or
```text
Authorization: Bearer YOUR_API_KEY
```

Endpoints:
- `GET /search?q=term&limit=10`
- `GET /info?url=...`
- `GET /download?url=...&type=audio&quality=192`
- `GET /download?url=...&type=video`
- `GET /health`
- `GET /docs`

## Scaling
For a large number of bots, use a persistent database and shared cache/rate limiter (Redis/PostgreSQL) instead of `/tmp` SQLite. Run multiple API instances behind a load balancer and keep FFmpeg installed on every worker.
