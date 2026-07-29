"""Auth-enforcement verification — turn the *inferred* auth column into a *verified* one.

`web-replicate` captures passively, so it can only ever *infer* whether an endpoint
requires auth (usually from "we only saw it while logged in"). That inference is
systematically wrong for public read endpoints that the app simply never calls
while logged out (feed, public like/comment/rating counts, …). The only way to
settle it is to actually ask the server: request each **already-observed** endpoint
twice — once with the saved session, once anonymously — and compare.

This is the engine's **one active check** and it is deliberately narrow:
- **Verify, don't discover.** It probes only endpoints you already observed and pass
  in; it never enumerates or fuzzes hidden routes.
- **Read-only by default.** Only GET is probed unless ``include_mutating`` is set
  (a real write would execute) — so the default is safe on a target you own.

The pure helpers below (parsing / substitution / classification / reconciliation)
carry the logic and are unit-tested; the async ``probe_endpoints`` does the HTTP.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_URL_RE = re.compile(r"https?://[^\s'\"()]+")
_PARAM_RE = re.compile(r"\{([^{}]+)\}")

# Verbs that mutate state — withheld unless the caller opts in.
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def resolve_base_url(backend_base_url: Optional[str], override: Optional[str]) -> Optional[str]:
    """Pick the origin to probe against.

    ``override`` wins. Otherwise pull the first URL out of a backend ``base_url``
    string (which is often prose like ``"https://x/api (same-origin; …)"``) and
    return its scheme://host[:port], dropping any path/description.
    """
    if override:
        return override.rstrip("/")
    if not backend_base_url:
        return None
    m = _URL_RE.search(backend_base_url)
    if not m:
        return None
    url = m.group(0)
    # keep scheme://host[:port] only
    m2 = re.match(r"(https?://[^/]+)", url)
    return m2.group(1) if m2 else url.rstrip("/")


def parse_subs(pairs: Any) -> Dict[str, str]:
    """``["id=abc", "slug=x"]`` → ``{"id": "abc", "slug": "x"}``."""
    out: Dict[str, str] = {}
    for p in pairs or []:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def substitute_params(path: str, subs: Dict[str, str]) -> Optional[str]:
    """Replace ``{key}`` tokens from ``subs``. Return ``None`` if any ``{token}``
    is left unresolved (so the caller can skip-and-report rather than probe a
    literal ``/{id}`` route)."""
    def _rep(m: "re.Match[str]") -> str:
        return subs.get(m.group(1), m.group(0))

    resolved = _PARAM_RE.sub(_rep, path)
    return None if _PARAM_RE.search(resolved) else resolved


def classify(with_status: Optional[int], without_status: Optional[int]) -> str:
    """Classify one endpoint from its (authed, anonymous) status pair.

    - ``enforced``     — anonymous request rejected (401/403) or redirected (3xx).
    - ``public``       — anonymous request succeeded (2xx).
    - ``error-unauth`` — anonymous request 5xx'd: the guard throws instead of
      rejecting cleanly. A real defect (should be a 401), so surfaced distinctly.
    - ``inconclusive`` — a probe errored, or a status we can't judge (e.g. 404,
      often a missing path param or a route that needs a real id).
    """
    if without_status is None:
        return "inconclusive"
    if without_status in (401, 403):
        return "enforced"
    if 300 <= without_status < 400:
        return "enforced"
    if 500 <= without_status < 600:
        return "error-unauth"
    if 200 <= without_status < 300:
        return "public"
    return "inconclusive"


def _normalize_inferred(inferred: Optional[str]) -> str:
    """Reduce a free-text auth claim to ``auth`` | ``public`` | ``unknown``."""
    if not inferred:
        return "unknown"
    s = inferred.strip().lower()
    if s.startswith(("no", "public")) or "public" in s:
        return "public"
    if s.startswith(("yes", "auth", "refresh", "cookie", "bearer", "token")):
        return "auth"
    return "unknown"


def reconcile(inferred: Optional[str], verdict: str) -> Tuple[bool, str]:
    """Compare a verdict against the blueprint's inferred auth claim.

    Returns ``(matches, note)`` — ``matches`` is False whenever the verdict
    contradicts the inference or reveals a bug, so callers can count corrections.
    """
    norm = _normalize_inferred(inferred)
    if verdict == "error-unauth":
        return False, "BUG: 5xx unauthenticated (should be a clean 401)"
    if verdict == "inconclusive":
        return True, "inconclusive — see statuses (may need a path param or a real id)"
    if verdict == "enforced":
        if norm == "public":
            return False, "CORRECTED: inferred public, actually ENFORCED"
        return True, "agrees (enforced)"
    if verdict == "public":
        if norm == "auth":
            return False, "CORRECTED: inferred auth-required, actually PUBLIC"
        return True, "agrees (public)"
    return True, ""


def select_endpoints(
    endpoints: List[Dict[str, Any]], subs: Dict[str, str], include_mutating: bool
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split the inventory into ``(to_probe, skipped)``.

    Skipped endpoints carry a ``skip_reason`` (mutating-verb-withheld or an
    unresolved path param) so nothing is silently dropped from the report.
    """
    to_probe: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for e in endpoints:
        method = (e.get("method") or "GET").upper()
        path = e.get("path") or ""
        if method in _MUTATING and not include_mutating:
            skipped.append({**e, "skip_reason": "mutating verb withheld (read-only mode)"})
            continue
        resolved = substitute_params(path, subs)
        if resolved is None:
            skipped.append({**e, "skip_reason": "unresolved path param — pass --sub key=value"})
            continue
        to_probe.append({**e, "method": method, "resolved_path": resolved})
    return to_probe, skipped


