"""Step→action construction and env-var secret resolution (reused from web-qa)."""

import pytest

from engine.flow import MissingSecretError, build_action, resolve_str, slug


def test_resolve_env_whole_value():
    assert resolve_str({"env": "PW"}, {"PW": "s3cret"}) == "s3cret"


def test_resolve_interpolation():
    assert resolve_str("Bearer ${TOK}", {"TOK": "abc"}) == "Bearer abc"


def test_resolve_missing_raises():
    with pytest.raises(MissingSecretError):
        resolve_str({"env": "NOPE"}, {})
    with pytest.raises(MissingSecretError):
        resolve_str("${NOPE}", {})


def test_resolve_passthrough():
    assert resolve_str(None, {}) is None
    assert resolve_str(42, {}) == 42


def test_build_action_resolves_and_labels():
    step = {
        "type": "fill",
        "selector": "#pw",
        "value": {"env": "PW"},
        "label": "enter password",
    }
    a = build_action(step, {"PW": "hunter2"})
    assert a.type.value == "fill"
    assert a.value == "hunter2"
    assert a.intent == "enter password"  # label -> intent when intent absent


def test_slug():
    assert slug("Load Data!") == "load-data"
    assert slug("") == "step"
