"""Tests for household JWT validation."""

import jwt
import pytest
from fastapi import HTTPException

from app.auth import validate_household_jwt


class TestValidateHouseholdJWT:
    def test_valid_jwt(self):
        token = jwt.encode(
            {"household_id": "hh-123", "exp": 9999999999},
            "test-secret-key",
            algorithm="HS256",
        )
        result = validate_household_jwt(
            authorization=f"Bearer {token}",
            x_household_id="hh-123",
        )
        assert result == "hh-123"

    def test_missing_bearer_prefix(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_household_jwt(
                authorization="token-without-bearer",
                x_household_id="hh-123",
            )
        assert exc_info.value.status_code == 401

    def test_empty_token(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_household_jwt(
                authorization="Bearer ",
                x_household_id="hh-123",
            )
        assert exc_info.value.status_code == 401

    def test_expired_token(self):
        token = jwt.encode(
            {"household_id": "hh-123", "exp": 1},
            "test-secret-key",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc_info:
            validate_household_jwt(
                authorization=f"Bearer {token}",
                x_household_id="hh-123",
            )
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_invalid_token(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_household_jwt(
                authorization="Bearer not-a-real-jwt",
                x_household_id="hh-123",
            )
        assert exc_info.value.status_code == 401

    def test_missing_household_claim(self):
        token = jwt.encode(
            {"sub": "user-1", "exp": 9999999999},
            "test-secret-key",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc_info:
            validate_household_jwt(
                authorization=f"Bearer {token}",
                x_household_id="hh-123",
            )
        assert exc_info.value.status_code == 401

    def test_household_id_mismatch(self):
        token = jwt.encode(
            {"household_id": "hh-999", "exp": 9999999999},
            "test-secret-key",
            algorithm="HS256",
        )
        with pytest.raises(HTTPException) as exc_info:
            validate_household_jwt(
                authorization=f"Bearer {token}",
                x_household_id="hh-123",
            )
        assert exc_info.value.status_code == 401
        assert "match" in exc_info.value.detail.lower()
