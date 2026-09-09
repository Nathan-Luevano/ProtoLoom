from collections.abc import Mapping
from math import isnan
from pathlib import Path
from typing import Any

from protoloom.bench.corpus import CorpusManifest, materialize
from protoloom.bench.jsonio import read_json
from protoloom.bench.metrics import (
    METRIC_NAMES,
    TYPE_FIDELITY_AMBIGUITIES,
    AggregateReport,
    BenchmarkEnum,
    BenchmarkField,
    BenchmarkMessage,
    BenchmarkSchema,
    MetricReport,
    aggregate_reports,
    score_target,
)

MAX_BENCH_SCHEMA_ITEMS = 100_000


def run_corpus(manifest: CorpusManifest, workdir: Path) -> AggregateReport:
    artifacts = materialize(manifest, workdir)
    reports = [
        score_target(
            target.name,
            load_schema(artifacts[f"{target.name}/{target.truth.name}"]),
            load_schema(artifacts[f"{target.name}/{target.recovered.name}"]),
        )
        for target in manifest.targets
    ]
    return aggregate_reports(reports)


def load_schema(path: Path) -> BenchmarkSchema:
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise ValueError(f"benchmark schema must be an object: {path}")
    _validate_schema_budget(raw)
    messages = tuple(_message(item) for item in _items(raw, "messages"))
    enums = tuple(_enum(item) for item in _items(raw, "enums"))
    _ensure_unique(
        [message.name for message in messages if message.name is not None],
        "message names",
    )
    _ensure_unique([enum.name for enum in enums], "enum names")
    round_trip = raw.get("round_trip", {})
    if not isinstance(round_trip, dict):
        raise ValueError("round_trip must be an object")
    passed = _integer(round_trip.get("passed", 0), "round_trip.passed")
    total = _integer(round_trip.get("total", 0), "round_trip.total")
    if passed < 0 or total < 0 or passed > total:
        raise ValueError("round-trip counts are invalid")
    ambiguities = _ambiguities(raw.get("type_fidelity_ambiguities"))
    return BenchmarkSchema(
        messages,
        _boolean(raw.get("compiled", True), "compiled"),
        passed,
        total,
        enums,
        ambiguities,
    )


def _validate_schema_budget(raw: Mapping[str, Any]) -> None:
    messages = _items(raw, "messages")
    enums = _items(raw, "enums")
    count = len(messages) + len(enums)
    if count > MAX_BENCH_SCHEMA_ITEMS:
        raise ValueError(f"benchmark schema exceeds {MAX_BENCH_SCHEMA_ITEMS} items")
    for message in messages:
        if not isinstance(message, dict):
            continue
        fields = message.get("fields", [])
        nested_enums = message.get("enums", [])
        if isinstance(fields, list):
            count += len(fields)
        if isinstance(nested_enums, list):
            count += len(nested_enums)
            count += sum(_enum_value_count(item) for item in nested_enums)
        if count > MAX_BENCH_SCHEMA_ITEMS:
            raise ValueError(f"benchmark schema exceeds {MAX_BENCH_SCHEMA_ITEMS} items")
    count += sum(_enum_value_count(item) for item in enums)
    if count > MAX_BENCH_SCHEMA_ITEMS:
        raise ValueError(f"benchmark schema exceeds {MAX_BENCH_SCHEMA_ITEMS} items")


def _enum_value_count(value: object) -> int:
    values = value.get("values", []) if isinstance(value, dict) else []
    return len(values) if isinstance(values, list) else 0


def _ambiguities(value: object) -> tuple[frozenset[str], ...]:
    if value is None:
        return TYPE_FIDELITY_AMBIGUITIES
    if not isinstance(value, list):
        raise ValueError("type_fidelity_ambiguities must be an array")
    groups = []
    seen: set[str] = set()
    for group in value:
        if (
            not isinstance(group, list)
            or not group
            or not all(isinstance(item, str) for item in group)
        ):
            raise ValueError("type fidelity ambiguity groups must be string arrays")
        members = frozenset(group)
        if len(members) != len(group) or seen.intersection(members):
            raise ValueError("type fidelity ambiguity groups must not overlap")
        groups.append(members)
        seen.update(members)
    return tuple(groups)


