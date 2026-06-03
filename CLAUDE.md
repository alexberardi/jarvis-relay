# jarvis-relay

Stateless cloud relay for Jarvis self-hosted installations. Two responsibilities:

1. **Push notifications** — Receives push requests from `jarvis-notifications`, validates household JWTs, applies rate limiting, forwards to Expo Push API for APNs/FCM delivery.
2. **OAuth bounce** — Receives OAuth provider callbacks (e.g., Google) at an HTTPS endpoint and 302-redirects to `jarvis://auth-complete` so the mobile app intercepts the auth code. No tokens are exchanged — just a URL redirect. This solves the self-hosted OAuth problem where providers require HTTPS redirect URIs.

## Architecture

```
jarvis-notifications (self-hosted)
        │
        ▼  POST /v1/send (household JWT)
jarvis-relay (cloud)
        │
        ▼  POST https://exp.host/--/api/v2/push/send
    Expo Push API → APNs / FCM → device

Google OAuth (or any provider)
        │
        ▼  GET /oauth/bounce?code=xxx&state=yyy
jarvis-relay (cloud)
        │
        ▼  302 → jarvis://auth-complete?code=xxx&state=yyy
    Mobile app intercepts → exchanges code locally
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
| POST | `/v1/register` | None | Mint a household JWT for a self-hosted install (rate-limited per IP) |
| POST | `/v1/send` | Household JWT | Forward push notifications to Expo |
| GET | `/oauth/bounce` | None | Redirect OAuth callback to mobile app custom scheme |

### POST /v1/register

**No auth required.** Open endpoint so self-hosted `jarvis-notifications` can bootstrap without operator intervention. Per-IP rate-limited (default 20/hr).

**Body:**
```json
{ "household_id": "<uuid>" }
```

**Response:**
```json
{ "household_id": "<uuid>", "jwt": "<eyJ...>", "expires_at": <unix_seconds> }
```

The returned JWT is HS256-signed with `RELAY_JWT_SECRET` and contains `{household_id, exp}`. Default TTL is 10 years (`HOUSEHOLD_JWT_TTL_SECONDS`). The relay has no central registry — callers self-attest their `household_id` (typically a UUID from `jarvis-auth`). UUID collisions are negligible, and Expo push tokens are device-specific so a guessed household_id confers no useful attack surface.

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

### GET /oauth/bounce

**No auth required.** Public endpoint — just a 302 redirect.

**Query params:**
- `code` (required) — OAuth authorization code from provider
- `state` (required) — Base64url-encoded JSON: `{"t": "<csrf_token>", "r": "<redirect_uri>"}`

**Response:** `302` redirect to `<redirect_uri>?code=xxx&state=<csrf_token>`

**State encoding:** The client app encodes both the CSRF token and its target redirect URI into the OAuth `state` parameter:
```python
import base64, json
state = base64.urlsafe_b64encode(json.dumps({
    "t": "random-csrf-token",
    "r": "jarvis://auth-complete"
}).encode()).decode().rstrip("=")
```

The relay decodes state, validates the redirect scheme (must be a custom scheme like `jarvis://`, not `http`/`https`), and 302-redirects with the code and original CSRF token.

**Allowed schemes:** `jarvis`, `exp`, `myapp` (configurable in `_ALLOWED_SCHEMES`)

**Usage:** Register `https://<relay-host>/oauth/bounce` as the redirect URI in your OAuth provider (e.g., Google Cloud Console). Any app can use the same endpoint — just encode its own callback URI in the state parameter.

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
.venv/bin/pytest -v           # 49 tests
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
