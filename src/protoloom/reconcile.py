from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TypeVar

from protoloom.model import (
    Confidence,
    EnumType,
    Evidence,
    Field,
    Message,
    RecoveredSchema,
)


@dataclass(frozen=True, slots=True)
class Conflict:
    path: str
    attribute: str
    kept: str
    rejected: str
    kept_confidence: Confidence | None
    rejected_confidence: Confidence | None


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    schemas: list[RecoveredSchema]
    conflicts: list[Conflict]


_RANK = {
    Confidence.SPECULATIVE: 0,
    Confidence.MEDIUM: 1,
    Confidence.HIGH: 2,
    Confidence.CERTAIN: 3,
}
T = TypeVar("T")


def reconcile(schemas: list[RecoveredSchema]) -> ReconciliationResult:
    merged: dict[str, RecoveredSchema] = {}
    conflicts: list[Conflict] = []
    for source in schemas:
        key = source.name
        if key not in merged:
            merged[key] = deepcopy(source)
            continue
        _merge_schema(merged[key], source, conflicts)
    return ReconciliationResult(list(merged.values()), conflicts)


def _merge_schema(
    target: RecoveredSchema,
    source: RecoveredSchema,
    conflicts: list[Conflict],
) -> None:
    path = target.name
    if target.package != source.package:
        _record(conflicts, path, "package", target.package, source.package, None, None)
    if target.syntax != source.syntax:
        _record(conflicts, path, "syntax", target.syntax, source.syntax, None, None)
    target.dependencies = _unique(target.dependencies + source.dependencies)
    target.evidence = _evidence(target.evidence, source.evidence)
    _merge_named_messages(target.messages, source.messages, path, conflicts)
    _merge_named_enums(target.enums, source.enums, path, conflicts)


def _merge_named_messages(
    target: list[Message],
    source: list[Message],
    parent: str,
    conflicts: list[Conflict],
) -> None:
    by_name = {item.name: item for item in target}
    for incoming in source:
        current = by_name.get(incoming.name)
        if current is None:
            copied = deepcopy(incoming)
            target.append(copied)
            by_name[copied.name] = copied
            continue
        path = f"{parent}.{current.name}"
        current.confidence = _best(current.confidence, incoming.confidence)
        current.evidence = _evidence(current.evidence, incoming.evidence)
        _merge_fields(current.fields, incoming.fields, path, conflicts)
        _merge_named_messages(current.messages, incoming.messages, path, conflicts)
        _merge_named_enums(current.enums, incoming.enums, path, conflicts)


def _merge_fields(
    target: list[Field],
    source: list[Field],
    parent: str,
    conflicts: list[Conflict],
) -> None:
    by_number = {item.number: item for item in target}
    for incoming in source:
        current = by_number.get(incoming.number)
        if current is None:
            copied = deepcopy(incoming)
            target.append(copied)
            by_number[copied.number] = copied
            continue
        winner, loser = _ordered(current, incoming)
        path = f"{parent}.{incoming.number}"
        for attribute in (
            "name",
            "type_name",
            "label",
            "oneof",
            "json_name",
            "default_value",
            "packed",
        ):
            kept = getattr(winner, attribute)
            rejected = getattr(loser, attribute)
            if kept != rejected:
                _record(
                    conflicts,
                    path,
                    attribute,
                    kept,
                    rejected,
                    winner.confidence,
                    loser.confidence,
                )
        evidence = _evidence(current.evidence, incoming.evidence)
        if winner is incoming:
            replacement = deepcopy(incoming)
            replacement.evidence = evidence
            target[target.index(current)] = replacement
            by_number[incoming.number] = replacement
        else:
            current.evidence = evidence


def _merge_named_enums(
    target: list[EnumType],
    source: list[EnumType],
    parent: str,
    conflicts: list[Conflict],
) -> None:
    by_name = {item.name: item for item in target}
    for incoming in source:
        current = by_name.get(incoming.name)
        if current is None:
            copied = deepcopy(incoming)
            target.append(copied)
            by_name[copied.name] = copied
            continue
        path = f"{parent}.{current.name}"
        current.evidence = _evidence(current.evidence, incoming.evidence)
        winner_is_source = _RANK[incoming.confidence] > _RANK[current.confidence]
        current.confidence = _best(current.confidence, incoming.confidence)
        values = deepcopy(incoming.values if winner_is_source else current.values)
        other = current.values if winner_is_source else incoming.values
        by_number = {value.number: value for value in values}
        for value in other:
            known = by_number.get(value.number)
            if known is None:
                values.append(deepcopy(value))
                by_number[value.number] = value
            elif known.name != value.name:
                _record(
                    conflicts,
                    path,
                    f"value[{value.number}]",
                    known.name,
                    value.name,
                    current.confidence,
                    incoming.confidence,
                )
        current.values = values


def _ordered(first: Field, second: Field) -> tuple[Field, Field]:
    if _RANK[second.confidence] > _RANK[first.confidence]:
        return second, first
    return first, second


def _best(first: Confidence, second: Confidence) -> Confidence:
    return first if _RANK[first] >= _RANK[second] else second


def _record(
    conflicts: list[Conflict],
    path: str,
    attribute: str,
    kept: object,
    rejected: object,
    kept_confidence: Confidence | None,
    rejected_confidence: Confidence | None,
) -> None:
    conflicts.append(
        Conflict(
            path,
            attribute,
            str(kept),
            str(rejected),
            kept_confidence,
            rejected_confidence,
        )
    )


def _evidence(first: list[Evidence], second: list[Evidence]) -> list[Evidence]:
    return _unique(first + second)


def _unique(values: list[T]) -> list[T]:
    return list(dict.fromkeys(values))
