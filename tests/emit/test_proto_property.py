from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from protoloom.emit.proto import emit_proto
from protoloom.model import (
    Confidence,
    EnumType,
    EnumValue,
    Field,
    Message,
    RecoveredSchema,
)
from protoloom.validate.compile import compile_proto

_SCALARS = [
    "bool",
    "bytes",
    "double",
    "fixed32",
    "fixed64",
    "float",
    "int32",
    "int64",
    "sfixed32",
    "sfixed64",
    "sint32",
    "sint64",
    "string",
    "uint32",
    "uint64",
]
_MAP_KEYS = [
    "bool",
    "int32",
    "int64",
    "uint32",
    "uint64",
    "sint32",
    "sint64",
    "fixed32",
    "fixed64",
    "sfixed32",
    "sfixed64",
    "string",
]
# A small vocabulary that mixes ordinary identifiers with names that are
# awkward on purpose: protoc statement keywords, dotted strings, and empty --
# the same kind of thing a decoder can hand emit_proto from unsanitized or
# adversarial binary input.
_NAMES = st.sampled_from(
    ["Foo", "Bar", "message", "oneof", "enum", "a.b", "_x", "0weird", ""]
)


def _enum_strategy() -> st.SearchStrategy[EnumType]:
    values = st.lists(
        st.builds(
            EnumValue, name=_NAMES, number=st.integers(min_value=-5, max_value=10)
        ),
        max_size=4,
    )
    return st.builds(
        EnumType, name=_NAMES, values=values, confidence=st.sampled_from(Confidence)
    )


def _field_type(
    enum_names: list[str], message_names: list[str]
) -> st.SearchStrategy[str]:
    choices = [st.sampled_from(_SCALARS)]
    if enum_names:
        choices.append(st.sampled_from(enum_names))
    if message_names:
        choices.append(st.sampled_from(message_names))
    value_choices = [*_SCALARS, *message_names] or _SCALARS
    choices.append(
        st.tuples(st.sampled_from(_MAP_KEYS), st.sampled_from(value_choices)).map(
            lambda kv: f"map<{kv[0]},{kv[1]}>"
        )
    )
    return st.one_of(*choices)


@st.composite
def _message_strategy(draw: st.DrawFn, depth: int = 2) -> Message:
    name = draw(_NAMES)
    enums = draw(st.lists(_enum_strategy(), max_size=2))
    enum_names = [item.name for item in enums]
    nested = (
        draw(st.lists(_message_strategy(depth - 1), max_size=2)) if depth > 0 else []
    )
    nested_names = [item.name for item in nested]
    count = draw(st.integers(min_value=0, max_value=5))
    numbers = draw(
        st.lists(
            st.integers(min_value=1, max_value=30),
            min_size=count,
            max_size=count,
            unique=True,
        )
    )
    oneof_pool = draw(st.lists(_NAMES, max_size=2))
    fields = []
    for number in numbers:
        oneof = draw(st.sampled_from([None, *oneof_pool])) if oneof_pool else None
        fields.append(
            Field(
                name=draw(_NAMES),
                number=number,
                type_name=draw(_field_type(enum_names, nested_names)),
                confidence=draw(st.sampled_from(Confidence)),
                label=draw(st.sampled_from(["optional", "required", "repeated"]))
                if oneof is None
                else "optional",
                oneof=oneof,
                proto3_optional=draw(st.booleans()) if oneof is None else False,
                packed=draw(st.sampled_from([None, True, False])),
            )
        )
    return Message(name=name, fields=fields, messages=nested, enums=enums)


@st.composite
def _schema_strategy(draw: st.DrawFn) -> RecoveredSchema:
    return RecoveredSchema(
        name="recovered.proto",
        package=draw(st.one_of(st.just(""), _NAMES)),
        syntax=draw(st.sampled_from(["proto2", "proto3"])),
        messages=draw(st.lists(_message_strategy(), max_size=3)),
        enums=draw(st.lists(_enum_strategy(), max_size=2)),
    )


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(_schema_strategy())
def test_emit_then_compile_never_crashes(schema: RecoveredSchema) -> None:
    # The only acceptable outcomes are a clean compile (possibly failing, for
    # a schema that turned out not to be expressible in valid proto syntax)
    # or a documented ValueError from emit_proto's own budget guard -- never
    # an unhandled exception, and never a hang (compile_proto has its own
    # timeout).
    try:
        source = emit_proto(schema)
    except ValueError:
        return
    compile_proto(source, timeout_seconds=10)