def render_report(report: AggregateReport, per_target: bool = False) -> str:
    lines = ["metric                     macro      micro      lead"]
    for metric in METRIC_NAMES:
        if isnan(report.macro[metric]) and isnan(report.micro[metric]):
            lines.append(f"{metric:25} {'n/a':>9} {'n/a':>9} {'n/a':>12}")
            continue
        label, value = report.least_flattering(metric)
        lines.append(
            f"{metric:25} {report.macro[metric]:9.2%} "
            f"{report.micro[metric]:9.2%} {label} {value:.2%}"
        )
    if isnan(report.type_fidelity_ceiling_macro):
        lines.append(f"{'type_fidelity_ceiling':25} {'n/a':>9} {'n/a':>9} {'n/a':>12}")
        return _render_targets(lines, report) if per_target else "\n".join(lines)
    ceiling_lead = min(
        report.type_fidelity_ceiling_macro, report.type_fidelity_ceiling_micro
    )
    ceiling_label = (
        "macro"
        if report.type_fidelity_ceiling_macro <= report.type_fidelity_ceiling_micro
        else "micro"
    )
    lines.append(
        f"{'type_fidelity_ceiling':25} "
        f"{report.type_fidelity_ceiling_macro:9.2%} "
        f"{report.type_fidelity_ceiling_micro:9.2%} "
        f"{ceiling_label} {ceiling_lead:.2%}"
    )
    return _render_targets(lines, report) if per_target else "\n".join(lines)


def _render_targets(lines: list[str], report: AggregateReport) -> str:
    lines.extend(("", "per target"))
    lines.extend(_target_line(target) for target in report.targets)
    return "\n".join(lines)


def _target_line(report: MetricReport) -> str:
    values = " ".join(_target_metric(report, metric) for metric in METRIC_NAMES)
    ceiling = report.type_fidelity_ceiling
    ceiling_value = "n/a" if ceiling.denominator == 0 else f"{ceiling.value:.2%}"
    return f"{report.target}: {values} type_fidelity_ceiling={ceiling_value}"


def _target_metric(report: MetricReport, metric: str) -> str:
    score = report.scores[metric]
    if score.denominator == 0:
        return f"{metric}=n/a"
    return f"{metric}={score.value:.2%}"


def _message(value: object) -> BenchmarkMessage:
    if not isinstance(value, dict):
        raise ValueError("message must be an object")
    fields = tuple(_field(item) for item in _items(value, "fields"))
    enums = tuple(_enum(item) for item in value.get("enums", []))
    _ensure_unique([field.number for field in fields], "field numbers")
    _ensure_unique([field.name for field in fields], "field names")
    _ensure_unique([enum.name for enum in enums], "message enum names")
    return BenchmarkMessage(
        _optional_string(value.get("name"), "message name"),
        fields,
        _optional_string(value.get("parent"), "message parent"),
        enums,
    )


def _field(value: object) -> BenchmarkField:
    if not isinstance(value, dict):
        raise ValueError("field must be an object")
    number = _integer(value["number"], "field number")
    wire_type = _integer(value["wire_type"], "field wire_type")
    label = _string(value.get("label", "optional"), "field label")
    if number <= 0 or number > 536_870_911 or 19_000 <= number <= 19_999:
        raise ValueError("field number is invalid")
    if wire_type not in range(6):
        raise ValueError("field wire_type is invalid")
    if label not in {"optional", "required", "repeated"}:
        raise ValueError("field label is invalid")
    return BenchmarkField(
        number,
        _string(value["name"], "field name"),
        _string(value["proto_type"], "field proto_type"),
        wire_type,
        label,
        _optional_string(value.get("oneof"), "field oneof"),
    )


def _enum(value: object) -> BenchmarkEnum:
    if not isinstance(value, dict):
        raise ValueError("enum must be an object")
    values = tuple(_enum_value(item) for item in _items(value, "values"))
    _ensure_unique([name for name, _ in values], "enum value names")
    return BenchmarkEnum(_string(value["name"], "enum name"), values)


def _enum_value(value: object) -> tuple[str, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("enum value must be a name and integer pair")
    return (
        _string(value[0], "enum value name"),
        _integer(value[1], "enum value number"),
    )


def _items(value: Mapping[str, Any], key: str) -> list[Any]:
    items = value.get(key, [])
    if not isinstance(items, list):
        raise ValueError(f"{key} must be an array")
    return items


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _optional_string(value: object, label: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _ensure_unique(values: list[object], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must be unique")
