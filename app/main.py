"""Jarvis Notifications Relay — stateless Expo Push proxy."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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

app = FastAPI(title="jarvis-notifications-relay", lifespan=lifespan)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "jarvis-notifications-relay"}


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
