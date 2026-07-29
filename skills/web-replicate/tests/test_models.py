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
