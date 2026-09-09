from typing import Any

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


def test_dashboard_bounds_message_depth() -> None:
    root = Message("Outer")
    root.messages.append(Message("Inner"))

    with pytest.raises(ValueError, match="message depth 1"):
        emit_dashboard([RecoveredSchema("deep.proto", messages=[root])], max_depth=1)


def test_dashboard_bounds_cyclic_message_graph() -> None:
    root = Message("Cycle")
    root.messages.append(root)

    with pytest.raises(ValueError, match="message depth 10"):
        emit_dashboard([RecoveredSchema("cycle.proto", messages=[root])], max_depth=10)


@pytest.mark.parametrize(
    "limits", [{"max_items": 0}, {"max_depth": 0}, {"max_bytes": 0}]
)
def test_dashboard_rejects_nonpositive_limits(limits: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        emit_dashboard([], **limits)
