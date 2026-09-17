"""Ported from web-qa (2026-09-16): `fill` self-verification, `pause`, and
`wait_for_human`.

These existed in web-qa's `browser.py` but were never carried into
web-replicate's copy (both are supposed to stay byte-identical per each
sibling's own copied-lineage discipline). Found while porting the same fix
into web-drive (BUGS.md 2026-08-26 / 2026-09-16): `perform()` called bare
`page.fill()` instead of a self-verifying wrapper, `wait_for_human` was
missing entirely, and `ActionType.PAUSE` was missing from `models.py`'s enum
— so a `pause` step would raise `ValueError` in `flow.py`'s
`ActionType(step["type"])` the moment `flow` is wired to the CLI.

Drives `CaptureController` directly (web-replicate's `flow` is not exposed as
a CLI command with a `--steps` interface the way this would otherwise mirror).
"""

from __future__ import annotations

import http.server
import threading
import time
from contextlib import contextmanager

import pytest

from engine.browser import CaptureController
from engine.models import Action, ActionType

# A controlled input that behaves like React Native Web's TextInput: the
# component owns the value, so any `input` event that did not come from a
# real keystroke gets stomped back to component state on the next render.
PAGE = b"""<!doctype html>
<title>controlled-input fixture</title>
<input id="plain" placeholder="plain">
<input id="controlled" placeholder="controlled">
<script>
  var state = '';
  var keys = 0;
  var el = document.getElementById('controlled');
  el.addEventListener('keydown', function (e) {
    keys++;
    if (e.key === 'Backspace') { state = state.slice(0, -1); }
    else if (e.key === 'Delete') { state = ''; }
    else if (e.key.length === 1) { state += e.key; }
  });
  el.addEventListener('input', function () { el.value = state; });
  window.__keys = function () { return keys; };
</script>
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 — stdlib callback name
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
async def test_fill_lands_on_a_controlled_input_and_stays_fast_on_a_plain_one(tmp_path):
    controller = await _launch(tmp_path)
    try:
        with _server() as base:
            await controller.navigate(base)
            await controller.perform(
                Action(type=ActionType.FILL, selector="#plain", value="PlainValue")
            )
            await controller.perform(
                Action(type=ActionType.FILL, selector="#controlled", value="QATester")
            )
            plain = await controller.page.input_value("#plain")
            controlled = await controller.page.input_value("#controlled")
            keys = await controller.page.evaluate("() => window.__keys()")
    finally:
        await controller.close()

    assert plain == "PlainValue"
    # The controlled field actually holds the value — the defect this fixes.
    assert controlled == "QATester"
    # Non-zero proves the keystroke fallback actually fired for #controlled.
    assert keys > 0, f"expected the keystroke fallback to fire, saw KEYS:{keys}"


@pytest.mark.asyncio
async def test_plain_fill_does_not_type_character_by_character(tmp_path):
    controller = await _launch(tmp_path)
    try:
        with _server() as base:
            await controller.navigate(base)
            await controller.perform(
                Action(type=ActionType.FILL, selector="#plain", value="PlainValue")
            )
            plain = await controller.page.input_value("#plain")
            keys = await controller.page.evaluate("() => window.__keys()")
    finally:
        await controller.close()

    assert plain == "PlainValue"
    assert keys == 0  # the fast path never touched the controlled field's counter


@pytest.mark.asyncio
async def test_pause_refuses_to_run_headless(tmp_path):
    controller = await _launch(tmp_path)
    try:
        with _server() as base:
            await controller.navigate(base)
            with pytest.raises(RuntimeError, match="needs a browser the human can see"):
                await controller.perform(
                    Action(type=ActionType.PAUSE, text="solve it", value="5")
                )
    finally:
        await controller.close()


# -- wait_for_human's actual polling loop (audit WR-T1) ----------------------
#
# The refusal test above only proves the headless guard fires; it never drives
# the polling mechanism a human-in-the-loop step actually depends on once past
# that guard. These launch headed (a real display is available in this
# environment -- smoke-tested separately) and drive the resume condition from
# in-page JS on a delay, so the assertions prove wait_for_human genuinely waits
# for and detects a late-arriving condition rather than returning immediately
# or by coincidence.

DELAYED_PAGE = b"""<!doctype html>
<title>resume-condition fixture</title>
<body>
<script>
  setTimeout(function () {
    var el = document.createElement('div');
    el.id = 'resumed';
    document.body.appendChild(el);
  }, 700);
