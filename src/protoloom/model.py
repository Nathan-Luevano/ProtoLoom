from dataclasses import dataclass, field
from enum import StrEnum


class Confidence(StrEnum):
    CERTAIN = "certain"
    HIGH = "high"
    MEDIUM = "medium"
    SPECULATIVE = "speculative"


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str
    location: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class EnumValue:
    name: str
    number: int


@dataclass(slots=True)
class EnumType:
    name: str
    values: list[EnumValue] = field(default_factory=list)
    confidence: Confidence = Confidence.SPECULATIVE
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(slots=True)
class Field:
    name: str
    number: int
    type_name: str
    confidence: Confidence
    evidence: list[Evidence] = field(default_factory=list)
    label: str = "optional"
    oneof: str | None = None
    json_name: str | None = None
    default_value: str | None = None
    packed: bool | None = None
    proto3_optional: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.number < 2**29:
            raise ValueError(f"invalid protobuf field number: {self.number}")
        if 19_000 <= self.number <= 19_999:
            raise ValueError(f"reserved protobuf field number: {self.number}")
        if self.label not in {"optional", "required", "repeated"}:
            raise ValueError(f"invalid protobuf field label: {self.label}")


@dataclass(slots=True)
class Message:
    name: str
    fields: list[Field] = field(default_factory=list)
    messages: list["Message"] = field(default_factory=list)
    enums: list[EnumType] = field(default_factory=list)
    confidence: Confidence = Confidence.SPECULATIVE
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(slots=True)
class RecoveredSchema:
    name: str
    package: str = ""
    syntax: str = "proto2"
    messages: list[Message] = field(default_factory=list)
    enums: list[EnumType] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.syntax not in {"proto2", "proto3"}:
            raise ValueError(f"unsupported protobuf syntax: {self.syntax}")
