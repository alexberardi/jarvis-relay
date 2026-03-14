"""Household JWT validation for relay requests."""

import logging

import jwt
from fastapi import Header, HTTPException

from app.config import get_settings

logger = logging.getLogger(__name__)


def validate_household_jwt(
    authorization: str = Header(...),
    x_household_id: str = Header(..., alias="X-Household-Id"),
) -> str:
    """Validate household JWT from Authorization header. Returns household_id."""
    settings = get_settings()

    if not settings.relay_jwt_secret:
        raise HTTPException(status_code=500, detail="Relay JWT secret not configured")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    token = authorization[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Missing JWT token")

    try:
        payload = jwt.decode(token, settings.relay_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    token_household_id = payload.get("household_id")
    if not token_household_id:
        raise HTTPException(status_code=401, detail="Token missing household_id claim")

    if token_household_id != x_household_id:
        raise HTTPException(
            status_code=401,
            detail="household_id in token does not match X-Household-Id header",
        )

    return token_household_id
