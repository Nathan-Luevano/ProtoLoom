from collections.abc import Mapping
from dataclasses import dataclass
from math import nan
from statistics import fmean
from types import MappingProxyType
from typing import Final

METRIC_NAMES: Final = (
    "field_recall",
    "field_precision",
    "wire_type_accuracy",
    "type_fidelity",
    "name_recovery_rate",
    "label_accuracy",
    "structural_fidelity",
    "enum_recovery",
    "compile_rate",
    "round_trip_rate",
)

TYPE_FIDELITY_AMBIGUITIES: Final = (
    frozenset({"int32", "sint32", "uint32"}),
    frozenset({"int64", "sint64", "uint64"}),
)


@dataclass(frozen=True, slots=True)
class BenchmarkField:
    number: int
    name: str
    proto_type: str
    wire_type: int
    label: str = "optional"
    oneof: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkEnum:
    name: str
    values: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class BenchmarkMessage:
    name: str | None
    fields: tuple[BenchmarkField, ...]
    parent: str | None = None
    enums: tuple[BenchmarkEnum, ...] = ()


@dataclass(frozen=True, slots=True)
class BenchmarkSchema:
    messages: tuple[BenchmarkMessage, ...]
    compiled: bool = True
    round_trip_passed: int = 0
    round_trip_total: int = 0
    enums: tuple[BenchmarkEnum, ...] = ()
    type_fidelity_ambiguities: tuple[frozenset[str], ...] = TYPE_FIDELITY_AMBIGUITIES


@dataclass(frozen=True, slots=True)
class Score:
    numerator: int
    denominator: int

    @property
    def value(self) -> float:
        return self.numerator / self.denominator if self.denominator else 1.0


@dataclass(frozen=True, slots=True)
class MetricReport:
    target: str
    scores: Mapping[str, Score]
    type_fidelity_ceiling: Score

    def value(self, metric: str) -> float:
        return self.scores[metric].value


@dataclass(frozen=True, slots=True)
class AggregateReport:
    macro: Mapping[str, float]
    micro: Mapping[str, float]
    targets: tuple[MetricReport, ...]
    type_fidelity_ceiling_macro: float
    type_fidelity_ceiling_micro: float

    def least_flattering(self, metric: str) -> tuple[str, float]:
        macro = self.macro[metric]
        micro = self.micro[metric]
        return ("macro", macro) if macro <= micro else ("micro", micro)


def score_target(
    target: str, truth: BenchmarkSchema, recovered: BenchmarkSchema
) -> MetricReport:
    matches = _match_messages(truth.messages, recovered.messages)
    truth_fields = sum(len(message.fields) for message in truth.messages)
    recovered_fields = sum(len(message.fields) for message in recovered.messages)
    matched_fields: list[tuple[BenchmarkField, BenchmarkField]] = []
    for expected, actual in matches:
        actual_by_number = {item.number: item for item in actual.fields}
        matched_fields.extend(
            (field, actual_by_number[field.number])
            for field in expected.fields
            if field.number in actual_by_number
        )

    structures_total, structures_correct = _score_structures(matches, truth.messages)
    enum_total, enum_correct = _score_enums(
        matches, truth.messages, truth.enums, recovered.enums
    )
    gate = int(recovered.compiled)
    matched_count = len(matched_fields)
    scores = {
        "field_recall": Score(gate * matched_count, truth_fields),
        "field_precision": Score(gate * matched_count, recovered_fields),
        "wire_type_accuracy": Score(
            gate
            * sum(left.wire_type == right.wire_type for left, right in matched_fields),
            matched_count,
        ),
        "type_fidelity": Score(
            gate
            * sum(
                left.proto_type == right.proto_type for left, right in matched_fields
            ),
            matched_count,
        ),
        "name_recovery_rate": Score(
            gate * sum(left.name == right.name for left, right in matched_fields),
            matched_count,
        ),
        "label_accuracy": Score(
            gate * sum(left.label == right.label for left, right in matched_fields),
            matched_count,
        ),
        "structural_fidelity": Score(gate * structures_correct, structures_total),
        "enum_recovery": Score(gate * enum_correct, enum_total),
        "compile_rate": Score(gate, 1),
        "round_trip_rate": Score(
            gate * recovered.round_trip_passed, recovered.round_trip_total
        ),
    }
    if not recovered.compiled:
        scores = {
            metric: Score(0, max(score.denominator, 1))
            for metric, score in scores.items()
        }
    ceiling = type_fidelity_ceiling(truth, truth.type_fidelity_ambiguities)
    return MetricReport(target, MappingProxyType(scores), ceiling)


