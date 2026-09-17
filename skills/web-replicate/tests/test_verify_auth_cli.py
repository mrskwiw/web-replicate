"""``verify-auth``'s own CLI-level validation (WR-T5, optimization plan v2.0):
`probe_endpoints` itself is tested via `test_authcheck_network.py`; this covers
the argument-handling branches around it that had zero coverage -- the two
`ClickException` guards and the "accept a full blueprint/results object, not
just a bare backend.json" unwrap (`data.get("backend", data)`).
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from contextlib import contextmanager

from click.testing import CliRunner

from engine.cli import cli

_SESSION = {"cookies": [], "origins": []}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

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


def _write(tmp_path, name, obj):
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


def test_verify_auth_refuses_a_backend_file_with_no_endpoints(tmp_path):
    backend_path = _write(tmp_path, "backend.json", {"base_url": "http://example.invalid"})
    session_path = _write(tmp_path, "session.json", _SESSION)

    res = CliRunner().invoke(
        cli, ["verify-auth", "--backend", backend_path, "--session", session_path]
    )
    assert res.exit_code != 0
    assert "No endpoints" in res.output


def test_verify_auth_refuses_when_no_base_url_resolves(tmp_path):
    backend_path = _write(
        tmp_path, "backend.json", {"endpoints": [{"method": "GET", "path": "/api/x"}]}
    )
    session_path = _write(tmp_path, "session.json", _SESSION)

    res = CliRunner().invoke(
        cli, ["verify-auth", "--backend", backend_path, "--session", session_path]
    )
    assert res.exit_code != 0
    assert "base" in res.output.lower()


def test_verify_auth_accepts_a_full_blueprint_object_not_just_bare_backend_json(tmp_path):
    """`data.get("backend", data)` must unwrap a full blueprint/results.json
    (endpoints nested under a "backend" key) exactly like a bare backend.json
    -- proven by actually completing a real probe against a local server,
    not just by not-crashing."""
    with _server() as base:
        blueprint = {
            "some_other_top_level_key": "irrelevant",
            "backend": {
                "base_url": base,
                "endpoints": [{"method": "GET", "path": "/api/open"}],
            },
        }
        backend_path = _write(tmp_path, "blueprint.json", blueprint)
        session_path = _write(tmp_path, "session.json", _SESSION)

        res = CliRunner().invoke(
            cli, ["verify-auth", "--backend", backend_path, "--session", session_path]
        )

    if res.exception and "Executable doesn't exist" in str(res.exception):
        import pytest
        pytest.skip("Playwright driver not available")
    assert res.exit_code == 0, res.output
    result = json.loads(res.output)
    assert result  # a real report came back, not an empty/error shape
