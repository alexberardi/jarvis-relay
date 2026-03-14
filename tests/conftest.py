"""Shared fixtures for relay tests."""

import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.rate_limiter import RateLimiter, rate_limiter


def _test_settings() -> Settings:
    return Settings(
        EXPO_ACCESS_TOKEN="test-expo-token",
        RELAY_JWT_SECRET="test-secret-key",
        RATE_LIMIT_PER_HOUSEHOLD_PER_HOUR=100,
        RATE_LIMIT_PER_TOKEN_PER_HOUR=20,
        RATE_LIMIT_BURST_PER_SECOND=10,
        ALERT_WEBHOOK_URL=None,
        CONSECUTIVE_429_ALERT_THRESHOLD=3,
        CONSECUTIVE_429_SUSPEND_THRESHOLD=10,
        SUSPENSION_COOLDOWN_HOURS=1,
    )


@pytest.fixture(autouse=True)
def override_settings():
    """Use test settings for all tests."""
    app.dependency_overrides[get_settings] = _test_settings
    with patch("app.auth.get_settings", return_value=_test_settings()):
        with patch("app.rate_limiter.get_settings", return_value=_test_settings()):
            with patch("app.expo_client.get_settings", return_value=_test_settings()):
                with patch("app.alert_service.get_settings", return_value=_test_settings()):
                    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset rate limiter state between tests."""
    rate_limiter._household_buckets.clear()
    rate_limiter._token_buckets.clear()
    rate_limiter._burst_buckets.clear()
    rate_limiter._household_state.clear()
    yield
    rate_limiter._household_buckets.clear()
    rate_limiter._token_buckets.clear()
    rate_limiter._burst_buckets.clear()
    rate_limiter._household_state.clear()


@pytest.fixture
def valid_jwt_header():
    """Create a valid JWT for testing."""
    import jwt
    token = jwt.encode(
        {"household_id": "test-household-123", "exp": 9999999999},
        "test-secret-key",
        algorithm="HS256",
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-Household-Id": "test-household-123",
    }


@pytest.fixture
def client():
    return TestClient(app)
