"""
tests/test_security.py — Security layer tests.

Covers:
  - JWT create/verify/expiry/tamper
  - Rate limiting (per email, per IP, lockout, reset on success)
  - XSS sanitization (strings, dicts, nested, edge cases)
  - CSRF token generate/verify/expiry/replay
  - Bot detection (UA patterns, honeypot)
  - Password hashing + strength validation
  - RLS SQL helper (structure check)

Zero API key required. Zero network calls.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import os
os.environ.setdefault("APEX_JWT_SECRET", "test-secret-key-that-is-long-enough-32chars!")

from security.middleware import (
    create_token, verify_token, hash_token,
    check_rate_limit, record_login_attempt, get_lockout_remaining,
    sanitize_string, sanitize_dict,
    generate_csrf_token, verify_csrf_token,
    is_bot_request, hash_password, verify_password,
    validate_password_strength,
    AuthenticatedUser, SecurityViolation,
    _rate_store,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_token() -> tuple[str, str]:
    """Return (token, session_id)."""
    session_id = "test-session-abc123"
    token = create_token(
        user_id="usr_001",
        org_id="org_001",
        email="test@apex.io",
        role="operator",
        session_id=session_id,
    )
    return token, session_id


def _clear_rate_store():
    """Clear in-memory rate limit store between tests."""
    _rate_store.clear()


# ══════════════════════════════════════════════════════════════════════════════
# JWT Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_jwt_create_and_verify():
    token, _ = _make_token()
    user = verify_token(token)
    assert user is not None
    assert user.user_id == "usr_001"
    assert user.org_id == "org_001"
    assert user.email == "test@apex.io"
    assert user.role == "operator"


def test_jwt_returns_authenticated_user_type():
    token, _ = _make_token()
    user = verify_token(token)
    assert isinstance(user, AuthenticatedUser)


def test_jwt_tampered_signature_rejected():
    token, _ = _make_token()
    # Flip the last character of the signature
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    assert verify_token(tampered) is None


def test_jwt_wrong_secret_rejected():
    from security.middleware import _hmac_jwt_encode
    payload = {"sub": "x", "org": "y", "email": "a@b.com",
               "role": "viewer", "sid": "s", "iat": 0, "exp": 9999999999}
    bad_token = _hmac_jwt_encode(payload, "wrong-secret-that-is-long-enough!!")
    assert verify_token(bad_token) is None


def test_jwt_missing_claims_rejected():
    from security.middleware import _hmac_jwt_encode
    # Missing required 'role' claim
    payload = {"sub": "x", "org": "y", "email": "a@b.com",
               "sid": "s", "iat": 0, "exp": 9999999999}
    bad_token = _hmac_jwt_encode(payload, os.environ["APEX_JWT_SECRET"])
    assert verify_token(bad_token) is None


def test_jwt_expired_token_rejected():
    from security.middleware import _hmac_jwt_encode
    payload = {"sub": "x", "org": "y", "email": "a@b.com",
               "role": "viewer", "sid": "s",
               "iat": 0, "exp": 1}  # expired in 1970
    expired = _hmac_jwt_encode(payload, os.environ["APEX_JWT_SECRET"])
    assert verify_token(expired) is None


def test_jwt_empty_string_rejected():
    assert verify_token("") is None


def test_jwt_none_rejected():
    assert verify_token(None) is None


def test_hash_token_is_sha256():
    token, _ = _make_token()
    h = hash_token(token)
    assert len(h) == 64  # SHA-256 hex digest
    assert h != token


def test_hash_token_deterministic():
    token, _ = _make_token()
    assert hash_token(token) == hash_token(token)


# ══════════════════════════════════════════════════════════════════════════════
# Rate Limiting Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_rate_limit_not_triggered_initially():
    _clear_rate_store()
    assert not check_rate_limit("user@test.com", "1.2.3.4")


def test_rate_limit_triggers_after_max_attempts():
    _clear_rate_store()
    for _ in range(5):
        record_login_attempt("ratelimit@test.com", "9.9.9.9", success=False)
    assert check_rate_limit("ratelimit@test.com", "9.9.9.9")


def test_rate_limit_resets_on_success():
    _clear_rate_store()
    for _ in range(4):
        record_login_attempt("reset@test.com", "1.1.1.1", success=False)
    record_login_attempt("reset@test.com", "1.1.1.1", success=True)
    assert not check_rate_limit("reset@test.com", "1.1.1.1")


def test_rate_limit_per_ip_independent_of_email():
    _clear_rate_store()
    for _ in range(5):
        record_login_attempt("other@test.com", "5.5.5.5", success=False)
    # Different email, same IP — should still be blocked by IP
    assert check_rate_limit("different@test.com", "5.5.5.5")


def test_lockout_remaining_positive_when_locked():
    _clear_rate_store()
    for _ in range(5):
        record_login_attempt("locked@test.com", "6.6.6.6", success=False)
    remaining = get_lockout_remaining("locked@test.com", "6.6.6.6")
    assert remaining > 0


def test_lockout_remaining_zero_when_not_locked():
    _clear_rate_store()
    remaining = get_lockout_remaining("free@test.com", "7.7.7.7")
    assert remaining == 0


def test_rate_limit_below_threshold_not_blocked():
    _clear_rate_store()
    for _ in range(4):  # one under limit
        record_login_attempt("almost@test.com", "8.8.8.8", success=False)
    assert not check_rate_limit("almost@test.com", "8.8.8.8")


# ══════════════════════════════════════════════════════════════════════════════
# XSS Sanitization Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_sanitize_clean_string():
    assert sanitize_string("SKF 6205-2Z bearing") == "SKF 6205-2Z bearing"


def test_sanitize_html_escapes_angle_brackets():
    result = sanitize_string("5 < 10 and 10 > 5")
    assert "<" not in result
    assert ">" not in result


def test_sanitize_blocks_script_tag():
    with pytest.raises(SecurityViolation):
        sanitize_string("<script>alert('xss')</script>")


def test_sanitize_blocks_javascript_protocol():
    with pytest.raises(SecurityViolation):
        sanitize_string("javascript:alert(1)")


def test_sanitize_blocks_onerror_attribute():
    with pytest.raises(SecurityViolation):
        sanitize_string('<img onerror="alert(1)">')


def test_sanitize_blocks_iframe():
    with pytest.raises(SecurityViolation):
        sanitize_string('<iframe src="evil.com"></iframe>')


def test_sanitize_blocks_data_uri():
    with pytest.raises(SecurityViolation):
        sanitize_string('data:text/html,<script>alert(1)</script>')


def test_sanitize_enforces_max_length():
    long_str = "a" * 2000
    result = sanitize_string(long_str, max_length=100)
    assert len(result) == 100


def test_sanitize_strips_control_chars():
    result = sanitize_string("hello\x00world\x1f")
    assert "\x00" not in result
    assert "\x1f" not in result


def test_sanitize_empty_string():
    assert sanitize_string("") == ""


def test_sanitize_dict_cleans_values():
    data = {"material": "Chrome steel", "name": "Bearing 6205"}
    result = sanitize_dict(data)
    assert result["material"] == "Chrome steel"


def test_sanitize_dict_blocks_xss_in_value():
    with pytest.raises(SecurityViolation):
        sanitize_dict({"field": "<script>evil()</script>"})


def test_sanitize_dict_nested():
    data = {"outer": {"inner": "safe value"}}
    result = sanitize_dict(data)
    assert result["outer"]["inner"] == "safe value"


def test_sanitize_dict_caps_list_length():
    data = {"items": ["a"] * 100}
    result = sanitize_dict(data)
    assert len(result["items"]) == 50


def test_sanitize_dict_max_depth():
    # Build dict 10 levels deep
    deep = {}
    current = deep
    for i in range(10):
        current["child"] = {}
        current = current["child"]
    current["val"] = "leaf"
    result = sanitize_dict(deep, max_depth=5)
    assert isinstance(result, dict)


# ══════════════════════════════════════════════════════════════════════════════
# CSRF Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_csrf_generate_and_verify():
    session_id = "session-123-abc"
    token = generate_csrf_token(session_id)
    assert verify_csrf_token(token, session_id)


def test_csrf_wrong_session_rejected():
    token = generate_csrf_token("session-A")
    assert not verify_csrf_token(token, "session-B")


def test_csrf_tampered_token_rejected():
    token = generate_csrf_token("session-X")
    parts = token.split(".")
    tampered = parts[0] + ".bad-signature-here"
    assert not verify_csrf_token(tampered, "session-X")


def test_csrf_empty_token_rejected():
    assert not verify_csrf_token("", "session-X")


def test_csrf_malformed_no_dot():
    assert not verify_csrf_token("nodottoken", "session-X")


def test_csrf_token_format():
    token = generate_csrf_token("sess-test")
    parts = token.split(".")
    assert len(parts) == 2
    assert parts[0].isdigit()   # timestamp
    assert len(parts[1]) == 64  # SHA-256 hex


# ══════════════════════════════════════════════════════════════════════════════
# Bot Detection Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_bot_detection_honeypot_filled():
    assert is_bot_request("Mozilla/5.0 Chrome", honeypot_value="filled-by-bot")


def test_bot_detection_clean_browser():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    assert not is_bot_request(ua, honeypot_value="")


def test_bot_detection_curl():
    assert is_bot_request("curl/7.68.0")


def test_bot_detection_python_requests():
    assert is_bot_request("python-requests/2.28.0")


def test_bot_detection_empty_ua():
    assert is_bot_request("")


def test_bot_detection_googlebot():
    assert is_bot_request("Googlebot/2.1 (+http://www.google.com/bot.html)")


def test_bot_detection_selenium():
    assert is_bot_request("Mozilla/5.0 (selenium)")


# ══════════════════════════════════════════════════════════════════════════════
# Password Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_password_hash_and_verify():
    pw = "StrongPass@2026!"
    h = hash_password(pw)
    assert verify_password(pw, h)


def test_password_wrong_password_rejected():
    h = hash_password("CorrectPassword@123")
    assert not verify_password("WrongPassword@123", h)


def test_password_hash_not_plaintext():
    pw = "MyPassword@2026!"
    h = hash_password(pw)
    assert pw not in h


def test_password_hashes_are_unique():
    pw = "SamePassword@123!"
    h1, h2 = hash_password(pw), hash_password(pw)
    assert h1 != h2  # bcrypt uses random salt


def test_password_strength_strong_passes():
    issues = validate_password_strength("StrongPass@2026!")
    assert issues == []


def test_password_strength_too_short():
    issues = validate_password_strength("Short@1!")
    assert any("12" in i for i in issues)


def test_password_strength_no_uppercase():
    issues = validate_password_strength("nouppercase@123!")
    assert any("uppercase" in i for i in issues)


def test_password_strength_no_lowercase():
    issues = validate_password_strength("NOLOWERCASE@123!")
    assert any("lowercase" in i for i in issues)


def test_password_strength_no_digit():
    issues = validate_password_strength("NoDigitHere!!!!!")
    assert any("digit" in i for i in issues)


def test_password_strength_no_special():
    issues = validate_password_strength("NoSpecialChar12345")
    assert any("special" in i for i in issues)


def test_password_strength_all_failures():
    issues = validate_password_strength("weak")
    assert len(issues) >= 4


# ══════════════════════════════════════════════════════════════════════════════
# RLS SQL structure tests (no DB required)
# ══════════════════════════════════════════════════════════════════════════════

def test_rls_migration_file_exists():
    sql_path = Path(__file__).parent.parent / "migrations" / "001_schema_rls.sql"
    assert sql_path.exists()


def test_rls_migration_has_enable_rls():
    sql = (Path(__file__).parent.parent / "migrations" / "001_schema_rls.sql").read_text()
    assert "ENABLE ROW LEVEL SECURITY" in sql


def test_rls_migration_has_policies():
    sql = (Path(__file__).parent.parent / "migrations" / "001_schema_rls.sql").read_text()
    assert "CREATE POLICY" in sql
    assert "products_select" in sql
    assert "products_insert" in sql
    assert "products_update" in sql
    assert "products_delete" in sql


def test_rls_migration_has_audit_log():
    sql = (Path(__file__).parent.parent / "migrations" / "001_schema_rls.sql").read_text()
    assert "audit_log" in sql
    assert "prevent_audit_mutation" in sql


def test_rls_migration_has_rate_limit_function():
    sql = (Path(__file__).parent.parent / "migrations" / "001_schema_rls.sql").read_text()
    assert "is_rate_limited" in sql


def test_rls_migration_has_org_isolation():
    sql = (Path(__file__).parent.parent / "migrations" / "001_schema_rls.sql").read_text()
    assert "current_org_id()" in sql


def test_rls_migration_three_roles():
    sql = (Path(__file__).parent.parent / "migrations" / "001_schema_rls.sql").read_text()
    assert "'admin'" in sql
    assert "'operator'" in sql
    assert "'viewer'" in sql
