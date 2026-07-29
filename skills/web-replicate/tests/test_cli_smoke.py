"""End-to-end CLI smoke test against a local fixture (skips if Chromium absent)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from engine.cli import cli

FIXTURE = (Path(__file__).parent / "fixtures" / "app.html").resolve()


def _invoke(args):
    res = CliRunner().invoke(cli, args)
    if res.exit_code != 0:
        msg = str(res.exception or res.output)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            pytest.skip("Chromium not installed for Playwright")
        raise AssertionError(msg)
    return res


def test_capture_page(tmp_path):
    out_dir = tmp_path / "cap"
    res = _invoke(["capture", "--url", FIXTURE.as_uri(), "--out-dir", str(out_dir)])
    page = json.loads(res.output)

    assert page["title"] == "Fixture Dashboard"
    assert page["lang"] == "en"
    # metadata + tech fingerprint
    assert page["meta"]["generator"] == "FixtureKit 1.0"
    assert page["tech"]["meta_generator"] == "FixtureKit 1.0"
    # client storage the app seeds must be observed
    assert page["storage"]["local"].get("fixture.theme") == "dark"
    # structure: the form's backend target is captured
    actions = [f.get("action") for f in page["forms"]]
    assert "/api/save" in actions
    # form field carries its visible <label> + declared validation
    title = next(x for f in page["forms"] for x in f["fields"] if x["name"] == "title")
    assert title["label"] == "Post Title"
    assert title["required"] is True
    assert title["minlength"] == 5 and title["maxlength"] == 80
    # <select> options are captured
    vis = next(x for f in page["forms"] for x in f["fields"] if x["name"] == "visibility")
    assert vis["options"] == ["Public", "Private"]
    # page-level `fields` inventory includes the FORMLESS labeled textarea the
    # <form> scan can't see (the wizard case)
    bio = next(x for x in page["fields"] if x["name"] == "bio")
    assert bio["label"] == "Business Description"
    assert bio["type"] == "textarea"
    # ...and still includes the in-form fields
    assert {"title", "visibility", "bio"} <= {x["name"] for x in page["fields"]}
    # inline <style> block captured
    assert any(".cta" in s for s in page["inline_styles"])
    # rendered markup written to disk and referenced
    assert page["html_ref"] == "pages/page.html"
    assert (out_dir / "pages" / "page.html").exists()
    assert (out_dir / "page.json").exists()


def test_trace_records_path(tmp_path):
    out_dir = tmp_path / "trace"
    steps = [
        {"type": "click", "selector": "#load", "label": "load data",
         "intent": "fetch dashboard data", "settle_ms": 300}
    ]
    steps_file = tmp_path / "steps.json"
    steps_file.write_text(json.dumps(steps), encoding="utf-8")

    res = _invoke([
        "trace", "--url", FIXTURE.as_uri(), "--steps", str(steps_file),
        "--out-dir", str(out_dir), "--name", "load-flow",
    ])
    rec = json.loads(res.output)

    assert rec["name"] == "load-flow"
    assert len(rec["steps"]) == 1
    step = rec["steps"][0]
    assert step["action"]["type"] == "click"
    assert step["capture_ref"] == "steps/01-load-data.json"
    # the click updates #result — the after-capture must reflect it
    assert (out_dir / "steps" / "01-load-data.json").exists()
    cap = json.loads((out_dir / "steps" / "01-load-data.json").read_text(encoding="utf-8"))
    assert "loaded" in cap["content"] or "ok" in cap["content"]
    assert (out_dir / "path.json").exists()
