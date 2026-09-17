"""``wait_for_api`` (audit WR-T2) — zero prior coverage.

Mirrors web-qa's ``await_response`` convention: for a slow async action whose
network call lands after a fixed settle window, poll the captured responses
until the awaited call shows up (or the timeout elapses). Wired into `trace`'s
``await_response`` step key (`cli.py`), but the underlying polling method
itself had no direct test at all before this file.
"""

from __future__ import annotations

import http.server
import threading
import time
from contextlib import contextmanager

import pytest

from engine.browser import CaptureController

PAGE = b"""<!doctype html>
<title>wait_for_api fixture</title>
<body>
<button id="go" onclick="fetch('/api/late').then(r => r.text())">Go</button>
</body>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/late"):
            # The response lands well after the click that triggered it --
            # the exact shape `wait_for_api` exists to poll through.
            time.sleep(0.6)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_args):
        return


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@contextmanager
def _server():
    srv = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        host, port = srv.server_address
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


async def _launch(out_dir):
    controller = CaptureController(out_dir=str(out_dir), engine="chromium", headless=True)
    try:
        await controller.launch()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Chromium not installed for Playwright")
        raise
    return controller


@pytest.mark.asyncio
async def test_wait_for_api_detects_a_delayed_response(tmp_path):
    """The server holds `/api/late` for 600ms; wait_for_api's 250ms poll must
    still detect it well before its own 5s timeout -- proving this is real
    polling of the response log, not a snapshot taken once at call time."""
    controller = await _launch(tmp_path)
    try:
        with _server() as base:
            await controller.navigate(base)
            mark = controller.response_count()
            await controller.page.click("#go")
            started = time.monotonic()
            result = await controller.wait_for_api(
                "/api/late", method="GET", since=mark, timeout_ms=5000
            )
            elapsed = time.monotonic() - started
    finally:
        await controller.close()
    assert result is True
    # Must have actually waited for the server's artificial delay (not a
    # coincidental match against something already in the response log)...
    assert elapsed >= 0.3, f"returned too early ({elapsed:.2f}s) -- not real polling"
    # ...but comfortably inside the 5s timeout.
    assert elapsed < 3.0, f"took {elapsed:.2f}s -- did not detect the response promptly"


@pytest.mark.asyncio
async def test_wait_for_api_times_out_when_no_matching_response_arrives(tmp_path):
    """A path that will never be requested must return False once the (short)
    timeout elapses -- not hang, not raise."""
    controller = await _launch(tmp_path)
    try:
        with _server() as base:
            await controller.navigate(base)
            result = await controller.wait_for_api(
                "/api/never-requested", timeout_ms=500
            )
    finally:
        await controller.close()
    assert result is False


@pytest.mark.asyncio
async def test_wait_for_api_respects_the_since_mark(tmp_path):
    """A response that landed BEFORE `since` must not satisfy a later wait --
    otherwise a step's own await would false-positive on a prior step's call
    to the same endpoint."""
    controller = await _launch(tmp_path)
    try:
        with _server() as base:
            await controller.navigate(base)
            # First call, completes before we take our mark.
            await controller.page.click("#go")
            await controller.page.wait_for_timeout(900)
            mark = controller.response_count()
            # No second call is made -- since `mark`, nothing matching exists.
            result = await controller.wait_for_api(
                "/api/late", since=mark, timeout_ms=500
            )
    finally:
        await controller.close()
    assert result is False


@pytest.mark.asyncio
async def test_wait_for_api_filters_by_method(tmp_path):
    """A method filter must not match a same-path response of a different
    method."""
    controller = await _launch(tmp_path)
    try:
        with _server() as base:
            await controller.navigate(base)
            mark = controller.response_count()
            await controller.page.click("#go")  # fires a GET
            result = await controller.wait_for_api(
                "/api/late", method="POST", since=mark, timeout_ms=1500
            )
    finally:
        await controller.close()
    assert result is False
