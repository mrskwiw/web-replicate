"""Tech fingerprint detects prod-build stacks (skips if Chromium absent).

Exercises the hard case the old fingerprint missed: a production React+Vite app
that exposes no window.React — detected via DOM fiber markers + asset-URL build
inference — plus passive library detection.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from engine.cli import cli

SPA = (Path(__file__).parent / "fixtures" / "spa.html").resolve()
VANILLA = (Path(__file__).parent / "fixtures" / "app.html").resolve()


def _capture(url, tmp_path):
    res = CliRunner().invoke(cli, ["capture", "--url", url, "--out-dir", str(tmp_path / "c")])
    if res.exit_code != 0:
        msg = str(res.exception or res.output)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Chromium not installed for Playwright")
        raise AssertionError(msg)
    return json.loads(res.output)["tech"]


def test_detects_prod_react_and_vite(tmp_path):
    tech = _capture(SPA.as_uri(), tmp_path)
    # React detected WITHOUT window.React, purely from fiber DOM markers
    assert "React" in tech["frameworks"]
    # Vite inferred from the module bundle under /assets/*-[hash].js
    assert tech["build_tool"] == "Vite"


def test_detects_libraries(tmp_path):
    tech = _capture(SPA.as_uri(), tmp_path)
    libs = tech["libraries"]
    assert "Stripe.js" in libs
    assert "Google Analytics / GTM" in libs
    assert "Sentry" in libs
    assert "Tailwind CSS" in libs  # from atomic utility class patterns


def test_vanilla_page_has_no_false_positives(tmp_path):
    tech = _capture(VANILLA.as_uri(), tmp_path)
    # The plain fixture is not a SPA — no framework/build-tool should be claimed
    assert tech["frameworks"] == []
    assert tech["build_tool"] is None
    assert "React" not in tech["frameworks"]