def aggregate_reports(reports: list[MetricReport]) -> AggregateReport:
    if not reports:
        raise ValueError("at least one target report is required")
    macro = {}
    for metric in METRIC_NAMES:
        measured = [
            report.value(metric)
            for report in reports
            if report.scores[metric].denominator > 0
        ]
        macro[metric] = fmean(measured) if measured else nan
    micro = {}
    for metric in METRIC_NAMES:
        numerator = sum(report.scores[metric].numerator for report in reports)
        denominator = sum(report.scores[metric].denominator for report in reports)
        micro[metric] = nan if denominator == 0 else numerator / denominator
    ceiling_numerator = sum(
        report.type_fidelity_ceiling.numerator for report in reports
    )
    ceiling_denominator = sum(
        report.type_fidelity_ceiling.denominator for report in reports
    )
    return AggregateReport(
        MappingProxyType(macro),
        MappingProxyType(micro),
        tuple(reports),
        fmean(report.type_fidelity_ceiling.value for report in reports),
        Score(ceiling_numerator, ceiling_denominator).value,
    )


def type_fidelity_ceiling(
    truth: BenchmarkSchema, ambiguous_groups: tuple[frozenset[str], ...]
) -> Score:
    fields = [field for message in truth.messages for field in message.fields]
    ambiguous_types = frozenset().union(*ambiguous_groups)
    identifiable = sum(field.proto_type not in ambiguous_types for field in fields)
    recoverable = identifiable
    for group in ambiguous_groups:
        counts = [sum(field.proto_type == kind for field in fields) for kind in group]
        recoverable += max(counts, default=0)
    return Score(recoverable, len(fields))


def _match_messages(
    truth: tuple[BenchmarkMessage, ...], recovered: tuple[BenchmarkMessage, ...]
) -> list[tuple[BenchmarkMessage, BenchmarkMessage]]:
    matches: list[tuple[BenchmarkMessage, BenchmarkMessage]] = []
    unused = list(recovered)
    for expected in truth:
        named = [
            item
            for item in unused
            if expected.name is not None and item.name == expected.name
        ]
        candidates = named or [
            item for item in unused if _signature(item) == _signature(expected)
        ]
        if len(candidates) == 1:
            actual = candidates[0]
            matches.append((expected, actual))
            unused.remove(actual)
    return matches


def _signature(message: BenchmarkMessage) -> tuple[tuple[int, int], ...]:
    return tuple(sorted((field.number, field.wire_type) for field in message.fields))


def _score_structures(
    matches: list[tuple[BenchmarkMessage, BenchmarkMessage]],
    truth: tuple[BenchmarkMessage, ...],
) -> tuple[int, int]:
    actual_by_truth_name = {
        expected.name: actual
        for expected, actual in matches
        if expected.name is not None
    }
    nesting = [message for message in truth if message.parent is not None]
    nesting_correct = sum(
        message.name in actual_by_truth_name
        and actual_by_truth_name[message.name].parent == message.parent
        for message in nesting
    )
    oneofs_total = sum(len(_oneof_groups(message)) for message in truth)
    oneofs_correct = 0
    for expected, actual in matches:
        expected_groups = _oneof_groups(expected)
        actual_groups = _oneof_groups(actual)
        oneofs_correct += sum(group in actual_groups for group in expected_groups)
    return len(nesting) + oneofs_total, nesting_correct + oneofs_correct


def _oneof_groups(message: BenchmarkMessage) -> set[frozenset[int]]:
    names = {field.oneof for field in message.fields if field.oneof is not None}
    return {
        frozenset(field.number for field in message.fields if field.oneof == name)
        for name in names
    }


def _score_enums(
    matches: list[tuple[BenchmarkMessage, BenchmarkMessage]],
    truth: tuple[BenchmarkMessage, ...],
    truth_enums: tuple[BenchmarkEnum, ...],
    recovered_enums: tuple[BenchmarkEnum, ...],
) -> tuple[int, int]:
    expected_total = sum(
        len(enum.values) for message in truth for enum in message.enums
    )
    expected_total += sum(len(enum.values) for enum in truth_enums)
    recovered_top = {enum.name: set(enum.values) for enum in recovered_enums}
    correct = sum(
        len(set(enum.values) & recovered_top.get(enum.name, set()))
        for enum in truth_enums
    )
    for expected, actual in matches:
        actual_enums = {enum.name: set(enum.values) for enum in actual.enums}
        for enum in expected.enums:
            correct += len(set(enum.values) & actual_enums.get(enum.name, set()))
    return expected_total, correct
