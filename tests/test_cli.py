from protoloom.cli import _nested_lite_messages
from protoloom.model import Confidence, Field, Message, RecoveredSchema


def _schema(name: str, message: Message) -> RecoveredSchema:
    return RecoveredSchema(name=f"{name}.proto", messages=[message])


def test_nests_message_under_its_enclosing_class() -> None:
    outer = Message(name="Outer")
    inner = Message(name="Inner")
    items = [_schema("Outer", outer), _schema("Inner", inner)]
    lineage = {
        "Outer.proto": ("LOuter;", None),
        "Inner.proto": ("LOuter$Inner;", "LOuter;"),
    }
    top_level = _nested_lite_messages(items, lineage)
    assert top_level == [outer]
    assert outer.messages == [inner]


def test_strips_redundant_parent_prefix_and_rewrites_field_references() -> None:
    outer = Message(
        name="AccessMethod",
        fields=[
            Field(
                name="bridges",
                number=2,
                type_name="AccessMethod_Bridges",
                confidence=Confidence.HIGH,
            )
        ],
    )
    inner = Message(name="AccessMethod_Bridges")
    items = [_schema("AccessMethod", outer), _schema("AccessMethod_Bridges", inner)]
    lineage = {
        "AccessMethod.proto": ("LAccessMethod;", None),
        "AccessMethod_Bridges.proto": (
            "LAccessMethod$AccessMethod_Bridges;",
            "LAccessMethod;",
        ),
    }
    top_level = _nested_lite_messages(items, lineage)
    assert top_level == [outer]
    assert [child.name for child in outer.messages] == ["Bridges"]
    assert outer.fields[0].type_name == "Bridges"


def test_leaves_message_top_level_without_lineage() -> None:
    solo = Message(name="Solo")
    items = [_schema("Solo", solo)]
    top_level = _nested_lite_messages(items, {})
    assert top_level == [solo]


def test_ignores_enclosing_descriptor_never_recovered_in_this_run() -> None:
    orphan = Message(name="Orphan")
    items = [_schema("Orphan", orphan)]
    lineage: dict[str, tuple[str, str | None]] = {
        "Orphan.proto": ("LSomewhere$Orphan;", "LSomewhere;")
    }
    top_level = _nested_lite_messages(items, lineage)
    assert top_level == [orphan]
    assert orphan.messages == []
