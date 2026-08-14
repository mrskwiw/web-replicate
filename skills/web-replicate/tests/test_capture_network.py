"""End-to-end network capture with REAL traffic (audit ID T9).

``harvest_network`` is the backend-inference backbone: every endpoint, shape and
auth guess in a blueprint is derived from what it records. Until now its coverage
came from static 2 KB fixtures and a fake-context download test, so the live path
-- request/response headers, POST bodies, redaction, the inline-vs-spill decision,
and per-step windowing -- was never exercised against an actual browser making
actual `fetch` calls.

This drives a local stdlib server through the ``trace`` subcommand and asserts on
the recorded path, covering:

* **redaction** of an `Authorization` header and of credential fields inside a
  JSON request body (the privacy guarantee the whole capture rests on),
* **inline vs. spill** -- a body over ``max_inline_body`` (8 KB) goes to
  ``bodies/`` and is referenced, not embedded,
* **per-step windowing** -- step 2's records contain step 2's calls and not
  step 1's, which is what makes per-step network deltas meaningful,
* **binary responses** carry metadata only, never bytes.

Skips (never fails) when Chromium is absent, matching the other live tests.
"""

from __future__ import annotations

import http.server
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner

from engine.cli import cli
from engine.sanitize import REDACTED

BIG_BODY_CHARS = 20_000  # comfortably over the 8192-byte inline cap
SECRET_TOKEN = "tok-super-secret-value-12345"
SECRET_PASSWORD = "hunter2-should-never-be-recorded"

# 1x1 transparent GIF -- a genuinely binary response.
PIXEL = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b"
)

PAGE = f"""<!doctype html>
<title>capture fixture</title>
<h1>Capture fixture</h1>
<button id="login" onclick="fetch('/api/login',{{
  method:'POST',
  headers:{{'Content-Type':'application/json','Authorization':'Bearer {SECRET_TOKEN}'}},
  body:JSON.stringify({{email:'a@b.c',password:'{SECRET_PASSWORD}'}})
}}).then(r=>r.text()).then(t=>document.getElementById('out').textContent='login:'+t)">Login</button>
<button id="big" onclick="fetch('/api/big').then(r=>r.text()).then(t=>
  document.getElementById('out').textContent='big:'+t.length)">Big</button>
<button id="img" onclick="fetch('/pixel.gif').then(r=>r.blob()).then(b=>
  document.getElementById('out').textContent='img:'+b.size)">Img</button>
<div id="out">idle</div>
""".encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, body, ctype="text/html; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 — stdlib callback name
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._send(200, b'{"ok":true,"user":"a@b.c"}', "application/json")

    def do_GET(self):  # noqa: N802 — stdlib callback name
        if self.path == "/api/big":
            self._send(
                200, ("x" * BIG_BODY_CHARS).encode(), "text/plain; charset=utf-8"
            )
        elif self.path == "/pixel.gif":
            self._send(200, PIXEL, "image/gif")
        else:
            self._send(200, PAGE)

    def log_message(self, *args):
        pass


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """Silence benign keep-alive disconnects; socketserver would otherwise
        print a traceback to stdout and corrupt the CLI JSON these tests parse."""


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


def _trace(tmp_path: Path, base: str, steps, extra=()):
    steps_file = tmp_path / "steps.json"
    steps_file.write_text(json.dumps(steps), encoding="utf-8")
    out_dir = tmp_path / "cap"
    res = CliRunner().invoke(
        cli,
        [
            "trace",
            "--url",
            base,
            "--steps",
            str(steps_file),
            "--out-dir",
            str(out_dir),
            *extra,
        ],
    )
    if res.exit_code != 0:
        msg = str(res.exception or res.output)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Chromium not installed for Playwright")
        raise AssertionError(f"trace failed (exit {res.exit_code}): {msg}")
    return json.loads((out_dir / "path.json").read_text(encoding="utf-8")), out_dir


def _find(records, fragment):
    return [r for r in records if fragment in r["url"]]


def test_live_capture_redacts_secrets_and_spills_large_bodies(tmp_path):
    with _server() as base:
        path, out_dir = _trace(
            tmp_path,
            base,
            [
                {
                    "type": "click",
                    "selector": "#login",
                    "label": "login",
                    "settle_ms": 800,
                },
                {"type": "click", "selector": "#big", "label": "big", "settle_ms": 800},
            ],
        )

    login_step, big_step = path["steps"][0], path["steps"][1]

    # -- redaction: the auth header and the password field never reach disk ----
    login = _find(login_step["http"], "/api/login")
    assert login, "the POST fired by the click was not captured"
    rec = login[0]
    assert rec["method"] == "POST"
    auth = {k.lower(): v for k, v in rec["request_headers"].items()}.get(
        "authorization"
    )
    assert auth == REDACTED, f"Authorization header not redacted: {auth!r}"
    assert SECRET_TOKEN not in json.dumps(rec)
    assert SECRET_PASSWORD not in json.dumps(rec), "password survived redaction"
    assert REDACTED in (rec["request_body"] or ""), "request body was not redacted"
    # the non-secret sibling field must survive -- redaction must not nuke the shape
    assert "email" in (rec["request_body"] or "")

    # -- inline vs spill -------------------------------------------------------
    small = login[0]
    assert small["response_body"], "a small JSON body should be inlined"
    assert small["response_body_ref"] is None

    big = _find(big_step["http"], "/api/big")
    assert big, "the large-body fetch was not captured"
    big_rec = big[0]
    assert big_rec["body_bytes"] >= BIG_BODY_CHARS
    assert big_rec["response_body"] is None, "an oversized body must not be inlined"
    assert big_rec["response_body_ref"], "an oversized body must be spilled to bodies/"
    spilled = out_dir / big_rec["response_body_ref"]
    assert spilled.exists(), f"spill file missing: {spilled}"
    assert len(spilled.read_text(encoding="utf-8")) >= BIG_BODY_CHARS

    # -- per-step windowing ----------------------------------------------------
    assert not _find(
        big_step["http"], "/api/login"
    ), "step 2 captured step 1's call — the per-step network delta is not windowed"


def test_binary_response_records_metadata_without_body(tmp_path):
    with _server() as base:
        path, _ = _trace(
            tmp_path,
            base,
            [
                {"type": "click", "selector": "#img", "label": "img", "settle_ms": 800},
            ],
        )
    img = _find(path["steps"][0]["http"], "/pixel.gif")
    assert img, "the image fetch was not captured"
    assert img[0]["response_content_type"].startswith("image/")
    assert img[0]["response_body"] is None, "binary bytes must never be inlined"
    assert img[0]["response_body_ref"] is None


def test_include_secrets_disables_redaction(tmp_path):
    """`--include-secrets` is the documented escape hatch; if it silently kept
    redacting, a user who needs raw values would get a blueprint that looks
    complete but isn't."""
    with _server() as base:
        path, _ = _trace(
            tmp_path,
            base,
            [
                {
                    "type": "click",
                    "selector": "#login",
                    "label": "login",
                    "settle_ms": 800,
                },
            ],
            extra=["--include-secrets"],
        )
    rec = _find(path["steps"][0]["http"], "/api/login")[0]
    auth = {k.lower(): v for k, v in rec["request_headers"].items()}.get(
        "authorization"
    )
    assert (
        auth == f"Bearer {SECRET_TOKEN}"
    ), "--include-secrets must preserve the raw header"
    assert SECRET_PASSWORD in (rec["request_body"] or "")
