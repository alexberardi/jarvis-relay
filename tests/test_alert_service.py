"""Tests for alert webhook service."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.alert_service import send_alert, _level_to_int


class TestAlertService:
    @pytest.mark.asyncio
    async def test_no_webhook_configured_logs_only(self, caplog):
        """When no webhook URL, alert is only logged."""
        import logging
        with caplog.at_level(logging.WARNING):
            await send_alert("hh-123", 3, "warn")
        assert "hh-123" in caplog.text

    @pytest.mark.asyncio
    @patch("app.alert_service.httpx.AsyncClient")
    async def test_webhook_sends_payload(self, mock_client_cls):
        """When webhook URL configured, POST is sent."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.alert_service.get_settings") as mock_settings:
            settings = MagicMock()
            settings.alert_webhook_url = "https://hooks.example.com/test"
            mock_settings.return_value = settings

            await send_alert("hh-456", 5, "error")

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://hooks.example.com/test"
            payload = call_args[1]["json"]
            assert payload["household_id"] == "hh-456"
            assert payload["consecutive_hits"] == 5
            assert payload["level"] == "error"

    @pytest.mark.asyncio
    @patch("app.alert_service.httpx.AsyncClient")
    async def test_webhook_failure_does_not_raise(self, mock_client_cls):
        """Webhook errors are caught — relay must not crash."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.RequestError("connection refused")
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch("app.alert_service.get_settings") as mock_settings:
            settings = MagicMock()
            settings.alert_webhook_url = "https://hooks.example.com/test"
            mock_settings.return_value = settings

            # Should not raise
            await send_alert("hh-789", 10, "critical")


class TestLevelToInt:
    def test_warn(self):
        import logging
        assert _level_to_int("warn") == logging.WARNING

    def test_error(self):
        import logging
        assert _level_to_int("error") == logging.ERROR

    def test_critical(self):
        import logging
        assert _level_to_int("critical") == logging.CRITICAL

    def test_unknown_defaults_to_warning(self):
        import logging
        assert _level_to_int("unknown") == logging.WARNING
