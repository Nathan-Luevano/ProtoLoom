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
