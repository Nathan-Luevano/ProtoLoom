import pytest

from protoloom.emit.report import emit_report
from protoloom.model import (
    Confidence,
    Field,
    Message,
    RecoveredSchema,
    Service,
    ServiceMethod,
)


def test_report_counts_nested_messages_confidence_and_bailouts() -> None:
    schema = RecoveredSchema(
        name="nested.proto",
        messages=[
            Message(
                name="Outer",
                fields=[Field("id", 1, "int32", Confidence.CERTAIN)],
                messages=[
                    Message(
                        name="Inner",
                        fields=[Field("note", 1, "string", Confidence.HIGH)],
                    )
                ],
            )
        ],
    )

    report = emit_report([schema], ["classes.dex: incomplete info string"])

    assert "Recovered 1 files and 2 messages." in report
    assert "- certain: 1" in report
    assert "- high: 1" in report
    assert "- medium: 0" in report
    assert "- speculative: 0" in report
    assert "Bail-outs: 1\n- classes.dex: incomplete info string\n" in report


def test_report_counts_services_and_rpcs() -> None:
    schema = RecoveredSchema(
        name="svc.proto",
        services=[
            Service(
                "Foo",
                [
                    ServiceMethod("A", "Req", "Res", Confidence.HIGH),
                    ServiceMethod("B", "Req", "Res", Confidence.HIGH),
                ],
            )
        ],
    )

    report = emit_report([schema], [])

    assert "Recovered 1 services and 2 RPCs." in report


def test_report_omits_service_line_when_none_recovered() -> None:
    report = emit_report([RecoveredSchema("plain.proto")], [])
    assert "services" not in report


def test_report_flattens_multiline_bailouts() -> None:
    report = emit_report([], ["first line\n- injected bullet\r\nlast line"])
    assert "- first line - injected bullet last line\n" in report


def test_report_handles_deep_message_trees() -> None:
    root = Message("Level0")
    current = root
    for index in range(1, 1100):
        child = Message(f"Level{index}")
        current.messages.append(child)
        current = child
    report = emit_report([RecoveredSchema("deep.proto", messages=[root])], [])
    assert "Recovered 1 files and 1100 messages." in report


@pytest.mark.parametrize(
    ("schemas", "bailouts", "message"),
    [
        ([RecoveredSchema("a"), RecoveredSchema("b")], [], "schemas"),
        ([], ["a", "b"], "bailouts"),
    ],
)
def test_report_bounds_top_level_collections(
    schemas: list[RecoveredSchema], bailouts: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        emit_report(schemas, bailouts, max_items=1)


def test_report_bounds_encoded_output() -> None:
    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        emit_report([], [], max_bytes=8)


def test_report_rejects_large_bailout_before_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def render(value: str) -> str:
        nonlocal calls
        calls += 1
        return value

    monkeypatch.setattr("protoloom.emit.report._single_line", render)

    with pytest.raises(ValueError, match="exceeds 8 bytes"):
        emit_report([], ["x" * 9], max_bytes=8)

    assert calls == 0


def test_report_bounds_aggregate_bailout_bytes() -> None:
    with pytest.raises(ValueError, match="exceeds 10 bytes"):
        emit_report([], ["1234", "5678"], max_bytes=10)


def test_report_bounds_bailout_after_rendering_prefix_overhead() -> None:
    # The raw value alone fits the remaining budget, but the "- " prefix
    # and trailing newline this renders with push it over.
    with pytest.raises(ValueError, match="exceeds 6 bytes"):
        emit_report([], ["12345"], max_bytes=6)


def test_report_raises_when_a_single_schema_exceeds_the_message_budget() -> None:
    schema = RecoveredSchema("a.proto", messages=[Message("A"), Message("B")])
    with pytest.raises(ValueError, match="exceeds 1 messages"):
        emit_report([schema], [], max_items=1)


def test_report_bounds_field_count() -> None:
    schema = RecoveredSchema(
        "a.proto",
        messages=[
            Message(
                "A",
                fields=[
                    Field("x", 1, "int32", Confidence.HIGH),
                    Field("y", 2, "int32", Confidence.HIGH),
                ],
            )
        ],
    )
    with pytest.raises(ValueError, match="exceeds 1 fields"):
        emit_report([schema], [], max_items=1)


def test_report_bounds_service_or_method_count() -> None:
    schema = RecoveredSchema(
        "a.proto",
        services=[
            Service(
                "Foo",
                [
                    ServiceMethod("A", "Req", "Res", Confidence.HIGH),
                    ServiceMethod("B", "Req", "Res", Confidence.HIGH),
                ],
            )
        ],
    )
    with pytest.raises(ValueError, match="exceeds 1 services"):
        emit_report([schema], [], max_items=1)


def test_report_bounds_message_depth() -> None:
    root = Message("Outer")
    root.messages.append(Message("Inner"))

    with pytest.raises(ValueError, match="message depth 1"):
        emit_report([RecoveredSchema("deep.proto", messages=[root])], [], max_depth=1)


def test_report_bounds_cyclic_message_graph() -> None:
    root = Message("Cycle")
    root.messages.append(root)

    with pytest.raises(ValueError, match="message depth 10"):
        emit_report([RecoveredSchema("cycle.proto", messages=[root])], [], max_depth=10)


@pytest.mark.parametrize(
    "limits", [{"max_items": 0}, {"max_depth": 0}, {"max_bytes": 0}]
)
def test_report_rejects_nonpositive_limits(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="limits must be positive"):
        emit_report([], [], **limits)
