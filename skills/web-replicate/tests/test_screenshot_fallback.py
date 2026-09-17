"""``screenshot()``'s two-stage fallback (WR-T3, optimization plan v2.0): a
full-page screenshot can hang on an infinite-scroll/live page, so it's
time-capped and falls back to a viewport-only shot; if even that fails, the
capture proceeds without one rather than blocking the whole run.

No real browser needed -- a fake `page.screenshot` deterministically fails on
the first call (`full_page=True`) and succeeds on the second (`full_page=False`),
same "inject a fake, skip launch()" technique `test_download.py` already uses
for `CaptureController`.
"""

from __future__ import annotations

import asyncio

from engine.browser import CaptureController


class _FakePage:
    """Records each screenshot attempt; fails full-page, succeeds viewport."""

    def __init__(self, *, fail_viewport_too: bool = False):
        self.calls: list[bool] = []
        self._fail_viewport_too = fail_viewport_too

    async def screenshot(self, *, path, full_page, animations, timeout):
        self.calls.append(full_page)
        if full_page or self._fail_viewport_too:
            raise TimeoutError("simulated: screenshot did not complete in time")
        # Real Playwright writes the file at `path`; mimic that so the
        # function's own `target.relative_to(self._out)` return value is real.
        with open(path, "wb") as f:
            f.write(b"fake-png-bytes")


def _controller(tmp_path, page):
    c = CaptureController(str(tmp_path), shot_timeout_ms=50)
    c._page = page  # inject fake, skip launch() -- see test_download.py's pattern
    return c


def test_screenshot_falls_back_to_viewport_when_full_page_fails(tmp_path):
    page = _FakePage()
    controller = _controller(tmp_path, page)

    ref = asyncio.run(controller.screenshot("screenshots", "example.png"))

    assert page.calls == [True, False], (
        "must try full_page=True first, then fall back to full_page=False -- "
        f"got {page.calls}"
    )
    assert ref == "screenshots/example.png"
    assert (tmp_path / "screenshots" / "example.png").exists()


def test_screenshot_gives_up_gracefully_when_both_attempts_fail(tmp_path):
    page = _FakePage(fail_viewport_too=True)
    controller = _controller(tmp_path, page)

    ref = asyncio.run(controller.screenshot("screenshots", "example.png"))

    assert page.calls == [True, False], "both stages must still be attempted"
    assert ref is None, "a capture must proceed without a screenshot, never raise"
    assert not (tmp_path / "screenshots" / "example.png").exists()
