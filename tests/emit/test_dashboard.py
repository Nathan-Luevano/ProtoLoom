import pytest

from protoloom.emit.dashboard import emit_dashboard
from protoloom.model import Confidence, Field, Message, RecoveredSchema
from protoloom.reconcile import Conflict


def test_dashboard_is_self_contained_and_escapes_recovered_data() -> None:
    schema = RecoveredSchema(
        "unsafe<script>.proto",
        messages=[
            Message(
                "Record",
                [Field("name<bad>", 1, "string", Confidence.HIGH)],
            )
        ],
    )
    conflict = Conflict("Record.1", "name", "name<bad>", "x&y", None, None)

    page = emit_dashboard([schema], [conflict])

    assert page.startswith("<!doctype html>")
    assert "unsafe&lt;script&gt;.proto" in page
    assert "name&lt;bad&gt;" in page
    assert "x&amp;y" in page
    assert "https://" not in page
    assert "<script" not in page
    assert "Recovery dashboard" in page


def test_empty_dashboard_has_clear_empty_states() -> None:
    page = emit_dashboard([])
    assert "No fields recovered." in page
    assert "No conflicts recorded." in page


def test_dashboard_handles_deep_message_trees() -> None:
    root = Message("Level0")
    current = root
    for index in range(1, 1100):
        child = Message(f"Level{index}")
        current.messages.append(child)
        current = child
    current.fields.append(Field("value", 1, "string", Confidence.HIGH))
    page = emit_dashboard([RecoveredSchema("deep.proto", messages=[root])])
    assert "Level1099" in page


def test_dashboard_bounds_conflict_iterables() -> None:
    conflicts = ({"path": str(index), "attribute": "name"} for index in range(3))
    with pytest.raises(ValueError, match="exceeds 2 conflicts"):
        emit_dashboard([], conflicts, max_items=2)


def test_dashboard_bounds_encoded_output() -> None:
    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        emit_dashboard([], max_bytes=8)


@pytest.mark.parametrize(("max_items", "max_bytes"), [(0, 1), (1, 0)])
def test_dashboard_rejects_nonpositive_limits(max_items: int, max_bytes: int) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        emit_dashboard([], max_items=max_items, max_bytes=max_bytes)
