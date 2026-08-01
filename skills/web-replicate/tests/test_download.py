"""--download-assets dedup + failure recording (no browser — fake request context)."""

import asyncio

from engine.browser import CaptureController
from engine.models import AssetKind, AssetRef


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    async def body(self) -> bytes:
        return self._data


class _FakeRequest:
    """Counts fetches per URL and can be told to fail a given URL."""

    def __init__(self, fail=()):
        self.calls: list[str] = []
        self._fail = set(fail)

    async def get(self, url, timeout=None):
        self.calls.append(url)
        if url in self._fail:
            raise RuntimeError("boom")
        return _FakeResp(b"x" * 10)


class _FakeContext:
    def __init__(self, request):
        self.request = request


def _controller(tmp_path, request):
    c = CaptureController(str(tmp_path), download_assets=True)
    c._context = _FakeContext(request)  # inject fake, skip launch()
    return c


def test_download_dedups_repeated_urls(tmp_path):
    """The same asset URL is fetched once even across two _download calls (a
    multi-step trace of one SPA must not re-download shared bundles)."""
    req = _FakeRequest()
    c = _controller(tmp_path, req)
    a1 = AssetRef(kind=AssetKind.STYLESHEET, url="https://cdn.test/app.css")
    a2 = AssetRef(kind=AssetKind.STYLESHEET, url="https://cdn.test/app.css")

    asyncio.run(c._download([a1]))
    asyncio.run(c._download([a2]))  # second step, same URL

    assert req.calls == ["https://cdn.test/app.css"]  # fetched exactly once
    assert a1.local_ref is not None
    assert a2.local_ref == a1.local_ref  # reused, not re-fetched
    assert a1.download_error is None


def test_download_records_failure(tmp_path):
    """A failed fetch is recorded on the asset (not silently dropped), and the
    known-bad URL is not retried on a later step."""
    url = "https://cdn.test/missing.js"
    req = _FakeRequest(fail=[url])
    c = _controller(tmp_path, req)
    a1 = AssetRef(kind=AssetKind.SCRIPT, url=url)
    a2 = AssetRef(kind=AssetKind.SCRIPT, url=url)

    asyncio.run(c._download([a1]))
    asyncio.run(c._download([a2]))

    assert a1.local_ref is None
    assert a1.download_error and "boom" in a1.download_error
    assert req.calls == [url]  # not retried on the second call
