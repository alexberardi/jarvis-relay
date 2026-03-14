# jarvis-notifications-relay

Stateless Expo Push API proxy. Receives push notification requests from `jarvis-notifications`, validates household JWTs, applies rate limiting, and forwards to Expo's Push API for APNs/FCM delivery.

## Architecture

```
jarvis-notifications (self-hosted)
        │
        ▼  POST /v1/send (household JWT)
jarvis-notifications-relay (cloud/local)
        │
        ▼  POST https://exp.host/--/api/v2/push/send
    Expo Push API → APNs / FCM → device
```

**Stateless** — no database, no persistent storage. All state (rate limits, abuse tracking) is in-memory and resets on restart.

## Running

```bash
# Dev (local)
cd jarvis-notifications-relay
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env  # fill in EXPO_ACCESS_TOKEN, RELAY_JWT_SECRET
.venv/bin/uvicorn app.main:app --reload --port 8080

# Docker
docker build -t jarvis-notifications-relay .
docker run -p 8080:8080 --env-file .env jarvis-notifications-relay
```

## API

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | None | Health check |
| POST | `/v1/send` | Household JWT | Forward push notifications to Expo |

### POST /v1/send

**Headers:**
- `Authorization: Bearer <household_jwt>`
- `X-Household-Id: <household_id>`

**Body:**
```json
{
  "tokens": ["ExponentPushToken[xxx]"],
  "title": "Notification title",
  "body": "Notification body",
  "data": {"key": "value"},
  "priority": "high"
}
```

## Rate Limiting (In-Memory)

| Limit | Default | Window |
|-------|---------|--------|
| Per household | 100 req/hr | 1 hour sliding |
| Per token | 20 req/hr | 1 hour sliding |
| Burst | 10 req/s | 1 second |

### Abuse Escalation

| Consecutive 429s | Action |
|------------------|--------|
| 3+ | Warn alert (webhook) |
| 6+ | Error alert |
| 10+ | Suspend household (1hr cooldown) |

## Testing

```bash
.venv/bin/pytest -v           # 36 tests
.venv/bin/pytest --cov=app    # with coverage
```

## Environment Variables

See `.env.example` for all options. Required:
- `EXPO_ACCESS_TOKEN` — Expo push token
- `RELAY_JWT_SECRET` — shared secret with jarvis-notifications for household JWTs

## Key Files

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app, `/v1/send` endpoint |
| `app/auth.py` | Household JWT validation |
| `app/rate_limiter.py` | In-memory rate limiting + abuse detection |
| `app/expo_client.py` | Expo Push API wrapper with retry |
| `app/alert_service.py` | Webhook alerting for abuse |
| `app/config.py` | Pydantic settings |

## TODO

- [ ] **Fly.io deployment** — Deploy to Fly.io for production (1 machine, `iad` region). See `Dockerfile` and `run.sh`. Free tier allows 3 machines total.
- [ ] **Expo receipt checking** — Poll Expo receipts API to detect invalid tokens for cleanup feedback to jarvis-notifications.
