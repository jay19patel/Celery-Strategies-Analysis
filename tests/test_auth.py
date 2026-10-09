"""Tests for Argon2id 6-digit PIN authentication, 7-day session management, and access control."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient

from tests.conftest import IdleStream
from tests.test_pipeline import AlwaysBuy
from tradebuddy import auth
from tradebuddy.app import local_app
from tradebuddy.system import System


def make_authed_system(cfg, exchange, pin: str = "242425"):
    authed_cfg = dataclasses.replace(cfg, auth_pin=pin, auth_secret="test_secret_for_sessions_1234567890")
    system = System(authed_cfg, strategies=[AlwaysBuy()], stream_factory=IdleStream, client_factory=exchange.client)
    client = TestClient(local_app(system))
    return system, client


def test_argon2_hashing_and_verification():
    """Verify that Argon2id produces valid hashes and verifies in constant time."""
    pin = "242425"
    hashed = auth.hash_pin(pin)
    assert hashed.startswith("$argon2id$")
    assert auth.verify_pin(pin, hashed) is True
    assert auth.verify_pin("999999", hashed) is False
    assert auth.verify_pin("wrong", hashed) is False

    with pytest.raises(auth.InvalidPinError):
        auth.hash_pin("123")  # too short

    with pytest.raises(auth.InvalidPinError):
        auth.hash_pin("1234567")  # too long

    with pytest.raises(auth.InvalidPinError):
        auth.hash_pin("abcdef")  # not digits


def test_session_token_validity_and_expiration():
    """Verify HMAC signed 7-day session tokens."""
    secret = "my_super_secret_key"
    token = auth.create_session_token(secret, max_age_seconds=auth.SESSION_MAX_AGE_SECONDS)
    assert auth.validate_session_token(token, secret) is True
    assert auth.validate_session_token(token, "wrong_secret") is False

    # Tampered token
    tampered = token[:-4] + "abcd"
    assert auth.validate_session_token(tampered, secret) is False

    # Expired token (-10 seconds)
    expired_token = auth.create_session_token(secret, max_age_seconds=-10)
    assert auth.validate_session_token(expired_token, secret) is False


def test_unauthenticated_request_redirects_to_login(cfg, exchange):
    """Unauthorized page requests must be redirected to /login with next query param."""
    _, client = make_authed_system(cfg, exchange, pin="242425")
    res = client.get("/", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login?next=/"


def test_unauthenticated_api_returns_401(cfg, exchange):
    """Unauthorized API requests must return 401 JSON error."""
    _, client = make_authed_system(cfg, exchange, pin="242425")
    res = client.get("/api/header")
    assert res.status_code == 401
    assert "Authentication required" in res.json()["detail"]


def test_login_page_renders_correctly(cfg, exchange):
    """The /login page renders the 6-digit PIN screen and assets."""
    _, client = make_authed_system(cfg, exchange, pin="242425")
    res = client.get("/login")
    assert res.status_code == 200
    assert "Security Login · TradeBuddy" in res.text
    assert "digit0" in res.text
    assert "digit5" in res.text
    assert "7-day persistent session" in res.text


def test_pin_login_invalid_pin_rejected(cfg, exchange):
    """Submitting incorrect PIN returns 401."""
    _, client = make_authed_system(cfg, exchange, pin="242425")
    res = client.post("/api/auth/login", json={"pin": "000000"})
    assert res.status_code == 401
    assert "Incorrect PIN" in res.json()["detail"]


def test_pin_login_valid_pin_sets_7day_cookie(cfg, exchange):
    """Submitting correct PIN returns 200 and sets 7-day tb_session cookie."""
    _, client = make_authed_system(cfg, exchange, pin="242425")
    res = client.post("/api/auth/login", json={"pin": "242425", "next": "/journal"})
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["redirect"] == "/journal"

    # Verify cookie attributes
    cookie = res.cookies.get(auth.SESSION_COOKIE_NAME)
    assert cookie is not None
    assert auth.validate_session_token(cookie, "test_secret_for_sessions_1234567890") is True


def test_authenticated_access_granted_with_cookie(cfg, exchange):
    """Once cookie is set, full access is granted to pages and APIs."""
    _, client = make_authed_system(cfg, exchange, pin="242425")
    login_res = client.post("/api/auth/login", json={"pin": "242425"})
    assert login_res.status_code == 200

    # Test pages render without redirect
    home_res = client.get("/")
    assert home_res.status_code == 200
    assert "Overview · TradeBuddy" in home_res.text

    # Test API responds
    api_res = client.get("/api/header")
    assert api_res.status_code == 200
    assert "brokers" in api_res.json()


def test_brute_force_rate_limiting():
    """Check that 5 consecutive failed attempts lock out the client."""
    guard = auth.BruteForceGuard(max_attempts=3, lockout_seconds=10)
    client_ip = "192.168.1.100"

    rem, lock = guard.record_failure(client_ip)
    assert rem == 2 and lock == 0

    rem, lock = guard.record_failure(client_ip)
    assert rem == 1 and lock == 0

    rem, lock = guard.record_failure(client_ip)
    assert rem == 0 and lock == 10

    locked, rem_time = guard.is_locked(client_ip)
    assert locked is True
    assert rem_time > 0


def test_change_pin_flow(cfg, exchange):
    """Change PIN from 242425 to 112233 and verify new PIN authentication."""
    _, client = make_authed_system(cfg, exchange, pin="242425")

    # Login with current PIN
    login_res = client.post("/api/auth/login", json={"pin": "242425"})
    assert login_res.status_code == 200

    # Change PIN to 112233
    change_res = client.post("/api/auth/change-pin", json={"old_pin": "242425", "new_pin": "112233"})
    assert change_res.status_code == 200
    assert change_res.json()["ok"] is True

    # Logout
    client.cookies.clear()

    # Old PIN must be rejected
    fail_res = client.post("/api/auth/login", json={"pin": "242425"})
    assert fail_res.status_code == 401

    # New PIN must succeed
    ok_res = client.post("/api/auth/login", json={"pin": "112233"})
    assert ok_res.status_code == 200


def test_logout_clears_cookie(cfg, exchange):
    """GET /logout clears the session cookie and redirects to /login."""
    _, client = make_authed_system(cfg, exchange, pin="242425")
    client.post("/api/auth/login", json={"pin": "242425"})
    assert client.cookies.get(auth.SESSION_COOKIE_NAME) is not None

    logout_res = client.get("/logout", follow_redirects=False)
    assert logout_res.status_code == 303
    assert logout_res.headers["location"] == "/login"
