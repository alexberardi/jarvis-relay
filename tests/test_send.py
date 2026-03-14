"""Tests for the /v1/send endpoint."""

from unittest.mock import AsyncMock, patch


class TestSendEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @patch("app.main.forward_to_expo", new_callable=AsyncMock)
    def test_send_success(self, mock_expo, client, valid_jwt_header):
        mock_expo.return_value = [
            {"token": "ExponentPushToken[abc]", "status": "ok", "ticket_id": "ticket-1"},
        ]
        resp = client.post(
            "/v1/send",
            json={
                "tokens": ["ExponentPushToken[abc]"],
                "title": "Test",
                "body": "Hello",
            },
            headers=valid_jwt_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "ok"

    def test_send_missing_auth(self, client):
        resp = client.post(
            "/v1/send",
            json={
                "tokens": ["ExponentPushToken[abc]"],
                "title": "Test",
                "body": "Hello",
            },
        )
        assert resp.status_code == 422  # Missing required headers

    @patch("app.main.forward_to_expo", new_callable=AsyncMock)
    def test_send_empty_tokens(self, mock_expo, client, valid_jwt_header):
        resp = client.post(
            "/v1/send",
            json={
                "tokens": [],
                "title": "Test",
                "body": "Hello",
            },
            headers=valid_jwt_header,
        )
        assert resp.status_code == 422  # min_length=1 validation

    @patch("app.main.forward_to_expo", new_callable=AsyncMock)
    def test_send_with_data_and_priority(self, mock_expo, client, valid_jwt_header):
        mock_expo.return_value = [
            {"token": "t1", "status": "ok", "ticket_id": "tid-1"},
        ]
        resp = client.post(
            "/v1/send",
            json={
                "tokens": ["t1"],
                "title": "Alert",
                "body": "Something happened",
                "data": {"action": "open_screen", "screen": "alerts"},
                "priority": "normal",
            },
            headers=valid_jwt_header,
        )
        assert resp.status_code == 200
        mock_expo.assert_called_once_with(
            tokens=["t1"],
            title="Alert",
            body="Something happened",
            data={"action": "open_screen", "screen": "alerts"},
            priority="normal",
        )

    @patch("app.main.forward_to_expo", new_callable=AsyncMock)
    def test_send_suspended_household(self, mock_expo, client, valid_jwt_header):
        """Suspended households get 429."""
        from app.rate_limiter import rate_limiter
        import time

        state = rate_limiter._get_state("test-household-123")
        state.suspended_until = time.time() + 3600

        resp = client.post(
            "/v1/send",
            json={"tokens": ["t1"], "title": "T", "body": "B"},
            headers=valid_jwt_header,
        )
        assert resp.status_code == 429
        assert "suspended" in resp.json()["detail"].lower()
        mock_expo.assert_not_called()

    @patch("app.main.forward_to_expo", new_callable=AsyncMock)
    def test_send_household_rate_limited(self, mock_expo, client, valid_jwt_header):
        """Household over hourly limit gets 429."""
        from app.rate_limiter import RateBucket, rate_limiter

        rate_limiter._household_buckets["test-household-123"] = RateBucket()
        for _ in range(100):
            rate_limiter._household_buckets["test-household-123"].add()

        resp = client.post(
            "/v1/send",
            json={"tokens": ["t1"], "title": "T", "body": "B"},
            headers=valid_jwt_header,
        )
        assert resp.status_code == 429
        mock_expo.assert_not_called()

    @patch("app.main.forward_to_expo", new_callable=AsyncMock)
    def test_send_skips_over_limit_tokens(self, mock_expo, client, valid_jwt_header):
        """Tokens over per-token limit are skipped, others forwarded."""
        from app.rate_limiter import RateBucket, rate_limiter

        # Put t-over at its limit
        rate_limiter._token_buckets["t-over"] = RateBucket()
        for _ in range(20):
            rate_limiter._token_buckets["t-over"].add()

        mock_expo.return_value = [
            {"token": "t-ok", "status": "ok", "ticket_id": "tid-1"},
        ]

        resp = client.post(
            "/v1/send",
            json={"tokens": ["t-ok", "t-over"], "title": "T", "body": "B"},
            headers=valid_jwt_header,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2

        # t-ok was forwarded
        ok_result = next(r for r in data["results"] if r["token"] == "t-ok")
        assert ok_result["status"] == "ok"

        # t-over was skipped
        skipped_result = next(r for r in data["results"] if r["token"] == "t-over")
        assert skipped_result["status"] == "skipped"

    @patch("app.main.forward_to_expo", new_callable=AsyncMock)
    def test_send_all_tokens_over_limit(self, mock_expo, client, valid_jwt_header):
        """If all tokens are over limit, return 429."""
        from app.rate_limiter import RateBucket, rate_limiter

        rate_limiter._token_buckets["t1"] = RateBucket()
        for _ in range(20):
            rate_limiter._token_buckets["t1"].add()

        resp = client.post(
            "/v1/send",
            json={"tokens": ["t1"], "title": "T", "body": "B"},
            headers=valid_jwt_header,
        )
        assert resp.status_code == 429
        mock_expo.assert_not_called()
