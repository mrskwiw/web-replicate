"""Auth-verification pure logic: base-url parsing, param substitution, verdict
classification, reconciliation against the inferred column, and endpoint selection."""

from engine.authcheck import (
    classify,
    parse_subs,
    reconcile,
    resolve_base_url,
    select_endpoints,
    substitute_params,
)


def test_resolve_base_url_override_wins():
    assert resolve_base_url("https://x/api (prose)", "https://y.com/") == "https://y.com"


def test_resolve_base_url_from_prose():
    # base_url strings are often descriptive; keep only scheme://host
    assert resolve_base_url("https://app.example.com/api (same-origin; Next.js)", None) == \
        "https://app.example.com"
    assert resolve_base_url("http://127.0.0.1:8000/api", None) == "http://127.0.0.1:8000"
    assert resolve_base_url(None, None) is None
    assert resolve_base_url("no url here", None) is None


def test_parse_subs():
    assert parse_subs(["id=abc", "slug = x "]) == {"id": "abc", "slug": "x"}
    assert parse_subs([]) == {}
    assert parse_subs(["novalue"]) == {}


def test_substitute_params():
    assert substitute_params("/api/quiz/{id}/like", {"id": "u1"}) == "/api/quiz/u1/like"
    assert substitute_params("/api/quiz", {}) == "/api/quiz"
    # unresolved param → None so the caller skips-and-reports
    assert substitute_params("/api/quiz/{id}/like", {}) is None


def test_classify():
    assert classify(200, 401) == "enforced"
    assert classify(200, 403) == "enforced"
    assert classify(200, 302) == "enforced"
    assert classify(200, 200) == "public"
    assert classify(200, 500) == "error-unauth"
    assert classify(200, 404) == "inconclusive"
    assert classify(200, None) == "inconclusive"


def test_reconcile_agrees():
    assert reconcile("yes (cookie)", "enforced") == (True, "agrees (enforced)")
    assert reconcile("no (public)", "public") == (True, "agrees (public)")


def test_reconcile_corrections():
    ok, note = reconcile("yes (cookie)", "public")
    assert ok is False and "CORRECTED" in note and "PUBLIC" in note
    ok, note = reconcile("no (public)", "enforced")
    assert ok is False and "CORRECTED" in note and "ENFORCED" in note


def test_reconcile_bug_and_inconclusive():
    ok, note = reconcile("yes", "error-unauth")
    assert ok is False and "BUG" in note
    ok, note = reconcile("yes", "inconclusive")
    assert ok is True and "inconclusive" in note


def test_select_endpoints_withholds_mutating_by_default():
    eps = [
        {"method": "GET", "path": "/api/quiz"},
        {"method": "POST", "path": "/api/quiz"},
        {"method": "GET", "path": "/api/quiz/{id}/like"},
    ]
    probe, skipped = select_endpoints(eps, {}, include_mutating=False)
    probe_paths = {(e["method"], e["resolved_path"]) for e in probe}
    assert ("GET", "/api/quiz") in probe_paths
    # POST withheld, and the {id} GET has no sub → both skipped with reasons
    reasons = {e["path"]: e["skip_reason"] for e in skipped}
    assert "mutating" in reasons["/api/quiz"]
    assert "unresolved path param" in reasons["/api/quiz/{id}/like"]


def test_select_endpoints_resolves_and_includes_mutating():
    eps = [
        {"method": "POST", "path": "/api/quiz"},
        {"method": "GET", "path": "/api/quiz/{id}/like"},
    ]
    probe, skipped = select_endpoints(eps, {"id": "u1"}, include_mutating=True)
    got = {(e["method"], e["resolved_path"]) for e in probe}
    assert got == {("POST", "/api/quiz"), ("GET", "/api/quiz/u1/like")}
    assert skipped == []
