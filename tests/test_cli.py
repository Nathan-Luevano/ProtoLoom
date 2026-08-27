from protoloom.cli import _nested_lite_messages
from protoloom.model import Confidence, Field, Message, RecoveredSchema
from protoloom.reconcile import reconcile


def _schema(name: str, message: Message, package: str = "") -> RecoveredSchema:
    return RecoveredSchema(name=f"{name}.proto", package=package, messages=[message])


def test_nests_message_under_its_enclosing_class() -> None:
    outer = Message(name="Outer")
    inner = Message(name="Inner")
    items = [_schema("Outer", outer), _schema("Inner", inner)]
    lineage = {
        ("", "Outer.proto"): ("LOuter;", None),
        ("", "Inner.proto"): ("LOuter$Inner;", "LOuter;"),
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
        ("", "AccessMethod.proto"): ("LAccessMethod;", None),
        ("", "AccessMethod_Bridges.proto"): (
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
    lineage: dict[tuple[str, str], tuple[str, str | None]] = {
        ("", "Orphan.proto"): ("LSomewhere$Orphan;", "LSomewhere;")
    }
    top_level = _nested_lite_messages(items, lineage)
    assert top_level == [orphan]
    assert orphan.messages == []


def test_same_simple_name_in_different_packages_does_not_collide() -> None:
    # Two unrelated DEX classes can both recover to a bare file name "Relay",
    # e.g. one package's real Relay and an unrelated package's own Relay.
    real_relay = Message(
        name="Relay",
        fields=[
            Field(
                name="hostname",
                number=1,
                type_name="string",
                confidence=Confidence.HIGH,
            )
        ],
    )
    other_relay = Message(
        name="Relay",
        fields=[
            Field(
                name="weight", number=1, type_name="int32", confidence=Confidence.HIGH
            )
        ],
    )
    schemas = [
        _schema("Relay", real_relay, package="pkg.a"),
        _schema("Relay", other_relay, package="pkg.b"),
    ]
    result = reconcile(schemas)
    assert len(result.schemas) == 2
    by_package = {schema.package: schema for schema in result.schemas}
    assert by_package["pkg.a"].messages[0].fields[0].name == "hostname"
    assert by_package["pkg.b"].messages[0].fields[0].name == "weight"
