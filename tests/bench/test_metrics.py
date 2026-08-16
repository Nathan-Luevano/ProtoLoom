import math

import pytest

from protoloom.bench.metrics import (
    BenchmarkEnum,
    BenchmarkField,
    BenchmarkMessage,
    BenchmarkSchema,
    aggregate_reports,
    score_target,
    type_fidelity_ceiling,
)


def field(
    number: int,
    name: str,
    proto_type: str = "string",
    wire_type: int = 2,
    label: str = "optional",
    oneof: str | None = None,
) -> BenchmarkField:
    return BenchmarkField(number, name, proto_type, wire_type, label, oneof)


def test_all_metrics_have_known_values() -> None:
    truth = BenchmarkSchema(
        (
            BenchmarkMessage(
                "pkg.Item",
                (
                    field(1, "id", "int32", 0),
                    field(2, "name"),
                    field(3, "email", oneof="contact"),
                    field(4, "phone", oneof="contact"),
                ),
                enums=(BenchmarkEnum("State", (("UNKNOWN", 0), ("READY", 1))),),
            ),
            BenchmarkMessage("pkg.Item.Child", (field(1, "value"),), "pkg.Item"),
        )
    )
    recovered = BenchmarkSchema(
        (
            BenchmarkMessage(
                "pkg.Item",
                (
                    field(1, "field_1", "uint32", 0),
                    field(2, "name"),
                    field(3, "email", "bytes", oneof="choice"),
                    field(4, "phone", oneof="choice"),
                    field(9, "extra", "fixed32", 5),
                ),
                enums=(BenchmarkEnum("State", (("UNKNOWN", 0),)),),
            ),
            BenchmarkMessage("pkg.Item.Child", (field(1, "value"),), "pkg.Item"),
        ),
        round_trip_passed=1,
        round_trip_total=2,
    )
    report = score_target("known", truth, recovered)

    assert report.value("field_recall") == 1
    assert report.value("field_precision") == pytest.approx(5 / 6)
    assert report.value("wire_type_accuracy") == 1
    assert report.value("type_fidelity") == pytest.approx(3 / 5)
    assert report.value("name_recovery_rate") == pytest.approx(4 / 5)
    assert report.value("label_accuracy") == 1
    assert report.value("structural_fidelity") == 1
    assert report.value("enum_recovery") == 0.5
    assert report.value("compile_rate") == 1
    assert report.value("round_trip_rate") == 0.5


def test_non_compiling_target_scores_zero() -> None:
    truth = BenchmarkSchema((BenchmarkMessage("pkg.Item", (field(1, "id"),)),))
    recovered = BenchmarkSchema(
        truth.messages, compiled=False, round_trip_passed=1, round_trip_total=1
    )
    report = score_target("broken", truth, recovered)
    assert all(score.value == 0 for score in report.scores.values())


def test_messages_fall_back_to_unique_structural_signature() -> None:
    truth = BenchmarkSchema((BenchmarkMessage(None, (field(4, "value"),)),))
    recovered = BenchmarkSchema((BenchmarkMessage("a", (field(4, "renamed"),)),))
    assert score_target("stripped", truth, recovered).value("field_recall") == 1


def test_ambiguous_structural_signature_is_not_guessed() -> None:
    truth = BenchmarkSchema((BenchmarkMessage(None, (field(1, "value"),)),))
    recovered = BenchmarkSchema(
        (
            BenchmarkMessage("a", (field(1, "one"),)),
            BenchmarkMessage("b", (field(1, "two"),)),
        )
    )
    assert score_target("ambiguous", truth, recovered).value("field_recall") == 0


def test_macro_and_micro_are_both_reported() -> None:
    large_truth = BenchmarkSchema(
        (BenchmarkMessage("large", tuple(field(i, f"f{i}") for i in range(1, 10))),)
    )
    small_truth = BenchmarkSchema((BenchmarkMessage("small", (field(1, "f1"),)),))
    reports = [
        score_target("large", large_truth, large_truth),
        score_target("small", small_truth, BenchmarkSchema(())),
    ]
    aggregate = aggregate_reports(reports)
    assert aggregate.macro["field_recall"] == 0.5
    assert aggregate.micro["field_recall"] == 0.9
    assert aggregate.least_flattering("field_recall") == ("macro", 0.5)
    assert aggregate.type_fidelity_ceiling_macro == 1
    assert aggregate.type_fidelity_ceiling_micro == 1


def test_standard_type_ceiling_is_published_by_target() -> None:
    truth = BenchmarkSchema(
        (
            BenchmarkMessage(
                "numbers",
                (
                    field(1, "a", "int32", 0),
                    field(2, "b", "sint32", 0),
                    field(3, "c", "uint32", 0),
                    field(4, "d", "int32", 0),
                    field(5, "name"),
                ),
            ),
        )
    )
    report = score_target("numbers", truth, truth)
    assert report.type_fidelity_ceiling.value == pytest.approx(3 / 5)


def test_exact_descriptor_evidence_has_an_exact_type_ceiling() -> None:
    truth = BenchmarkSchema(
        (BenchmarkMessage("numbers", (field(1, "value", "sint32", 0),)),),
        type_fidelity_ambiguities=(),
    )
    report = score_target("descriptor", truth, truth)

    assert report.value("type_fidelity") == 1
    assert report.type_fidelity_ceiling.value == 1
    assert report.value("type_fidelity") <= report.type_fidelity_ceiling.value


def test_type_ceiling_uses_best_possible_ambiguous_choice() -> None:
    truth = BenchmarkSchema(
        (
            BenchmarkMessage(
                "numbers",
                (
                    field(1, "a", "int32", 0),
                    field(2, "b", "int32", 0),
                    field(3, "c", "sint32", 0),
                    field(4, "name"),
                ),
            ),
        )
    )
    ceiling = type_fidelity_ceiling(truth, (frozenset({"int32", "sint32", "uint32"}),))
    assert ceiling.value == 0.75


def test_top_level_enums_are_scored() -> None:
    truth = BenchmarkSchema(
        (), enums=(BenchmarkEnum("State", (("UNKNOWN", 0), ("READY", 1))),)
    )
    recovered = BenchmarkSchema((), enums=(BenchmarkEnum("State", (("UNKNOWN", 0),)),))
    assert score_target("enums", truth, recovered).value("enum_recovery") == 0.5


def test_unmeasured_round_trips_do_not_inflate_aggregate() -> None:
    schema = BenchmarkSchema((BenchmarkMessage("Empty", ()),))
    report = aggregate_reports([score_target("empty", schema, schema)])

    assert math.isnan(report.macro["round_trip_rate"])
    assert math.isnan(report.micro["round_trip_rate"])
