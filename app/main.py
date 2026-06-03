"""Jarvis Relay — stateless Expo Push proxy + OAuth bounce."""

import base64
import json
import logging
import time
from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlparse

import jwt as jwt_lib
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.alert_service import send_alert
from app.auth import validate_household_jwt
from app.config import get_settings
from app.expo_client import forward_to_expo
from app.rate_limiter import rate_limiter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SendRequest(BaseModel):
    tokens: list[str] = Field(..., min_length=1)
    title: str
    body: str
    data: dict = Field(default_factory=dict)
    priority: str = "high"


class SendResponse(BaseModel):
    status: str
    results: list[dict]


# Allow any printable string; we accept caller-provided household_ids verbatim
# since the relay has no central registry. UUID-ish length cap prevents abuse.
class RegisterRequest(BaseModel):
    household_id: str = Field(..., min_length=1, max_length=128)


class RegisterResponse(BaseModel):
    household_id: str
    jwt: str
    expires_at: int


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    logger.info(
        "Relay starting — household limit %d/hr, token limit %d/hr, burst %d/s",
        settings.rate_limit_per_household_per_hour,
        settings.rate_limit_per_token_per_hour,
        settings.rate_limit_burst_per_second,
    )
    yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="jarvis-relay", lifespan=lifespan)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "jarvis-relay"}


# ---------------------------------------------------------------------------
# OAuth bounce — redirects provider callback to mobile app custom scheme
# ---------------------------------------------------------------------------

# Schemes allowed for OAuth bounce redirect (prevent open redirect attacks)
_ALLOWED_SCHEMES = {"jarvis", "exp", "myapp"}


def decode_oauth_state(raw_state: str) -> tuple[str, str]:
    """Decode a base64url-encoded OAuth state parameter.

    Expected JSON format: {"t": "<csrf_token>", "r": "<redirect_uri>"}

    Returns (csrf_token, redirect_uri).
    Raises HTTPException on invalid format.
    """
    try:
        # Add padding if needed
        padded = raw_state + "=" * (-len(raw_state) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        csrf = payload["t"]
        redirect_uri = payload["r"]
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state parameter: expected base64url JSON with 't' and 'r' keys",
        ) from exc

    # Validate redirect scheme
    parsed = urlparse(redirect_uri)
    if not parsed.scheme or parsed.scheme.lower() in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail=f"Redirect URI must use a custom scheme, not '{parsed.scheme or 'empty'}'",
        )
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise HTTPException(
            status_code=400,
            detail=f"Scheme '{parsed.scheme}' not in allowed list: {sorted(_ALLOWED_SCHEMES)}",
        )

    return csrf, redirect_uri


@app.get("/oauth/bounce")
async def oauth_bounce(
    code: str = Query(...),
    state: str = Query(...),
):
    """Bounce an OAuth callback to a client app's custom URI scheme.

    Google (and other providers) require an HTTPS redirect URI. This endpoint
    receives the callback, extracts the real redirect URI and CSRF token from
    the base64url-encoded state, then 302-redirects to the app's custom scheme.

    State format: base64url({"t": "<csrf_token>", "r": "<redirect_uri>"})

    No tokens are exchanged — just the auth code is forwarded. The client app
    handles the token exchange locally.
    """
    csrf_token, redirect_uri = decode_oauth_state(state)
    params = urlencode({"code": code, "state": csrf_token})

    # Append params to redirect URI (handle existing query string)
    separator = "&" if "?" in redirect_uri else "?"
    target = f"{redirect_uri}{separator}{params}"

    logger.info("OAuth bounce: %s → %s (csrf=%s…)", urlparse(redirect_uri).scheme, urlparse(redirect_uri).netloc or urlparse(redirect_uri).path, csrf_token[:8])
    return RedirectResponse(url=target, status_code=302)


