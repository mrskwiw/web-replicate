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

_UNIFORM_MAIN_CTA_PAGE = (
    b"<!doctype html><title>Uniform Main CTA</title><body>"
    b"<main><button>Delete</button><button>Edit</button><button>Archive</button></main>"
    b"</body>"
)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, page: bytes, **kwargs):
        self._page = page
        super().__init__(*args, **kwargs)

    def do_GET(self):  # noqa: N802
        body = self._page
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@contextmanager
def _server(page: bytes = _FLAT_RANK_PAGE):
    import functools

    handler = functools.partial(_Handler, page=page)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
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


def test_capture_does_not_apply_the_rank_fallback_to_a_page_with_several_real_main_ctas(
    tmp_path,
):
    """Post-commit Codex review (2026-09-19) of web-qa's copy, ported here:
    the original guard checked only that every control shares ONE rank, not
    that the shared rank is the UNRANKED default (4) -- a page with several
    legitimate main-CTA buttons and nothing else ALSO has every element at
    one rank (0, a real positive landmark match), and the buggy guard would
    have demoted "Delete"/"Edit"/"Archive" (matching neither keyword list)
    from their correct rank 0. Only a page where every element is UNRANKED
    (4) may trigger the fallback."""
    out_dir = tmp_path / "cap"
    with _server(_UNIFORM_MAIN_CTA_PAGE) as base:
        res = CliRunner().invoke(
            cli, ["capture", "--url", base, "--out-dir", str(out_dir)]
        )
    if res.exit_code != 0:
        msg = str(res.exception or res.output)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Chromium not installed for Playwright")
        raise AssertionError(msg)
    page = json.loads((out_dir / "page.json").read_text(encoding="utf-8"))
    ranks = {e["rank"] for e in page["interactive"]}
    assert ranks == {0}, (
        f"three real main CTAs sharing a legitimate rank must not be "
        f"reclassified by the RNW fallback: {page['interactive']}"
    )
