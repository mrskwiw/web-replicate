"""`--low-memory` passes Chromium-only switches, so they must never reach a
firefox/webkit launch (post-commit review, 2026-09-22). Pure unit test of the
guard helper -- no browser needed. (This fork carries `engine` as a plain string,
not web-qa/web-drive's `BrowserEngine` enum.)"""

from __future__ import annotations

from engine.browser import _LOW_MEMORY_ARGS, _low_memory_args


def test_low_memory_args_apply_only_to_chromium():
    assert _low_memory_args("chromium", True) == _LOW_MEMORY_ARGS
    # Chromium switches would choke firefox/webkit -- correctly a no-op there.
    assert _low_memory_args("firefox", True) == []
    assert _low_memory_args("webkit", True) == []


def test_low_memory_args_empty_when_flag_off():
    assert _low_memory_args("chromium", False) == []