# ---------------------------------------------------------------------------
# Register endpoint — mints household JWTs for self-hosted installs
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    """Best-effort caller IP. Prefer X-Forwarded-For (Fly.io / proxy) over
    the raw socket. We only use this for per-IP rate limiting, not auth."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _mint_household_jwt(household_id: str) -> tuple[str, int]:
    """Mint an HS256 household JWT. Returns (jwt, exp_unix_seconds)."""
    settings = get_settings()
    if not settings.relay_jwt_secret:
        raise HTTPException(status_code=500, detail="Relay JWT secret not configured")
    exp = int(time.time()) + settings.household_jwt_ttl_seconds
    token = jwt_lib.encode(
        {"household_id": household_id, "exp": exp},
        settings.relay_jwt_secret,
        algorithm="HS256",
    )
    return token, exp


@app.post("/v1/register", response_model=RegisterResponse)
async def register_household(req: RegisterRequest, request: Request):
    """Mint a household JWT for a self-hosted install.

    Open endpoint — there's no central registry of household_ids, so we accept
    whatever the caller provides (typically a UUID generated by jarvis-auth at
    first-boot). Per-IP rate limited to prevent abuse. The returned JWT is
    long-lived; the caller is expected to cache it.
    """
    ip = _client_ip(request)
    if not rate_limiter.check_register_limit(ip):
        raise HTTPException(status_code=429, detail="Register rate limit exceeded")

    token, exp = _mint_household_jwt(req.household_id)
    rate_limiter.record_register(ip)
    logger.info("Minted household JWT for %s (ip=%s, exp=%d)", req.household_id, ip, exp)
    return RegisterResponse(household_id=req.household_id, jwt=token, expires_at=exp)


# ---------------------------------------------------------------------------
# Send endpoint
# ---------------------------------------------------------------------------

@app.post("/v1/send", response_model=SendResponse)
async def send_push(
    req: SendRequest,
    household_id: str = Depends(validate_household_jwt),
):
    settings = get_settings()

    # 1. Check suspension
    if rate_limiter.check_suspended(household_id):
        raise HTTPException(status_code=429, detail="Household temporarily suspended")

    # 2. Check burst limit
    if not rate_limiter.check_burst_limit(household_id, len(req.tokens)):
        _record_and_alert(household_id, settings)
        raise HTTPException(status_code=429, detail="Burst rate limit exceeded")

    # 3. Check household hourly limit
    if not rate_limiter.check_household_limit(household_id, len(req.tokens)):
        _record_and_alert(household_id, settings)
        raise HTTPException(status_code=429, detail="Household rate limit exceeded")

    # 4. Filter per-token over-limit tokens
    over_limit = rate_limiter.get_tokens_over_limit(req.tokens)
    allowed_tokens = [t for t in req.tokens if t not in over_limit]

    if not allowed_tokens:
        _record_and_alert(household_id, settings)
        raise HTTPException(status_code=429, detail="All tokens over rate limit")

    # 5. Forward to Expo
    results = await forward_to_expo(
        tokens=allowed_tokens,
        title=req.title,
        body=req.body,
        data=req.data,
        priority=req.priority,
    )

    # 6. Add skipped tokens to results
    for token in over_limit:
        results.append({"token": token, "status": "skipped", "reason": "token_rate_limited"})

    # 7. Record successful request
    rate_limiter.record_request(household_id, allowed_tokens)

    return SendResponse(status="ok", results=results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_and_alert(household_id: str, settings) -> None:
    """Record a 429 hit and send alert if threshold reached."""
    consecutive = rate_limiter.record_rate_limit_hit(household_id)

    if consecutive >= settings.consecutive_429_suspend_threshold:
        level = "critical"
    elif consecutive >= settings.consecutive_429_alert_threshold * 2:
        level = "error"
    elif consecutive >= settings.consecutive_429_alert_threshold:
        level = "warn"
    else:
        return

    # Fire-and-forget — don't await in the request path to avoid slowing response
    import asyncio
    asyncio.ensure_future(send_alert(household_id, consecutive, level))


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
