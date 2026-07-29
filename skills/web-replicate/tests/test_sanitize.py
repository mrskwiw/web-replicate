"""Secret redaction covers the well-known carriers and can be disabled."""

from engine import sanitize


def test_redact_headers_hits_auth_and_cookie():
    h = {"Authorization": "Bearer abc", "Cookie": "sid=1", "Accept": "application/json"}
    out = sanitize.redact_headers(h)
    assert out["Authorization"] == sanitize.REDACTED
    assert out["Cookie"] == sanitize.REDACTED
    assert out["Accept"] == "application/json"  # non-secret untouched


def test_redact_headers_disabled_passthrough():
    h = {"Authorization": "Bearer abc"}
    assert sanitize.redact_headers(h, enabled=False) == h


def test_redact_body_json_keys():
    body = '{"user":"a","password":"hunter2","token":"xyz","count":3}'
    out = sanitize.redact_body(body)
    assert "hunter2" not in out
    assert '"count":3' in out or '"count": 3' in out
    assert out.count(sanitize.REDACTED) >= 2


def test_redact_body_bearer_and_jwt():
    body = "auth=Bearer sk_live_123 header eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abcdef"
    out = sanitize.redact_body(body)
    assert "sk_live_123" not in out
    assert "eyJhbGci" not in out


def test_redact_cookie_value():
    assert sanitize.redact_cookie_value("secret") == sanitize.REDACTED
    assert sanitize.redact_cookie_value("secret", enabled=False) == "secret"


def test_redact_storage_keeps_keys_scrubs_token_values():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payloadpayloadpayload.sig"
    store = {"theme": "dark", "access_token": jwt, "refresh_token": jwt, "sidebar": "open"}
    out = sanitize.redact_storage(store)
    # keys preserved (the storage contract), non-secret values kept
    assert set(out.keys()) == set(store.keys())
    assert out["theme"] == "dark"
    assert out["sidebar"] == "open"
    # secret-keyed values redacted
    assert out["access_token"] == sanitize.REDACTED
    assert out["refresh_token"] == sanitize.REDACTED


def test_redact_storage_catches_jwt_under_innocent_key():
    jwt = "eyJhbGciOiJIUzI1NiJ9.abcdefghijklmnopqrstuvwx.zzz"
    out = sanitize.redact_storage({"blob": jwt})
    assert out["blob"] == sanitize.REDACTED


def test_redact_storage_disabled_passthrough():
    store = {"access_token": "eyJraw"}
    assert sanitize.redact_storage(store, enabled=False) == store
