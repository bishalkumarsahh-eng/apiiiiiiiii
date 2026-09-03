# Juno X Music API v4 — Heroku Ready

Standalone API for multiple Telegram/music bots. Each bot can have its own API key.

## Features
- `/search`, `/info`, `/download`
- Separate bot API keys with create/list/revoke admin endpoints
- YouTube Netscape cookies via `YOUTUBE_COOKIES_B64`
- Automatic YouTube client fallback (`web_safari`, `mweb`, `tv`, `android_vr`, `web_embedded` by default)
- FFmpeg detection on `/health`
- Heroku web dyno ready
- Temporary download files cleaned automatically

Current yt-dlp documentation notes that YouTube is rolling out Proof-of-Origin (PO) Token enforcement and recommends a PO Token Provider for clients that require it. This API therefore uses a client fallback strategy and exposes `YOUTUBE_PLAYER_CLIENTS`; if YouTube requires a PO token for a particular request, an external provider may still be necessary.

## Heroku buildpacks
1. Python buildpack
2. FFmpeg buildpack

Recommended FFmpeg buildpack:
`https://github.com/jonathanong/heroku-buildpack-ffmpeg-latest.git`

Do **not** add `ffmpeg` to `requirements.txt`; the buildpack supplies the system binary.

## Environment
```env
API_NAME=Juno X Music API
ADMIN_KEY=YOUR_LONG_RANDOM_ADMIN_SECRET
API_DB=/tmp/juno_api.sqlite3
MAX_DURATION=900
MAX_RESULTS=20
DOWNLOAD_TIMEOUT=300
YOUTUBE_COOKIES_B64=
YOUTUBE_COOKIES_FILE=/tmp/juno_youtube_cookies.txt
YOUTUBE_PLAYER_CLIENTS=web_safari,mweb,tv,android_vr,web_embedded
```

`API_KEY` remains supported only for legacy compatibility.

## Create a bot key
Use `/docs` → `POST /admin/keys/create` and authorize with `X-Admin-Key` or `Authorization: Bearer`.

## Bot endpoints
```text
GET /search?q=QUERY&limit=5&api_key=BOT_KEY
GET /info?url=VIDEO_ID_OR_URL&api_key=BOT_KEY
GET /download?url=VIDEO_ID_OR_URL&type=audio&api_key=BOT_KEY
GET /download?url=VIDEO_ID_OR_URL&type=video&api_key=BOT_KEY
```

For production, prefer the `X-API-Key` header over putting the key in URLs when your bot code allows it.

## YouTube cookies
The cookie variable must contain a Base64-encoded Netscape/Mozilla cookie file. Do not commit or share the cookie file. If cookies are exposed, revoke/rotate the associated session.

Fresh cookies should be exported from a browser session according to yt-dlp's current guidance. Avoid opening the YouTube session again after exporting when using the private-session method, because YouTube may rotate cookies.

## Important limitation
No downloader can guarantee permanent YouTube access. YouTube can change its anti-bot, PO-token, client, or format requirements. If a request is still rejected after fresh cookies and the fallback clients, deploy a dedicated PO Token Provider alongside the API or use another supported extraction source.

Use only content and accounts you are authorized to access and comply with applicable platform terms and copyright law.