async def probe_endpoints(
    base_url: str,
    endpoints: List[Dict[str, Any]],
    storage_state: Any,
    user_agent: Optional[str],
    subs: Dict[str, str],
    include_mutating: bool = False,
    timeout_ms: int = 15000,
) -> Dict[str, Any]:
    """Probe each selected endpoint with and without the session; return a report.

    Uses two Playwright API request contexts (one seeded from the session, one
    anonymous). No browser page is opened — this is an HTTP-level check.
    """
    from playwright.async_api import async_playwright

    to_probe, skipped = select_endpoints(endpoints, subs, include_mutating)
    results: List[Dict[str, Any]] = []

    async with async_playwright() as p:
        ctx_kwargs: Dict[str, Any] = {"base_url": base_url}
        if user_agent:
            ctx_kwargs["user_agent"] = user_agent
        authed = await p.request.new_context(storage_state=storage_state, **ctx_kwargs)
        anon = await p.request.new_context(**ctx_kwargs)
        try:
            for e in to_probe:
                method = e["method"]
                path = e["resolved_path"]

                async def _status(rc: Any) -> Optional[int]:
                    try:
                        resp = await rc.fetch(path, method=method, timeout=timeout_ms)
                        return resp.status
                    except Exception:  # noqa: BLE001 — a probe failure is data, not fatal
                        return None

                with_status = await _status(authed)
                without_status = await _status(anon)
                verdict = classify(with_status, without_status)
                matches, note = reconcile(e.get("auth_required"), verdict)
                results.append({
                    "method": method,
                    "path": e.get("path"),
                    "auth_inferred": e.get("auth_required"),
                    "with_auth_status": with_status,
                    "without_auth_status": without_status,
                    "verdict": verdict,
                    "matches_inference": matches,
                    "note": note,
                })
        finally:
            await authed.dispose()
            await anon.dispose()

    summary = {
        "probed": len(results),
        "enforced": sum(1 for r in results if r["verdict"] == "enforced"),
        "public": sum(1 for r in results if r["verdict"] == "public"),
        "error_unauth": sum(1 for r in results if r["verdict"] == "error-unauth"),
        "inconclusive": sum(1 for r in results if r["verdict"] == "inconclusive"),
        "corrections": sum(1 for r in results if not r["matches_inference"]),
        "skipped": len(skipped),
    }
    return {
        "base_url": base_url,
        "include_mutating": include_mutating,
        "summary": summary,
        "results": results,
        "skipped": skipped,
    }
