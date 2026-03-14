"""Expo Push API wrapper with retry and error mapping."""

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
MAX_BATCH_SIZE = 100
MAX_RETRIES = 3


async def forward_to_expo(
    tokens: list[str],
    title: str,
    body: str,
    data: dict,
    priority: str,
) -> list[dict]:
    """Forward notifications to Expo Push API.

    Batches into groups of 100 (Expo's limit).
    Returns per-token results with status and error info.
    """
    settings = get_settings()

    if not settings.expo_access_token:
        logger.error("EXPO_ACCESS_TOKEN not configured")
        return [{"token": t, "status": "error", "error": "relay_not_configured"} for t in tokens]

    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "priority": priority,
            "data": data,
        }
        for token in tokens
    ]

    results: list[dict] = []

    async with httpx.AsyncClient(timeout=30) as client:
        for i in range(0, len(messages), MAX_BATCH_SIZE):
            batch = messages[i:i + MAX_BATCH_SIZE]
            batch_tokens = tokens[i:i + MAX_BATCH_SIZE]

            batch_results = await _send_batch_with_retry(
                client, batch, batch_tokens, settings.expo_access_token
            )
            results.extend(batch_results)

    return results


async def _send_batch_with_retry(
    client: httpx.AsyncClient,
    messages: list[dict],
    tokens: list[str],
    access_token: str,
) -> list[dict]:
    """Send a batch to Expo with retry on transient errors."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.post(
                EXPO_PUSH_URL,
                json=messages,
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if resp.status_code == 429:
                logger.warning("Expo rate limit hit, attempt %d/%d", attempt + 1, MAX_RETRIES)
                continue

            resp.raise_for_status()
            expo_data = resp.json().get("data", [])

            # Map Expo results to our format
            results = []
            for j, item in enumerate(expo_data):
                token = tokens[j] if j < len(tokens) else "unknown"
                if item.get("status") == "ok":
                    results.append({
                        "token": token,
                        "status": "ok",
                        "ticket_id": item.get("id", ""),
                    })
                else:
                    results.append({
                        "token": token,
                        "status": "error",
                        "error": item.get("details", {}).get("error", "unknown"),
                        "message": item.get("message", ""),
                    })
            return results

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < MAX_RETRIES - 1:
                logger.warning(
                    "Expo returned %s, retrying (%d/%d)",
                    exc.response.status_code, attempt + 1, MAX_RETRIES,
                )
                continue
            logger.error("Expo request failed: %s %s", exc.response.status_code, exc.response.text)
            return [{"token": t, "status": "error", "error": f"expo_http_{exc.response.status_code}"} for t in tokens]

        except httpx.RequestError as exc:
            if attempt < MAX_RETRIES - 1:
                logger.warning("Expo request error, retrying (%d/%d): %s", attempt + 1, MAX_RETRIES, exc)
                continue
            logger.error("Expo unreachable after %d attempts: %s", MAX_RETRIES, exc)
            return [{"token": t, "status": "error", "error": "expo_unreachable"} for t in tokens]

    return [{"token": t, "status": "error", "error": "expo_rate_limited"} for t in tokens]
