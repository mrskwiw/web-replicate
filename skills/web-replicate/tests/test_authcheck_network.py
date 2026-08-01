"""verify-auth NETWORK path — the one active check, against a live local server.

The pure helpers (parse/substitute/classify/reconcile/select) are covered in
``test_authcheck.py``. This exercises the async ``probe_endpoints`` that actually
issues the authed + anonymous requests — previously untested — including the
``include_mutating`` gating and the enforced/public/correction verdicts. It uses
stdlib ``http.server`` + Playwright's API request context (no browser page). If the
Playwright driver isn't available it skips rather than fails.
"""

import asyncio
import http.server
import socketserver
import threading
from contextlib import contextmanager

import pytest

from engine import authcheck

# The session the "authed" context replays: a single cookie the fixture server
# treats as a valid login.
_SESSION = {
    "cookies": [
        {
            "name": "session",
            "value": "valid",
            "domain": "127.0.0.1",
            "path": "/",
            "expires": -1,
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
        }
    ],
    "origins": [],
}


class _Handler(http.server.BaseHTTPRequestHandler):
    """`/api/private` needs the session cookie; `/api/open` is public; the POST is
    open too (only reachable with --include-mutating)."""

    def _authed(self) -> bool:
        return "session=valid" in self.headers.get("Cookie", "")

    def _reply(self):
        if self.path.startswith("/api/private") and not self._authed():
            self.send_response(401)
        else:
            self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def do_GET(self):  # noqa: N802 (http.server API)
        self._reply()

    def do_POST(self):  # noqa: N802
        self._reply()

    def log_message(self, *_args):
        return


@contextmanager
def _server():
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            host, port = httpd.server_address
            yield f"http://{host}:{port}"
        finally:
            httpd.shutdown()


def _probe(base, endpoints, **kw):
    try:
        return asyncio.run(
            authcheck.probe_endpoints(
                base, endpoints, _SESSION, None, subs={}, **kw
            )
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Playwright driver not available")
        raise


def test_probe_classifies_enforced_and_public_and_corrections():
    """Anonymous rejection → enforced; anonymous 2xx → public; and an endpoint
    inferred auth-required but actually public is counted as a correction."""
    # inferred "yes" (auth-required) for both — realistic passive inference that
    # "only saw it while logged in". Verification will agree on /api/private and
    # CORRECT /api/open (actually public).
    endpoints = [
        {"method": "GET", "path": "/api/private", "auth_required": "yes (inferred — only seen authed)"},
        {"method": "GET", "path": "/api/open", "auth_required": "yes (inferred — only seen authed)"},
    ]
    with _server() as base:
        report = _probe(base, endpoints)

    by_path = {r["path"]: r for r in report["results"]}
    assert by_path["/api/private"]["verdict"] == "enforced"
    assert by_path["/api/private"]["without_auth_status"] == 401
    assert by_path["/api/private"]["with_auth_status"] == 200
    # public endpoint that was inferred auth-required → a corrected inference
    assert by_path["/api/open"]["verdict"] == "public"
    assert by_path["/api/open"]["matches_inference"] is False
    assert report["summary"]["enforced"] == 1
    assert report["summary"]["public"] == 1
    assert report["summary"]["corrections"] == 1


def test_probe_withholds_mutating_by_default():
    """A write verb is skipped (not probed) unless include_mutating is set."""
    endpoints = [
        {"method": "POST", "path": "/api/open", "auth_required": "inferred-required"},
    ]
    with _server() as base:
        safe = _probe(base, endpoints)
        aggressive = _probe(base, endpoints, include_mutating=True)

    assert safe["summary"]["probed"] == 0
    assert safe["summary"]["skipped"] == 1
    assert "mutating" in safe["skipped"][0]["skip_reason"]
    # opted-in: the POST is now probed and (open) classified public
    assert aggressive["summary"]["probed"] == 1
    assert aggressive["results"][0]["verdict"] == "public"
