# Juno X Music API — Music Bot Compatible

This build uses the same API download contract as the working API package.

## Endpoints
- `GET /health`
- `GET /search?q=QUERY&limit=1`
- `GET /thumbnail?url=YOUTUBE_URL`
- `GET /download?url=YOUTUBE_URL&type=audio`
- `GET /download?url=YOUTUBE_URL&type=video`
- `GET /video?url=YOUTUBE_URL`
- `GET /files/{filename}`

## Important download behavior
`/download` returns JSON by default with:
`status`, `title`, `duration`, `thumbnail`, `filename`, `path`, `download_url`, `videoId`, `uploader`, `filesize`.

When `type=audio` or `type=video` is supplied, it returns the downloaded file directly for legacy clients that expect a file response.

## Authentication
Set `API_KEY` in Heroku. Send it as `X-API-Key`, `Authorization: Bearer ...`, or `?api_key=...`.

## Heroku
Procfile uses `main:app` and Heroku's `$PORT`.
