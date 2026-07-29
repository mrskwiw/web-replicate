"""Path steps: turn a step dict into an :class:`~engine.models.Action`.

Trimmed straight from web-qa's ``flow`` module — the *pure* pieces (no browser,
fully unit-testable). web-qa's per-step QA *assertions* are dropped: a
replication trace records what a path does, it doesn't judge pass/fail. What's
kept is exactly what a faithful traversal needs — action construction and
**secret resolution by env-var reference**, so login credentials are never
inlined in a ``path.json``.

A step dict is a ``capture``-style action plus optional keys: ``label`` (names
the step in the recording), ``intent`` (free-text note on what it accomplishes),
``await_response`` (``{method?, path_contains, timeout_ms?}`` — block until that
response lands before capturing, for slow async actions), and ``settle_ms``
(extra idle wait).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping

from .models import Action, ActionType

_ENV_REF = re.compile(r"\$\{(\w+)\}")


class MissingSecretError(KeyError):
    """A ``${VAR}`` / ``{"env": "VAR"}`` reference had no matching env var."""


def resolve_str(value: Any, env: Mapping[str, str]) -> Any:
    """Resolve a step field, substituting secrets from ``env``.

    - ``{"env": "NAME"}`` → ``env["NAME"]`` (whole-value form).
    - a string containing ``${NAME}`` → each ref replaced by ``env["NAME"]``.
    - anything else (incl. ``None``) → returned unchanged.

    Raises :class:`MissingSecretError` if a referenced var is absent, so a trace
    fails fast instead of sending an empty/``${VAR}``-literal credential.
    """
    if isinstance(value, dict) and "env" in value:
        name = value["env"]
        if name not in env:
            raise MissingSecretError(name)
        return env[name]
    if isinstance(value, str):

        def _sub(m: "re.Match[str]") -> str:
            name = m.group(1)
            if name not in env:
                raise MissingSecretError(name)
            return env[name]

        return _ENV_REF.sub(_sub, value)
    return value


def build_action(step: Dict[str, Any], env: Mapping[str, str]) -> Action:
    """Construct an :class:`Action` from a step dict, resolving secrets."""
    return Action(
        type=ActionType(step["type"]),
        selector=resolve_str(step.get("selector"), env),
        url=resolve_str(step.get("url"), env),
        text=resolve_str(step.get("text"), env),
        key=step.get("key"),
        value=resolve_str(step.get("value"), env),
        intent=step.get("intent") or step.get("label"),
    )


def slug(text: str) -> str:
    """Filesystem-safe short slug for per-step capture/screenshot names."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s or "step")[:40]
