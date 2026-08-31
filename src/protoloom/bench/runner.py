import json
from collections.abc import Mapping
from math import isnan
from pathlib import Path
from typing import Any

from protoloom.bench.corpus import CorpusManifest, materialize
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
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"benchmark schema must be an object: {path}")
    messages = tuple(_message(item) for item in _items(raw, "messages"))
    enums = tuple(_enum(item) for item in _items(raw, "enums"))
    round_trip = raw.get("round_trip", {})
    if not isinstance(round_trip, dict):
        raise ValueError("round_trip must be an object")
    passed = int(round_trip.get("passed", 0))
    total = int(round_trip.get("total", 0))
    if passed < 0 or total < 0 or passed > total:
        raise ValueError("round-trip counts are invalid")
    ambiguities = _ambiguities(raw.get("type_fidelity_ambiguities"))
    return BenchmarkSchema(
        messages, bool(raw.get("compiled", True)), passed, total, enums, ambiguities
    )


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
    name = value.get("name")
    parent = value.get("parent")
    return BenchmarkMessage(
        str(name) if name is not None else None,
        fields,
        str(parent) if parent is not None else None,
        enums,
    )


def _field(value: object) -> BenchmarkField:
    if not isinstance(value, dict):
        raise ValueError("field must be an object")
    oneof = value.get("oneof")
    return BenchmarkField(
        int(value["number"]),
        str(value["name"]),
        str(value["proto_type"]),
        int(value["wire_type"]),
        str(value.get("label", "optional")),
        str(oneof) if oneof is not None else None,
    )


def _enum(value: object) -> BenchmarkEnum:
    if not isinstance(value, dict):
        raise ValueError("enum must be an object")
    values = tuple((str(item[0]), int(item[1])) for item in _items(value, "values"))
    return BenchmarkEnum(str(value["name"]), values)


def _items(value: Mapping[str, Any], key: str) -> list[Any]:
    items = value.get(key, [])
    if not isinstance(items, list):
        raise ValueError(f"{key} must be an array")
    return items
