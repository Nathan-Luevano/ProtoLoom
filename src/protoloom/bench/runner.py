import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from protoloom.bench.corpus import CorpusManifest, materialize
from protoloom.bench.metrics import (
    METRIC_NAMES,
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
    return BenchmarkSchema(
        messages, bool(raw.get("compiled", True)), passed, total, enums
    )


def render_report(report: AggregateReport, per_target: bool = False) -> str:
    lines = ["metric                     macro      micro      lead"]
    for metric in METRIC_NAMES:
        label, value = report.least_flattering(metric)
        lines.append(
            f"{metric:25} {report.macro[metric]:9.2%} "
            f"{report.micro[metric]:9.2%} {label} {value:.2%}"
        )
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
    if per_target:
        lines.extend(("", "per target"))
        for target in report.targets:
            lines.append(_target_line(target))
    return "\n".join(lines)


def _target_line(report: MetricReport) -> str:
    values = " ".join(f"{metric}={report.value(metric):.2%}" for metric in METRIC_NAMES)
    return (
        f"{report.target}: {values} "
        f"type_fidelity_ceiling={report.type_fidelity_ceiling.value:.2%}"
    )


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
