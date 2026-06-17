# jarvis-relay

Internet-facing, stateless cloud relay for Jarvis self-hosted installations. It lets a
self-hosted Jarvis stack reach Expo's push infrastructure (and complete mobile OAuth)
without exposing the home network.

## Role

- **Expo push fan-out** — receives push requests from `jarvis-notifications` and forwards
  them to the [Expo Push API](https://exp.host/--/api/v2/push/send) for APNs / FCM delivery.
- **Household-JWT auth** — `/v1/send` requires an HS256 household JWT signed with
  `RELAY_JWT_SECRET`. Households self-register via `/v1/register` to mint a token.
- **Rate limiting** — in-memory per-household / per-token / burst limits with abuse
  escalation. No database; all state is in memory and resets on restart.
- **OAuth bounce** — `/oauth/bounce` 302-redirects provider callbacks to the app's custom
  scheme (e.g. `jarvis://auth-complete`) so self-hosted installs can use HTTPS redirect URIs.

See [CLAUDE.md](CLAUDE.md) for the full API surface, rate-limit tables, and architecture diagrams.

## Deploy

Runs on [Fly.io](https://fly.io) (see `fly.toml`):

```bash
fly deploy
```

Set the production secrets once (not committed):

```bash
fly secrets set RELAY_JWT_SECRET=... EXPO_ACCESS_TOKEN=...
```

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # fill in the values below
.venv/bin/uvicorn app.main:app --reload --port 8080
```

## Environment variables

See `.env.example` for the full list. Required:

| Variable | Purpose |
|----------|---------|
| `RELAY_JWT_SECRET` | Shared secret for signing / validating household JWTs (must match `jarvis-notifications`). |
| `EXPO_ACCESS_TOKEN` | Expo access token used to authenticate to the Expo Push API. |

Optional (rate limiting, alerting, port) are documented in `.env.example`.

## Testing

```bash
.venv/bin/pytest -v
```
