# Juno X Music — Standalone API

This is a **separate API server**. It does not contain or require the Telegram bot.

Your Telegram music bot can call this server with one API URL and one API key.

## 1. Generate your API key

Run:

```bash
python generate_key.py
```

Copy the generated value into `API_KEY`.

## 2. Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

API URL:

```text
http://localhost:8000
```

## 3. Connect your Telegram music bot

Set these variables in your music bot:

```env
MUSIC_API_URL=https://YOUR-API-APP.example.com
MUSIC_API_KEY=YOUR_GENERATED_KEY
ENABLE_API=True
```

The existing Juno X Music API downloader is compatible with:

```text
GET /download?url=VIDEO_ID&type=audio&api_key=YOUR_KEY
GET /download?url=VIDEO_ID&type=video&api_key=YOUR_KEY
```

The API also supports the safer header forms:

```text
X-API-Key: YOUR_KEY
```

or:

```text
Authorization: Bearer YOUR_KEY
```

## Endpoints

- `GET /health` — health check, no key required
- `GET /search?q=...` — YouTube search
- `GET /info?url=...` — video information
- `GET /download?url=...&type=audio|video` — binary media response
- `/docs` — interactive API documentation

## Render / Heroku

Use the `Procfile` web process. Set `API_KEY` as a secret environment variable.

Do not put your real API key into GitHub or the ZIP.

Use this service only for media you are authorized to access and in accordance with applicable platform terms and copyright law.
