# Juno X Music API v3 — Advanced / Heroku Ready

## Required Heroku Config Var
Set exactly:

`API_KEY=jx_live_...`

Generate a key locally with:

`python generate_key.py`

For backward compatibility, `BOT_API_KEY` is accepted only when `API_KEY` is absent.

## Authentication
Protected endpoints accept any one of:

- `X-API-Key: YOUR_KEY`
- `Authorization: Bearer YOUR_KEY`
- `?api_key=YOUR_KEY`

## Endpoints
- `GET /` — API information
- `GET /health` — public health/status
- `GET /search?q=...&limit=1`
- `GET /info?url=...`
- `GET /formats?url=...`
- `GET /thumbnail?url=...`
- `GET /download?url=...` — JSON audio result
- `GET /download?url=...&type=audio` — direct MP3 file
- `GET /download?url=...&type=video` — direct MP4/video file
- `GET /audio?url=...`
- `GET /video?url=...`
- `GET /files/{filename}`
- `GET /stats` — protected usage statistics
- `/docs` — Swagger UI

## Notes
FFmpeg is required by yt-dlp for MP3 conversion and video merging. Use a Heroku FFmpeg buildpack or another system package source if your stack does not already contain FFmpeg.

The API keeps SQLite cache metadata, automatically removes expired download files, limits abusive request rates, and preserves the old Music Bot `/download?type=audio|video` contract.
