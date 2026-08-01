"""Secret redaction for captured traffic.

A replication capture records request/response **headers and bodies** — which on
any authenticated app carry bearer tokens, session cookies, and PII. The default
posture is *redact*, mirroring web-qa's safety stance: the blueprint documents
the *shape* of the backend contract, not live credentials. Pass
``--include-secrets`` to the CLI to disable all redaction (only ever on a target
you own, when the raw values are genuinely needed).

Redaction is a best-effort net, not a guarantee — it covers the well-known
carriers (auth/cookie headers, common credential JSON keys, bearer/JWT-looking
tokens). Treat any capture as sensitive regardless.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, overload

REDACTED = "«redacted»"

# Request/response header names whose *entire value* is a secret.
_SECRET_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "x-auth-token",
    "x-csrf-token",
    "x-xsrf-token",
    "x-access-token",
    "x-session-token",
    "api-key",
    "authentication",
}

# JSON-ish keys whose value should be redacted, matched case-insensitively as
# "key": "value" (handles the overwhelmingly common string-valued case).
_SECRET_KEYS = (
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "private_key",
    "session",
    "credentials",
)
_KEY_RE = re.compile(
    r'("(?:' + "|".join(_SECRET_KEYS) + r')"\s*:\s*)"(?:[^"\\]|\\.)*"',
    re.IGNORECASE,
)

# Bearer tokens / long JWT-looking blobs that appear as bare values in bodies.
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9._\-]{20,}\b")


def redact_headers(
    headers: Dict[str, str], *, enabled: bool = True
) -> Dict[str, str]:
    """Return headers with secret-carrying values replaced by :data:`REDACTED`."""
    if not enabled:
        return dict(headers)
    out: Dict[str, str] = {}
    for name, value in headers.items():
        out[name] = REDACTED if name.lower() in _SECRET_HEADERS else value
    return out


@overload
def redact_body(text: str, *, enabled: bool = True) -> str: ...
@overload
def redact_body(text: None, *, enabled: bool = True) -> None: ...
def redact_body(text: Optional[str], *, enabled: bool = True) -> Optional[str]:
    """Return a text body with common secret patterns redacted.

    Best-effort over JSON credential keys plus bearer/JWT-looking tokens. Leaves
    structure intact so the agent can still read the request/response *shape*.
    Redaction preserves non-``None`` input (the overloads encode ``str -> str``),
    so callers holding a known-present body keep a ``str`` type.
    """
    if text is None or not enabled:
        return text
    out = _KEY_RE.sub(lambda m: m.group(1) + f'"{REDACTED}"', text)
    out = _BEARER_RE.sub(f"Bearer {REDACTED}", out)
    out = _JWT_RE.sub(REDACTED, out)
    return out


def redact_cookie_value(value: str, *, enabled: bool = True) -> str:
    """Redact a cookie value (kept as a fixed marker so shape is still visible)."""
    return REDACTED if enabled else value


# Web-Storage keys whose *value* is a credential (token-in-localStorage auth is
# common in SPAs). Substring-matched, case-insensitive.
_SECRET_STORAGE_KEYS = (
    "token",
    "auth",
    "session",
    "secret",
    "password",
    "jwt",
    "credential",
    "api_key",
    "apikey",
    "access",
    "refresh",
    "bearer",
)


def _value_looks_secret(value: str) -> bool:
    """A stored value that is itself a JWT/bearer blob, regardless of its key."""
    return bool(_JWT_RE.search(value) or _BEARER_RE.search(value))


def redact_storage(store: Dict[str, str], *, enabled: bool = True) -> Dict[str, str]:
    """Redact secret-carrying Web Storage **values**, preserving every key.

    A faithful rebuild needs to know *which* keys the app writes (the storage
    contract), but the live token sitting in ``access_token`` is a credential —
    so the key stays and the value becomes :data:`REDACTED`. Triggers on a
    secret-ish key name or a JWT/bearer-shaped value.
    """
    if not enabled:
        return dict(store)
    out: Dict[str, str] = {}
    for key, value in store.items():
        kl = key.lower()
        secret = any(tok in kl for tok in _SECRET_STORAGE_KEYS) or (
            isinstance(value, str) and _value_looks_secret(value)
        )
        out[key] = REDACTED if secret else value
    return out
