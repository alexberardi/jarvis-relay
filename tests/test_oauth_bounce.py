"""Tests for the OAuth bounce endpoint."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app, _ALLOWED_SCHEMES


def _encode_state(csrf: str, redirect_uri: str) -> str:
    """Encode a state parameter the way a client app would."""
    payload = json.dumps({"t": csrf, "r": redirect_uri})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


class TestOAuthBounce:
    def test_redirects_to_custom_scheme(self, client: TestClient):
        state = _encode_state("csrf123", "jarvis://auth-complete")
        resp = client.get(f"/oauth/bounce?code=abc123&state={state}")
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith("jarvis://auth-complete?")
        assert "code=abc123" in location
        assert "state=csrf123" in location

    def test_preserves_existing_query_params(self, client: TestClient):
        state = _encode_state("csrf123", "jarvis://auth-complete?session=xyz")
        resp = client.get(f"/oauth/bounce?code=abc&state={state}")
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "session=xyz" in location
        assert "code=abc" in location
        assert "&code=" in location  # appended with & not ?

    def test_missing_code_returns_422(self, client: TestClient):
        state = _encode_state("csrf123", "jarvis://auth-complete")
        resp = client.get(f"/oauth/bounce?state={state}")
        assert resp.status_code == 422

    def test_missing_state_returns_422(self, client: TestClient):
        resp = client.get("/oauth/bounce?code=abc123")
        assert resp.status_code == 422

    def test_missing_both_returns_422(self, client: TestClient):
        resp = client.get("/oauth/bounce")
        assert resp.status_code == 422

    def test_invalid_state_not_base64(self, client: TestClient):
        resp = client.get("/oauth/bounce?code=abc&state=not-valid-json!!!")
        assert resp.status_code == 400
        assert "Invalid state" in resp.json()["detail"]

    def test_invalid_state_missing_keys(self, client: TestClient):
        raw = base64.urlsafe_b64encode(json.dumps({"wrong": "keys"}).encode()).decode()
        resp = client.get(f"/oauth/bounce?code=abc&state={raw}")
        assert resp.status_code == 400

    def test_rejects_http_scheme(self, client: TestClient):
        state = _encode_state("csrf123", "http://evil.com/steal")
        resp = client.get(f"/oauth/bounce?code=abc&state={state}")
        assert resp.status_code == 400
        assert "custom scheme" in resp.json()["detail"]

    def test_rejects_https_scheme(self, client: TestClient):
        state = _encode_state("csrf123", "https://evil.com/steal")
        resp = client.get(f"/oauth/bounce?code=abc&state={state}")
        assert resp.status_code == 400
        assert "custom scheme" in resp.json()["detail"]

    def test_rejects_unknown_custom_scheme(self, client: TestClient):
        state = _encode_state("csrf123", "evilapp://callback")
        resp = client.get(f"/oauth/bounce?code=abc&state={state}")
        assert resp.status_code == 400
        assert "not in allowed list" in resp.json()["detail"]

    def test_allowed_schemes_include_jarvis(self):
        assert "jarvis" in _ALLOWED_SCHEMES

    def test_no_auth_required(self, client: TestClient):
        """OAuth bounce is public — no JWT or API key needed."""
        state = _encode_state("csrf123", "jarvis://auth-complete")
        resp = client.get(f"/oauth/bounce?code=test&state={state}")
        assert resp.status_code == 302

    def test_csrf_token_forwarded_as_state(self, client: TestClient):
        """The original CSRF token (not the encoded blob) is forwarded as state."""
        state = _encode_state("my-csrf-token", "jarvis://auth-complete")
        resp = client.get(f"/oauth/bounce?code=abc&state={state}")
        location = resp.headers["location"]
        assert "state=my-csrf-token" in location
