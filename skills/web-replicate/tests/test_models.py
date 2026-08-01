"""Models serialize enum-safely and recurse into nested dataclasses/lists."""

from engine.models import (
    Action,
    ActionType,
    AssetKind,
    AssetRef,
    Cookie,
    NetworkRecord,
    PageCapture,
    StorageSnapshot,
)


def test_action_to_dict_converts_enum():
    d = Action(type=ActionType.CLICK, selector="#go", intent="open").to_dict()
    assert d["type"] == "click"  # enum -> value, not the enum object
    assert d["selector"] == "#go"
    assert d["intent"] == "open"


def test_asset_ref_enum_value():
    d = AssetRef(kind=AssetKind.STYLESHEET, url="/a.css").to_dict()
    assert d["kind"] == "stylesheet"


def test_page_capture_recurses_nested():
    cap = PageCapture(
        url="u",
        final_url="u",
        title="t",
        status=200,
        ready_state="complete",
        assets=[AssetRef(kind=AssetKind.IMAGE, url="/x.png")],
        network=[NetworkRecord(method="GET", url="/api", resource_type="fetch", status=200)],
        storage=StorageSnapshot(
            local={"k": "v"}, cookies=[Cookie(name="sid", value="x")]
        ),
    )
    d = cap.to_dict()
    assert d["assets"][0]["kind"] == "image"
    assert d["network"][0]["method"] == "GET"
    assert d["storage"]["local"] == {"k": "v"}
    assert d["storage"]["cookies"][0]["name"] == "sid"


def test_page_capture_schema_is_frozen():
    """Seam guard: the top-level key set of PageCapture (the engine<->agent capture
    contract that the blueprint is rebuilt from) is frozen, so a field add/rename/
    remove is caught. web-replicate has no formal spec doc; this test IS the
    contract check. Change deliberately? Update this set (and the blueprint) too."""
    cap = PageCapture(
        url="u", final_url="u", title="t", status=200, ready_state="complete"
    )
    assert set(cap.to_dict().keys()) == {
        "url",
        "final_url",
        "title",
        "status",
        "ready_state",
        "lang",
        "meta",
        "html_ref",
        "html_bytes",
        "dom_outline",
        "content",
        "interactive",
        "forms",
        "fields",
        "links",
        "stylesheets",
        "scripts",
        "assets",
        "inline_styles",
        "design_tokens",
        "storage",
        "console",
        "network",
        "tech",
        "screenshot_ref",
        "capture_error",
    }
