"""RNW ranker fallback in `capture.py`'s shared-lineage `_SNAPSHOT_JS`-equivalent
(BUGS.md 2026-08-26, ported here for shared-lineage consistency with web-qa and
web-drive even though nothing in this engine currently caps/orders actions by
rank -- `trace` runs an explicit hand-authored step script, not an autonomous
rank-capped selection).
"""

from __future__ import annotations

import http.server
import json
import threading
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from engine.cli import cli

_FLAT_RANK_PAGE = (
    b"<!doctype html><title>Flat Rank</title><body>"
    b"<button>Home</button><button>Profile</button><button>Notifications</button>"
    b"<button>Settings</button><button>Menu</button><button>Help</button>"
    b"<button>Start Now</button>"
    b"</body>"
)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        body = _FLAT_RANK_PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@contextmanager
def _server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        host, port = srv.server_address
        yield f"http://{host}:{port}"
    finally:
        srv.shutdown()


def test_capture_falls_back_to_label_priority_when_every_control_ties_at_one_rank(
    tmp_path,
):
    """A page with no semantic landmarks (React Native Web's shape) ties every
    control at rank 4, so the primary landmark-based ranker learned nothing.
    `interactive[0]` -- last in the DOM -- must sort to the front via the
    label-priority fallback."""
    out_dir = tmp_path / "cap"
    with _server() as base:
        res = CliRunner().invoke(
            cli, ["capture", "--url", base, "--out-dir", str(out_dir)]
        )
    if res.exit_code != 0:
        msg = str(res.exception or res.output)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Chromium not installed for Playwright")
        raise AssertionError(msg)
    page = json.loads((out_dir / "page.json").read_text(encoding="utf-8"))
    interactive = page["interactive"]
    assert interactive[0]["text"] == "Start Now", (
        f"the one action-shaped label did not sort to the front: {interactive}"
    )
    assert interactive[0]["rank"] == 0
