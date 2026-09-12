# Juno X Music API v3.1 Advanced

Ready for Heroku. Authentication accepts:
- `X-API-Key: <key>`
- `Authorization: Bearer <key>`
- `?api_key=<key>`
- `?key=<key>`

Configure `API_KEY` for one key, or `API_KEYS` for multiple comma-separated client keys. `BOT_API_KEY` remains supported for older bots. Use `ADMIN_API_KEY` for `/admin/keys` and `/admin/stats`.

YouTube cookies are enabled by default with `YOUTUBE_USE_COOKIES=true` and `COOKIES_FILE=cookies.txt`. Put a valid cookies.txt in the project or set `COOKIE_URL`.

## Heroku
Config Vars:
`API_KEY`, `API_KEYS` (optional), `ADMIN_API_KEY`, `YOUTUBE_USE_COOKIES=true`, `COOKIES_FILE=cookies.txt`, `MAX_VIDEO_QUALITY=720`, `CACHE_EXPIRE_HOURS=24`, `RATE_LIMIT=60`, `RATE_WINDOW=60`.

Endpoints: `/`, `/health`, `/docs`, `/search`, `/info`, `/formats`, `/thumbnail`, `/download`, `/audio`, `/video`, `/files/{filename}`, `/stats`, `/admin/keys`, `/admin/stats`.
