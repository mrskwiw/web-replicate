"""Headless-browser driver + reconstruction-grade capture.

``CaptureController`` wraps Playwright's async API. It borrows web-qa's lifecycle,
persistent-auth (``storage_state`` + ``user_agent``), action dispatch, and settle
helpers almost verbatim, and adds the capture web-qa never needed: a full
request/response harvest (with sanitized bodies) and a whole-page assembly —
rendered HTML, asset graph, Web Storage, and tech fingerprint — with large blobs
written to a capture directory and referenced by relative path.

It makes no inferences. Turning captures into a backend model + blueprint is the
agent's job (see ../SKILL.md).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright

from . import capture as cap
from . import sanitize
from .models import (
    Action,
    ActionType,
    AssetKind,
    AssetRef,
    ConsoleMessage,
    Cookie,
    DesignTokens,
    FormField,
    FormInfo,
    InteractiveElement,
    LinkInfo,
    NetworkRecord,
    PageCapture,
    StorageSnapshot,
    TechFingerprint,
)


class CaptureController:
    """Drive a single page and capture it in enough detail to rebuild it."""

    def __init__(
        self,
        out_dir: str,
        engine: str = "chromium",
        headless: bool = True,
        viewport_width: int = 1366,
        viewport_height: int = 900,
        timeout_ms: int = 20000,
        nav_idle_ms: Optional[int] = None,
        storage_state: Optional[Any] = None,
        user_agent: Optional[str] = None,
        redact: bool = True,
        max_inline_body: int = 8192,
        download_assets: bool = False,
        action_timeout_ms: int = 6000,
        shot_timeout_ms: int = 12000,
        eval_budget_ms: int = 8000,
    ) -> None:
        self._out = Path(out_dir)
        self._engine = engine
        self._headless = headless
        self._viewport = {"width": viewport_width, "height": viewport_height}
        self._timeout = timeout_ms
        # Default per-ACTION timeout (clicks/fills/wait_for/evaluate) — deliberately
        # short so a missing selector fails fast (~6s) instead of stalling 20s per step.
        # Navigation (``goto``) keeps the longer ``timeout_ms`` explicitly.
        self._action_timeout_ms = action_timeout_ms
        # Screenshot + per-evaluate HARD budgets. A page saturated by SSE/websocket or
        # infinite scroll can make full-page screenshot / DOM serialization never
        # return; these wall-clock caps guarantee a capture completes (degraded) rather
        # than hanging the whole run (the /communities failure mode).
        self._shot_timeout_ms = shot_timeout_ms
        self._eval_budget_ms = eval_budget_ms
        # Bounded, best-effort post-load settle; never a hard networkidle wait.
        # Kept SHORT (web-qa's proven 3s default) on purpose: apps with constant
        # background traffic — Next.js route prefetching, polling, websockets, SSE,
        # analytics — never reach networkidle, so a long budget here would block the
        # FULL window on every navigation (a 6-step trace of a prefetch-heavy site
        # would stall for minutes). A page that never idles simply proceeds after
        # this; per-step ``settle_ms`` covers genuinely slow async work.
        self._nav_idle_ms = nav_idle_ms if nav_idle_ms is not None else 3000
        self._storage_state = storage_state
        self._user_agent = user_agent
        self._redact = redact
        self._max_inline_body = max_inline_body
        self._download_assets = download_assets

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

        self._console: List[ConsoleMessage] = []
        self._page_errors: List[str] = []
        # Raw Playwright Response objects, harvested (bodies pulled) on demand. A
        # harvested handle is set to None afterward so its browser-side body buffer
        # can be freed while the list length (and thus response_count marks) stays
        # stable across a long trace.
        self._responses: List[Any] = []
        self._blob_seq = 0
        self._entry_url: Optional[str] = None
        # URLs already fetched by --download-assets, so a multi-step trace of one SPA
        # doesn't re-download the same shared CSS/JS/font bundle every step.
        self._downloaded: Dict[str, Optional[str]] = {}

    # -- lifecycle (borrowed from web-qa) ---------------------------------

    async def launch(self) -> None:
        self._pw = await async_playwright().start()
        browser_type = getattr(self._pw, self._engine)
        self._browser = await browser_type.launch(headless=self._headless)
        ctx_kwargs: Dict[str, Any] = {"viewport": self._viewport}
        if self._user_agent:
            ctx_kwargs["user_agent"] = self._user_agent
        if self._storage_state is not None:
            ctx_kwargs["storage_state"] = self._storage_state
        self._context = await self._browser.new_context(**ctx_kwargs)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self._action_timeout_ms)
        self._wire_listeners()

    async def save_session(self, path: str, user_agent: Optional[str] = None) -> str:
        """Persist the live context's auth session (cookies + localStorage) plus
        user-agent to a session bundle, for replay via ``--session``. Same format
        and rationale as web-qa (tokens are UA+IP-fingerprint-bound)."""
        state = await self._context.storage_state()
        bundle = {"user_agent": user_agent or self._user_agent, "storage_state": state}
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
        return str(target)

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()

    def _wire_listeners(self) -> None:
        self._page.on("console", self._on_console)
        self._page.on("pageerror", lambda exc: self._page_errors.append(str(exc)))
        self._page.on("response", lambda resp: self._responses.append(resp))

    def _on_console(self, msg) -> None:
        loc = None
        try:
            location = msg.location
            if location and location.get("url"):
                loc = f"{location['url']}:{location.get('lineNumber', 0)}:{location.get('columnNumber', 0)}"
        except Exception:  # noqa: BLE001  # nosec B110 — a bad record must not abort
            pass
        self._console.append(ConsoleMessage(level=msg.type, text=msg.text, location=loc))

    # -- navigation & actions (borrowed from web-qa) ----------------------

    async def navigate(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=self._nav_idle_ms)
        except Exception:  # noqa: BLE001  # nosec B110 — persistent connections: proceed
            pass

    async def perform(self, action: Action) -> None:
        """Dispatch a single action, then wait briefly for the page to settle."""
        t = action.type
        if t is ActionType.NAVIGATE:
            await self.navigate(_require(action.url, "url"))
        elif t is ActionType.CLICK:
            await self._page.click(_require(action.selector, "selector"))
        elif t is ActionType.FILL:
            await self._page.fill(_require(action.selector, "selector"), action.value or "")
        elif t is ActionType.TYPE:
            await self._page.type(_require(action.selector, "selector"), action.text or "")
        elif t is ActionType.PRESS:
            await self._page.press(action.selector or "body", _require(action.key, "key"))
        elif t is ActionType.SCROLL:
            distance = int(action.value) if action.value else 500
            await self._page.mouse.wheel(0, distance)
        elif t is ActionType.WAIT_FOR:
            await self._page.wait_for_selector(_require(action.selector, "selector"))
        elif t is ActionType.SELECT:
            sel = _require(action.selector, "selector")
            option = action.value if action.value is not None else (action.text or "")
            try:
                await self._page.select_option(sel, label=option)
            except Exception:  # noqa: BLE001  # nosec B110 — retry by value
                await self._page.select_option(sel, value=option)
        else:  # pragma: no cover — enum is exhaustive
            raise ValueError(f"Unsupported action type: {t}")
        await self._page.wait_for_timeout(300)

    async def settle(self, ms: int) -> None:
        await self._page.wait_for_timeout(ms)

    async def wait_for_api(
        self,
        path_contains: str,
        method: Optional[str] = None,
        since: int = 0,
        timeout_ms: int = 20000,
        poll_ms: int = 250,
    ) -> bool:
        """Wait until a response matching ``path_contains`` (+ optional ``method``)
        appears after index ``since``, or timeout. For slow async actions whose
        response lands after a fixed settle window (web-qa convention)."""
        want = method.upper() if method else None
        waited = 0
        while True:
            for resp in self._responses[since:]:
                try:
                    if path_contains in resp.url and (
                        want is None or resp.request.method.upper() == want
                    ):
                        return True
                except Exception:  # noqa: BLE001  # nosec B110
                    continue
            if waited >= timeout_ms:
                return False
            await self._page.wait_for_timeout(poll_ms)
            waited += poll_ms

    def response_count(self) -> int:
        """Number of responses seen so far — a mark for per-step network deltas."""
        return len(self._responses)

    def console_count(self) -> int:
        """Number of console messages seen so far — a mark for per-step deltas."""
        return len(self._console)

    def console_since(self, i: int) -> List[ConsoleMessage]:
        """Console messages captured after index ``i`` (a per-step delta)."""
        return list(self._console[i:])

    @property
    def page_url(self) -> str:
        return self._page.url

    # -- blob writing -----------------------------------------------------

    def _write_text(self, subdir: str, name: str, text: str) -> str:
        """Write a text blob under the capture dir; return its relative posix ref."""
        target = self._out / subdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target.relative_to(self._out).as_posix()

    def _write_bytes(self, subdir: str, name: str, data: bytes) -> str:
        target = self._out / subdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target.relative_to(self._out).as_posix()

    async def screenshot(self, subdir: str, name: str) -> Optional[str]:
        """Full-page screenshot, bounded. Full-page can hang on infinite-scroll /
        live pages, so it's time-capped; on overrun it falls back to a viewport shot,
        and if even that fails the capture proceeds without a screenshot (never a
        blocker)."""
        target = self._out / subdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        for full_page in (True, False):
            try:
                await asyncio.wait_for(
                    self._page.screenshot(
                        path=str(target), full_page=full_page,
                        animations="disabled", timeout=self._shot_timeout_ms,
                    ),
                    self._shot_timeout_ms / 1000 + 2,
                )
                return target.relative_to(self._out).as_posix()
            except Exception:  # noqa: BLE001 — try viewport, then give up gracefully
                continue
        return None

    # -- network harvest --------------------------------------------------

    async def harvest_network(self, since: int = 0, body_subdir: str = "bodies") -> List[NetworkRecord]:
        """Turn the raw responses since index ``since`` into sanitized
        :class:`NetworkRecord`s. Text bodies below the inline cap are embedded;
        larger ones are written to ``body_subdir`` and referenced. Binary
        responses (images/fonts/media) carry metadata only.
        """
        records: List[NetworkRecord] = []
        end = len(self._responses)
        for idx in range(since, end):
            resp = self._responses[idx]
            if resp is None:  # already harvested & freed on a prior window
                continue
            try:
                req = resp.request
                req_headers = sanitize.redact_headers(
                    await req.all_headers(), enabled=self._redact
                )
                resp_headers = sanitize.redact_headers(
                    await resp.all_headers(), enabled=self._redact
                )
                resp_ct = cap.short_content_type(cap.header_get(resp_headers, "content-type"))
                req_ct = cap.short_content_type(cap.header_get(req_headers, "content-type"))
                rec = NetworkRecord(
                    method=req.method,
                    url=resp.url,
                    resource_type=req.resource_type,
                    status=resp.status,
                    request_headers=req_headers,
                    request_content_type=req_ct,
                    request_body=sanitize.redact_body(req.post_data, enabled=self._redact),
                    response_headers=resp_headers,
                    response_content_type=resp_ct,
                )
                if cap.is_textual(resp_ct):
                    body = await self._safe_body(resp)
                    if body is not None:
                        rec.body_bytes = len(body.encode("utf-8", "ignore"))
                        body = sanitize.redact_body(body, enabled=self._redact)
                        if rec.body_bytes <= self._max_inline_body:
                            rec.response_body = body
                        else:
                            self._blob_seq += 1
                            ext = "json" if resp_ct and "json" in resp_ct else "txt"
                            rec.response_body_ref = self._write_text(
                                body_subdir, f"body-{self._blob_seq:04d}.{ext}", body
                            )
                records.append(rec)
            except Exception:  # noqa: BLE001 — a single bad record must not sink the harvest
                continue
        # Free the harvested handles (their browser-side bodies are pulled now), but
        # keep the slots so response_count()/since indices stay valid for later steps.
        for idx in range(since, end):
            self._responses[idx] = None
        return records

    async def _safe_body(self, resp) -> Optional[str]:
        try:
            return await resp.text()
        except Exception:  # noqa: BLE001 — no body / redirect / already consumed
            return None

    # -- storage & cookies ------------------------------------------------

    async def capture_storage(self) -> StorageSnapshot:
        raw = await self._page.evaluate(cap.STORAGE_JS)
        cookies = await self._context.cookies()
        cookie_models = [
            Cookie(
                name=c.get("name", ""),
                value=sanitize.redact_cookie_value(
                    str(c.get("value", "")), enabled=self._redact
                ),
                domain=c.get("domain", ""),
                path=c.get("path", "/"),
                http_only=bool(c.get("httpOnly", False)),
                secure=bool(c.get("secure", False)),
                same_site=c.get("sameSite"),
                expires=c.get("expires"),
            )
            for c in cookies
        ]
        return StorageSnapshot(
            local=sanitize.redact_storage(raw.get("local", {}), enabled=self._redact),
            session=sanitize.redact_storage(raw.get("session", {}), enabled=self._redact),
            cookies=cookie_models,
        )

    # -- full page capture ------------------------------------------------

    async def capture_page(
        self,
        name: str,
        network: List[NetworkRecord],
        *,
        screenshot: bool = False,
        save_html: bool = True,
        console_from: int = 0,
    ) -> PageCapture:
        """Assemble a :class:`PageCapture` for the current page. ``network`` is the
        already-harvested record list (the caller controls the since-index so a
        traced step gets only its own delta). ``console_from`` mirrors that for the
        console: a traced step passes its start mark so each step's capture holds only
        the console it produced, not the cumulative log (which grew O(steps²))."""
        page = self._page
        errors: List[str] = []

        async def ev(js: str, default: Any, label: str, budget_ms: Optional[int] = None) -> Any:
            """Run one page-side collector under a hard cap. On overrun/error, record
            the field name and fall back to ``default`` so the capture still completes."""
            try:
                return await asyncio.wait_for(
                    page.evaluate(js), (budget_ms or self._eval_budget_ms) / 1000
                )
            except Exception as exc:  # noqa: BLE001 — degrade this field, don't hang the run
                errors.append(f"{label} ({type(exc).__name__})")
                return default

        meta = await ev(cap.META_JS, {}, "meta")
        snapshot = await ev(
            cap.SNAPSHOT_JS,
            {"interactive": [], "forms": [], "fields": [], "links": []},
            "snapshot",
        )
        assets_raw = await ev(
            cap.ASSETS_JS,
            {"stylesheets": [], "scripts": [], "images": [], "fonts": [], "media": [], "inline_styles": []},
            "assets",
        )
        tech_raw = await ev(cap.TECH_JS, {}, "tech")
        dom_outline = await ev(cap.DOM_OUTLINE_JS, "", "dom_outline")
        content = await ev(cap.CONTENT_JS, "", "content")
        tokens_raw = await ev(cap.DESIGN_TOKENS_JS, None, "design_tokens")

        try:
            storage = await asyncio.wait_for(
                self.capture_storage(), self._eval_budget_ms / 1000
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"storage ({type(exc).__name__})")
            storage = StorageSnapshot()

        html_ref, html_bytes = None, 0
        if save_html:
            try:
                html = await asyncio.wait_for(page.content(), self._eval_budget_ms / 1000)
                html_bytes = len(html.encode("utf-8", "ignore"))
                html_ref = self._write_text("pages", f"{name}.html", html)
            except Exception as exc:  # noqa: BLE001 — huge/never-settling DOM
                errors.append(f"html ({type(exc).__name__})")

        shot_ref = None
        if screenshot:
            shot_ref = await self.screenshot("screenshots", f"{name}.png")
            if shot_ref is None:
                errors.append("screenshot (timeout)")

        interactive = [
            InteractiveElement(
                selector=e["selector"], role=e["role"], text=e["text"],
                kind=e["kind"], rank=e["rank"],
            )
            for e in snapshot["interactive"]
        ]
        forms = [
            FormInfo(
                selector=f["selector"],
                fields=[_field(x) for x in f["fields"]],
                submit=f["submit"], action=f.get("action"), method=f.get("method"),
            )
            for f in snapshot["forms"]
        ]
        fields = [_field(x) for x in snapshot.get("fields", [])]
        links = [
            LinkInfo(
                selector=lk["selector"], text=lk["text"], href=lk["href"],
                scheme=lk["scheme"], external=lk["external"],
                new_tab=(lk["target"] == "_blank"),
            )
            for lk in snapshot["links"]
        ]

        stylesheets = [AssetRef(kind=AssetKind.STYLESHEET, url=a["url"], attrs=a["attrs"]) for a in assets_raw["stylesheets"]]
        scripts = [AssetRef(kind=AssetKind.SCRIPT, url=a["url"], attrs=a["attrs"]) for a in assets_raw["scripts"]]
        images = [AssetRef(kind=AssetKind.IMAGE, url=a["url"], attrs=a["attrs"]) for a in assets_raw["images"]]
        fonts = [AssetRef(kind=AssetKind.FONT, url=a["url"], attrs=a["attrs"]) for a in assets_raw["fonts"]]
        media = [AssetRef(kind=AssetKind.MEDIA, url=a["url"], attrs=a["attrs"]) for a in assets_raw["media"]]
        assets = images + fonts + media

        # Save any large inline <style> blocks as files; keep short ones inline.
        inline_styles: List[str] = []
        for i, css in enumerate(assets_raw.get("inline_styles", [])):
            if len(css) > self._max_inline_body:
                inline_styles.append(self._write_text("styles", f"{name}-inline-{i}.css", css))
            else:
                inline_styles.append(css)

        if self._download_assets:
            await self._download(stylesheets + scripts + assets)

        tech = self._build_tech(tech_raw, network, name)

        # Document response drives status; fall back to the page's own signal.
        doc = next((r for r in network if r.resource_type == "document"), None)
        status = doc.status if doc else 200

        return PageCapture(
            url=self._entry_url or page.url,
            final_url=page.url,
            title=await page.title(),
            status=status,
            ready_state=await page.evaluate("document.readyState"),
            lang=meta.get("lang"),
            meta=meta,
            html_ref=html_ref,
            html_bytes=html_bytes,
            dom_outline=dom_outline,
            content=content,
            interactive=interactive,
            forms=forms,
            fields=fields,
            links=links,
            stylesheets=stylesheets,
            scripts=scripts,
            assets=assets,
            inline_styles=inline_styles,
            design_tokens=_design_tokens(tokens_raw),
            storage=storage,
            console=list(self._console[console_from:]),
            network=network,
            tech=tech,
            screenshot_ref=shot_ref,
            capture_error="; ".join(errors) if errors else None,
        )

    def set_entry_url(self, url: str) -> None:
        """Record the URL the caller requested (may differ from final after redirects)."""
        self._entry_url = url

    def _build_tech(self, tech_raw: dict, network: List[NetworkRecord], name: str) -> TechFingerprint:
        doc = next((r for r in network if r.resource_type == "document"), None)
        server = powered_by = via = None
        if doc:
            server = cap.header_get(doc.response_headers, "server")
            powered_by = cap.header_get(doc.response_headers, "x-powered-by")
            via = cap.header_get(doc.response_headers, "via")
        build_ref = None
        build_data = tech_raw.get("build_data")
        if build_data:
            build_ref = self._write_text("tech", f"{name}-build-data.json", build_data)
        return TechFingerprint(
            frameworks=tech_raw.get("frameworks", []),
            build_tool=tech_raw.get("build_tool"),
            libraries=tech_raw.get("libraries", []),
            meta_generator=tech_raw.get("meta_generator"),
            server=server,
            powered_by=powered_by,
            via=via,
            build_data_ref=build_ref,
            evidence=tech_raw.get("evidence", []),
        )

    async def _download(self, assets: List[AssetRef]) -> None:
        """Best-effort download of asset bytes to the capture dir (``--download-assets``).

        Deduplicated across steps: a URL fetched once (in this or a prior step's
        capture) is reused, not re-fetched — a multi-step SPA trace shares one CSS/JS
        bundle instead of re-downloading it per step. A fetch failure is recorded on
        the asset (``download_error``) so a missing ``local_ref`` is explicit, not a
        silent under-count.
        """
        for a in assets:
            if not a.url.startswith(("http://", "https://")):
                continue
            if a.url in self._downloaded:  # already fetched (this or an earlier step)
                a.local_ref = self._downloaded[a.url]
                continue
            try:
                resp = await self._context.request.get(a.url, timeout=self._timeout)
                data = await resp.body()
                tail = a.url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "asset"
                self._blob_seq += 1
                a.local_ref = self._write_bytes(
                    "assets", f"{a.kind.value}-{self._blob_seq:04d}-{tail}"[:120], data
                )
                self._downloaded[a.url] = a.local_ref
            except Exception as exc:  # noqa: BLE001 — a failed download is non-fatal
                a.download_error = f"{type(exc).__name__}: {exc}"[:200]
                self._downloaded[a.url] = None  # don't retry a known-bad URL next step


def _design_tokens(raw: Optional[dict]) -> Optional[DesignTokens]:
    """Build :class:`DesignTokens` from the design-token collector's dict (or None)."""
    if not raw:
        return None
    return DesignTokens(
        css_variables=raw.get("css_variables", {}) or {},
        palette_text=raw.get("palette_text", []) or [],
        palette_bg=raw.get("palette_bg", []) or [],
        palette_border=raw.get("palette_border", []) or [],
        fonts=raw.get("fonts", []) or [],
        font_sizes=raw.get("font_sizes", []) or [],
        radii=raw.get("radii", []) or [],
        spacing=raw.get("spacing", []) or [],
        shadows=raw.get("shadows", []) or [],
        breakpoints=raw.get("breakpoints", []) or [],
        color_scheme=raw.get("color_scheme"),
        samples=raw.get("samples", {}) or {},
    )


def _field(x: dict) -> FormField:
    """Build a :class:`FormField` from the snapshot JS's field descriptor."""
    return FormField(
        selector=x["selector"],
        type=x["type"],
        name=x.get("name"),
        label=x.get("label", ""),
        required=bool(x.get("required", False)),
        placeholder=x.get("placeholder"),
        autocomplete=x.get("autocomplete"),
        minlength=x.get("minlength"),
        maxlength=x.get("maxlength"),
        min=x.get("min"),
        max=x.get("max"),
        pattern=x.get("pattern"),
        options=x.get("options", []) or [],
    )


def _require(value: Optional[str], field_name: str) -> str:
    if not value:
        raise ValueError(f"Action is missing required field: {field_name!r}")
    return value
