"""Webhook alerting for abuse detection."""

import logging
from datetime import datetime, timezone

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


async def send_alert(household_id: str, consecutive_hits: int, level: str) -> None:
    """Send alert via webhook when a household hits rate limits repeatedly.

    Args:
        household_id: The offending household
        consecutive_hits: Number of consecutive 429s
        level: "warn", "error", or "critical"
    """
    settings = get_settings()

    if not settings.alert_webhook_url:
        logger.log(
            _level_to_int(level),
            "Household %s hit rate limit %dx consecutively (no webhook configured)",
            household_id, consecutive_hits,
        )
        return

    emoji = {"warn": "\u26a0\ufe0f", "error": "\u274c", "critical": "\ud83d\udea8"}.get(level, "\u26a0\ufe0f")

    payload = {
        "text": (
            f"{emoji} Household {household_id} hit rate limit "
            f"{consecutive_hits}x consecutively. "
            f"{'Possible abuse or misconfigured client.' if level == 'warn' else ''}"
            f"{'Flagged for review.' if level == 'error' else ''}"
            f"{'SUSPENDED — temporary block applied.' if level == 'critical' else ''}"
        ),
        "household_id": household_id,
        "consecutive_hits": consecutive_hits,
        "level": level,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(settings.alert_webhook_url, json=payload)
            if resp.status_code >= 400:
                logger.error("Alert webhook returned %s", resp.status_code)
    except httpx.RequestError as exc:
        # Webhook failure must not crash the relay
        logger.error("Alert webhook failed: %s", exc)


def _level_to_int(level: str) -> int:
    return {"warn": logging.WARNING, "error": logging.ERROR, "critical": logging.CRITICAL}.get(
        level, logging.WARNING
    )
