"""Capture must describe ONE document, or say that it could not.

`capture_page` degrades each collector to a default on error, which is right for
a huge or hanging DOM. But it meant a navigation mid-capture silently emptied
every field at once: a PageCapture that parses fine and reports no forms, no
links and no assets is indistinguishable from a genuinely bare page — and a
replication blueprint built from it is confidently wrong.

These tests pin the two properties that matter: a settled page captures its real
content, and a page that will not hold still says so in `capture_error` rather
than quietly returning emptiness.
"""

from __future__ import annotations

import http.server
import json
import threading
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from engine.cli import cli

REAL = (
    b"<!doctype html><title>Settled</title><h1>Settled</h1>"
    b"<form><input name='q'><button type=submit>Go</button></form>"
    b"<a href='/other'>Other</a>"
)
# Re-navigates on a timer, so a capture is repeatedly torn out from under itself.
# It carries the SAME form and link as the settled page on purpose: without real
# content, "came back empty" would be true of a successful capture too, and the
# test would prove nothing. With them, emptiness can only mean a failed read.
CHURN = (
    b"<!doctype html><title>Churn</title><h1>Churn</h1>"
    b"<form><input name='q'><button type=submit>Go</button></form>"
    b"<a href='/other'>Other</a>"
    b"<script>setTimeout(function(){location.href='/churn?n='+Math.random();},120);</script>"
)


class _H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        body = CHURN if self.path.startswith("/churn") else REAL
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class _S(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """Silence keep-alive disconnects, which would corrupt the CLI's JSON."""


@contextmanager
def _server():
    srv = _S(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        host, port = srv.server_address
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


def _capture(tmp_path, url, name="cap"):
    out = tmp_path / name
    res = CliRunner().invoke(cli, ["capture", "--url", url, "--out-dir", str(out)])
    if res.exit_code != 0:
        msg = str(res.exception or res.output)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Chromium not installed for Playwright")
        raise AssertionError(f"capture failed (exit {res.exit_code}): {msg}")
    return json.loads((out / "page.json").read_text(encoding="utf-8"))


def test_settled_page_captures_its_real_content(tmp_path):
    """Baseline: the stability check must not cost us an ordinary capture."""
    with _server() as base:
        cap = _capture(tmp_path, base)
    assert cap["title"] == "Settled"
    assert cap["forms"], "a page with a form captured none"
    assert cap["links"], "a page with a link captured none"
    assert cap["capture_error"] is None or "document changed" not in str(
        cap["capture_error"]
    )


def test_page_that_never_holds_still_is_disclosed_not_silently_empty(tmp_path):
    """The whole point: an unreadable capture must announce itself.

    A blueprint consumer has to be able to tell 'this page really has nothing'
    from 'we could not read this page coherently'. Emptiness alone cannot carry
    that distinction, so the capture has to say it.

    COVERAGE CAVEAT, stated because a green test here is easy to over-read: this
    asserts the INVARIANT (empty implies disclosed), not the disclosure branch
    itself. Settle-plus-retry is effective enough that this fixture normally
    captures fine, so the branch does not fire; verified by stubbing the
    disclosure out, after which the test still passes. Churning harder does not
    help — a synchronous redirect loop makes navigate() time out before capture
    begins, which is a different failure. The disclosure path is therefore a
    last-resort net that is exercised by inspection, not by this test.
    """
    with _server() as base:
        cap = _capture(tmp_path, f"{base}/churn", name="churn")

    err = json.dumps(cap.get("capture_error"))
    looks_empty = not cap["forms"] and not cap["links"] and not cap["interactive"]
    if looks_empty:
        assert "document changed" in err, (
            "capture came back empty with no explanation — indistinguishable "
            f"from a genuinely bare page. capture_error={err}"
        )