</script>
</body>
"""


class _DelayedHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/redirect-target"):
            body = b"<!doctype html><title>Resumed</title><h1>Resumed</h1>"
        else:
            body = DELAYED_PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@contextmanager
def _delayed_server():
    srv = _Server(("127.0.0.1", 0), _DelayedHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        host, port = srv.server_address
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()
        srv.server_close()


async def _launch_headed(out_dir):
    controller = CaptureController(out_dir=str(out_dir), engine="chromium", headless=False)
    try:
        await controller.launch()
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Chromium not installed for Playwright")
        raise
    return controller


@pytest.mark.asyncio
async def test_wait_for_human_polls_until_selector_appears(tmp_path):
    """The resume selector is injected 700ms after navigation; a poll interval
    well below that (100ms) must observe it and return True well before the
    5s timeout -- proving this is real polling, not a lucky first check.

    The elapsed-time bounds are the point: an implementation that returned
    True immediately (element absent on the first check) or that only checked
    once at the very end would both still satisfy a bare `is True` assertion,
    so this pins the timing too.
    """
    controller = await _launch_headed(tmp_path)
    try:
        with _delayed_server() as base:
            await controller.navigate(base)
            # Inject the delay AFTER navigation settles and time it from there
            # (not from the static page's own on-load timer, whose countdown
            # overlaps unpredictably with navigation/settle overhead) so the
            # elapsed measurement below is a real assertion about polling,
            # not noise from how long the page took to load.
            await controller.page.evaluate(
                "() => setTimeout(() => {"
                "  var el = document.createElement('div');"
                "  el.id = 'resumed';"
                "  document.body.appendChild(el);"
                "}, 1500)"
            )
            started = time.monotonic()
            result = await controller.wait_for_human(
                until_selector="#resumed", timeout_s=8, poll_ms=100
            )
            elapsed = time.monotonic() - started
    finally:
        await controller.close()
    assert result is True
    # Browser timers in this automated/backgrounded context run compressed
    # relative to wall clock (measured: a nominal 700ms setTimeout resolved in
    # ~230ms) -- so this floor is deliberately loose, wide enough to absorb
    # that compression while still clearly ruling out an instant, no-poll
    # return (which would land near 0.00-0.02s, an order of magnitude below).
    assert elapsed >= 0.15, f"returned too early ({elapsed:.2f}s) -- not real polling"
    # ...but well short of the 8s timeout (not just waiting out the clock).
    assert elapsed < 5.0, f"took {elapsed:.2f}s -- did not detect the element promptly"


@pytest.mark.asyncio
async def test_wait_for_human_times_out_when_selector_never_appears(tmp_path):
    """A selector that will never exist must return False once the (short)
    timeout elapses -- not hang, not raise."""
    controller = await _launch_headed(tmp_path)
    try:
        with _delayed_server() as base:
            await controller.navigate(base)
            result = await controller.wait_for_human(
                until_selector="#never-exists", timeout_s=1, poll_ms=100
            )
    finally:
        await controller.close()
    assert result is False


@pytest.mark.asyncio
async def test_wait_for_human_polls_until_url_contains(tmp_path):
    """The until_url variant: the page navigates itself to a new URL on a
    delay: proves the URL-based resume condition is actually polled, not just
    checked once against the entry URL."""
    controller = await _launch_headed(tmp_path)
    try:
        with _delayed_server() as base:
            await controller.navigate(base)
            await controller.page.evaluate(
                "() => setTimeout(() => { location.href = location.origin "
                "+ '/redirect-target'; }, 700)"
            )
            result = await controller.wait_for_human(
                until_url="/redirect-target", timeout_s=5, poll_ms=100
            )
    finally:
        await controller.close()
    assert result is True
    assert "/redirect-target" in controller.page_url
