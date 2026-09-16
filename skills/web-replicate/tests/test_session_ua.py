"""A saved session must always name the UA it was established under.

``null`` there is silently poisonous: the replay falls through to its own default
— the ``HeadlessChrome`` string for a headless run — so the bundle is replayed
under a different device fingerprint than it was created with. That presents as
an expired token, and the misdiagnosis costs a re-login every time.

Driven through the controller rather than the CLI because ``save_session`` is the
unit under test; ``trace --save-session`` merely calls it.
"""

from __future__ import annotations

import json

import pytest

from engine.browser import CaptureController

PAGE = "<!doctype html><title>ua fixture</title><h1>ok</h1>"


async def _save(tmp_path, user_agent=None):
    controller = CaptureController(
        out_dir=str(tmp_path / "out"), headless=True, user_agent=user_agent
    )
    try:
        await controller.launch()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            pytest.skip("Chromium not installed for Playwright")
        raise
    try:
        page = tmp_path / "fixture.html"
        page.write_text(PAGE, encoding="utf-8")
        await controller.navigate(page.as_uri())
        out = tmp_path / "session.json"
        await controller.save_session(str(out), user_agent=user_agent)
    finally:
        await controller.close()
    return json.loads(out.read_text(encoding="utf-8"))


async def test_unpinned_session_records_the_browsers_real_user_agent(tmp_path):
    bundle = await _save(tmp_path)
    ua = bundle["user_agent"]
    assert ua, "session bundle recorded a null/empty user_agent"
    assert "Mozilla" in ua, ua


async def test_pinned_user_agent_is_recorded_verbatim(tmp_path):
    """The probe must not override an explicit choice."""
    pinned = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ReplicateSession"
    bundle = await _save(tmp_path, user_agent=pinned)
    assert bundle["user_agent"] == pinned
