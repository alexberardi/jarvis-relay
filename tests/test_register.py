"""Tests for the /v1/register endpoint — household JWT minting for self-hosts."""

import time

import jwt as jwt_lib


class TestRegisterEndpoint:
    def test_register_returns_signed_jwt_for_household(self, client):
        resp = client.post("/v1/register", json={"household_id": "hh-self-hosted-1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["household_id"] == "hh-self-hosted-1"
        assert data["jwt"]
        assert data["expires_at"] > int(time.time())

        # JWT verifies against the relay's test secret and carries our household_id
        payload = jwt_lib.decode(data["jwt"], "test-secret-key", algorithms=["HS256"])
        assert payload["household_id"] == "hh-self-hosted-1"
        assert payload["exp"] == data["expires_at"]

    def test_register_minted_jwt_is_accepted_by_send(self, client, monkeypatch):
        """End-to-end: minted JWT immediately works against /v1/send."""
        from unittest.mock import AsyncMock

        async_mock = AsyncMock(
            return_value=[{"token": "ExponentPushToken[abc]", "status": "ok", "ticket_id": "t1"}]
        )
        monkeypatch.setattr("app.main.forward_to_expo", async_mock)

        reg = client.post("/v1/register", json={"household_id": "hh-roundtrip"})
        token = reg.json()["jwt"]

        resp = client.post(
            "/v1/send",
            json={"tokens": ["ExponentPushToken[abc]"], "title": "T", "body": "B"},
            headers={"Authorization": f"Bearer {token}", "X-Household-Id": "hh-roundtrip"},
        )
        assert resp.status_code == 200

    def test_register_missing_household_id(self, client):
        resp = client.post("/v1/register", json={})
        assert resp.status_code == 422

    def test_register_rejects_empty_household_id(self, client):
        resp = client.post("/v1/register", json={"household_id": ""})
        assert resp.status_code == 422

    def test_register_ip_rate_limited(self, client):
        """After hitting the per-IP register limit, subsequent calls return 429."""
        from app.rate_limiter import RateBucket, rate_limiter

        # TestClient reports 'testclient' as the client host; the limit is 20/hr,
        # so fill the bucket to the cap before the call.
        rate_limiter._ip_register_buckets["testclient"] = RateBucket()
        for _ in range(20):
            rate_limiter._ip_register_buckets["testclient"].add()

        resp = client.post("/v1/register", json={"household_id": "hh-spam"})
        assert resp.status_code == 429
        assert "register" in resp.json()["detail"].lower()

    def _fill(self, ip: str) -> None:
        from app.rate_limiter import RateBucket, rate_limiter

        rate_limiter._ip_register_buckets[ip] = RateBucket()
        for _ in range(20):
            rate_limiter._ip_register_buckets[ip].add()

    def test_register_uses_rightmost_xff_hop(self, client):
        """The limiter keys off the RIGHT-most XFF hop (the trusted proxy's
        view of the client), so filling that bucket blocks the caller."""
        self._fill("172.16.0.1")  # right-most hop

        resp = client.post(
            "/v1/register",
            json={"household_id": "hh-x"},
            headers={"X-Forwarded-For": "10.1.2.3, 172.16.0.1"},
        )
        assert resp.status_code == 429

    def test_register_ignores_spoofable_leftmost_xff(self, client):
        """Filling the LEFT-most (caller-controlled) XFF entry must NOT throttle:
        keying off it would let a single client mint a fresh bucket per request."""
        self._fill("10.1.2.3")  # left-most, attacker-chosen

        resp = client.post(
            "/v1/register",
            json={"household_id": "hh-x"},
            headers={"X-Forwarded-For": "10.1.2.3, 172.16.0.1"},
        )
        assert resp.status_code == 200

    def test_register_prefers_fly_client_ip(self, client):
        """Fly-Client-IP is authoritative and can't be forged; a spoofed XFF is
        ignored when it's present."""
        self._fill("9.9.9.9")

        resp = client.post(
            "/v1/register",
            json={"household_id": "hh-x"},
            headers={
                "Fly-Client-IP": "9.9.9.9",
                "X-Forwarded-For": "1.1.1.1, 2.2.2.2",  # spoof attempt
            },
        )
        assert resp.status_code == 429

    def test_register_jwt_ttl_default_is_bounded(self):
        """The minting TTL default must be short (not the old 10 years) — the
        client transparently re-registers, so a long TTL only widens leak risk."""
        from app.config import Settings

        s = Settings(RELAY_JWT_SECRET="x", _env_file=None)
        assert s.household_jwt_ttl_seconds <= 31 * 24 * 3600
        assert s.household_jwt_ttl_seconds >= 24 * 3600
