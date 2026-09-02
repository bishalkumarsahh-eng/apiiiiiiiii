# Juno X Music — Standalone Powerful API v3

Separate API server for multiple Telegram/music bots. Each bot can have its own API key.

## Environment variables
```env
API_NAME=Juno X Music API
ADMIN_KEY=YOUR_LONG_RANDOM_ADMIN_SECRET
API_DB=/tmp/juno_api.sqlite3
MAX_DURATION=900
MAX_RESULTS=20
DOWNLOAD_TIMEOUT=300
```
`API_KEY` is optional legacy compatibility. New installations should use `ADMIN_KEY` and create individual bot keys.

## Generate a bot API key
Open `/docs`, find **POST /admin/keys/create**, click **Try it out**, enter a label such as `Juno Music Bot`, then click **Execute**.

Authorize the admin request with either `X-Admin-Key: YOUR_ADMIN_KEY` or `Authorization: Bearer YOUR_ADMIN_KEY`.

The response contains a `jx_live_...` key. Save it immediately; the raw key is not stored and cannot be displayed again.

## Manage keys
- `POST /admin/keys/create?label=Juno%20Music%20Bot` — create a key
- `GET /admin/keys` — list keys (metadata only)
- `POST /admin/keys/revoke?key_id=1` — revoke by ID
- `POST /admin/keys/revoke?api_key=jx_live_...` — revoke by key

## Music endpoints
- `GET /health`
- `GET /search?q=...&limit=5`
- `GET /info?url=...`
- `GET /download?url=...&type=audio|video`
- `/docs`

## Connect a bot
```env
MUSIC_API_URL=https://YOUR-API-APP.herokuapp.com
MUSIC_API_KEY=jx_live_YOUR_BOT_KEY
```

Use only media you are authorized to access and comply with applicable platform terms and copyright law.


## YouTube cookie support

If YouTube returns `Sign in to confirm you're not a bot`, the API can optionally use a
Netscape-format YouTube cookies file. Set `YOUTUBE_COOKIES_B64` to the base64-encoded
contents of that cookies file. The API writes it to `/tmp/juno_youtube_cookies.txt`
with restricted permissions and passes it to yt-dlp.

Do not commit cookies to source control or share them. Cookies are authentication
credentials and should be rotated/revoked if exposed. Use cookies only for accounts
and access you are authorized to use and in accordance with YouTube's terms.
