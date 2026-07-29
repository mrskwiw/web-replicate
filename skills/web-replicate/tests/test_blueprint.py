"""Blueprint rendering produces the three deliverables and a readable spec."""

import json

from engine.blueprint import BlueprintGenerator


def _results():
    return {
        "metadata": {"target_url": "https://ex.com", "engine": "chromium", "captured_at": "2026-07-23"},
        "tech": {"frameworks": ["Next.js", "React"], "server": "vercel", "evidence": ["__NEXT_DATA__ present"]},
        "backend": {
            "base_url": "https://ex.com/api",
            "auth": "Bearer JWT in Authorization header",
            "endpoints": [
                {"method": "GET", "path": "/api/data", "purpose": "list", "auth_required": "yes",
                 "request_shape": "-", "response_shape": "{items:[]}"}
            ],
            "data_models": [{"name": "Item", "fields": ["id", "title"]}],
            "notes": ["credits deducted on POST /api/run"],
        },
        "pages": [
            {"title": "Home", "final_url": "https://ex.com", "html_ref": "pages/page.html",
             "html_bytes": 1234, "forms": [{"action": "/api/save"}],
             "stylesheets": [{"url": "/a.css"}], "scripts": [{"url": "/a.js"}],
             "assets": [], "storage": {"local": {"theme": "dark"}}}
        ],
        "paths": [
            {"name": "load-data", "entry_url": "https://ex.com", "steps": [
                {"index": 1, "label": "click load", "intent": "fetch data",
                 "action": {"type": "click", "selector": "#load"},
                 "http": [{"method": "GET", "url": "/api/data", "status": 200, "resource_type": "fetch"}]}
            ]}
        ],
    }


def test_render_writes_three_files(tmp_path):
    out = BlueprintGenerator(output_dir=str(tmp_path / "bp")).render(_results())
    assert out["markdown"].exists()
    assert out["json"].exists()
    assert out["backend"].exists()
    backend = json.loads(out["backend"].read_text(encoding="utf-8"))
    assert backend["base_url"] == "https://ex.com/api"


def test_markdown_has_sections(tmp_path):
    out = BlueprintGenerator(output_dir=str(tmp_path / "bp")).render(_results())
    md = out["markdown"].read_text(encoding="utf-8")
    assert "# Replication Blueprint" in md
    assert "Tech stack" in md and "Next.js" in md
    assert "Backend model" in md and "/api/data" in md
    assert "User paths" in md and "load-data" in md
    assert "Frontend" in md and "Home" in md


def test_render_tolerates_empty_results(tmp_path):
    out = BlueprintGenerator(output_dir=str(tmp_path / "bp")).render({})
    assert out["markdown"].exists()
    md = out["markdown"].read_text(encoding="utf-8")
    assert "# Replication Blueprint" in md
