from google.protobuf.descriptor_pb2 import (
    DescriptorProto,
    EnumDescriptorProto,
    EnumValueDescriptorProto,
    FieldDescriptorProto,
    FileDescriptorProto,
    OneofDescriptorProto,
)
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from protoloom.decode.descpb import decode_file_descriptor
from protoloom.model import RecoveredSchema

# Untrusted binary input decodes to real FileDescriptorProto objects, not
# arbitrary bytes -- so the boundary worth fuzzing is "any value protobuf's
# own parser would accept", not "any string of bytes". Build real protobuf
# objects directly (no hypothesis protobuf-strategies package is vendored
# here) with a name vocabulary that includes protoc statement keywords,
# dotted strings, and empty names -- the wire format itself never validates
# identifier shape, only decode_file_descriptor's own consumers do.
_NAMES = st.sampled_from(
    ["Foo", "Bar", "message", "oneof", "enum", "a.b", "_x", "0weird", ""]
)
_FIELD_NUMBERS = st.integers(min_value=1, max_value=536_870_911).filter(
    lambda n: not (19_000 <= n <= 19_999)
)
_LABELS = [
    FieldDescriptorProto.LABEL_OPTIONAL,
    FieldDescriptorProto.LABEL_REQUIRED,
    FieldDescriptorProto.LABEL_REPEATED,
]
_SCALAR_TYPES = [
    value.number
    for value in FieldDescriptorProto.Type.DESCRIPTOR.values
    if value.name not in {"TYPE_MESSAGE", "TYPE_GROUP", "TYPE_ENUM"}
]


def _enum_strategy() -> st.SearchStrategy[EnumDescriptorProto]:
    values = st.lists(
        st.builds(
            EnumValueDescriptorProto,
            name=_NAMES,
            number=st.integers(min_value=-5, max_value=10),
        ),
        max_size=4,
    )
    return st.builds(EnumDescriptorProto, name=_NAMES, value=values)


@st.composite
def _field_strategy(draw: st.DrawFn, oneof_count: int) -> FieldDescriptorProto:
    kind = draw(st.sampled_from(["scalar", "message", "enum"]))
    if kind == "scalar":
        field_type = draw(st.sampled_from(_SCALAR_TYPES))
        type_name = ""
    else:
        field_type = (
            FieldDescriptorProto.TYPE_MESSAGE
            if kind == "message"
            else FieldDescriptorProto.TYPE_ENUM
        )
        type_name = draw(_NAMES)
    field = FieldDescriptorProto(
        name=draw(_NAMES),
        number=draw(_FIELD_NUMBERS),
        label=draw(st.sampled_from(_LABELS)),
        type=FieldDescriptorProto.Type.ValueType(field_type),
        type_name=type_name,
        proto3_optional=draw(st.booleans()),
    )
    if oneof_count and draw(st.booleans()):
        field.oneof_index = draw(st.integers(min_value=0, max_value=oneof_count - 1))
    if draw(st.booleans()):
        field.options.packed = draw(st.booleans())
    return field


@st.composite
def _message_strategy(draw: st.DrawFn, depth: int = 2) -> DescriptorProto:
    oneofs = [OneofDescriptorProto(name=n) for n in draw(st.lists(_NAMES, max_size=2))]
    field_count = draw(st.integers(min_value=0, max_value=5))
    fields = draw(
        st.lists(
            _field_strategy(len(oneofs)), min_size=field_count, max_size=field_count
        )
    )
    nested = (
        draw(st.lists(_message_strategy(depth - 1), max_size=2)) if depth > 0 else []
    )
    return DescriptorProto(
        name=draw(_NAMES),
        field=fields,
        nested_type=nested,
        enum_type=draw(st.lists(_enum_strategy(), max_size=2)),
        oneof_decl=oneofs,
    )


@st.composite
def _file_strategy(draw: st.DrawFn) -> FileDescriptorProto:
    return FileDescriptorProto(
        name=draw(_NAMES),
        package=draw(st.one_of(st.just(""), _NAMES)),
        syntax=draw(st.sampled_from(["proto2", "proto3", ""])),
        message_type=draw(st.lists(_message_strategy(), max_size=3)),
        enum_type=draw(st.lists(_enum_strategy(), max_size=2)),
        dependency=draw(st.lists(_NAMES, max_size=3)),
    )


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
@given(_file_strategy())
def test_decode_file_descriptor_never_crashes(raw: FileDescriptorProto) -> None:
    # Real untrusted binary data enters the pipeline right here: any
    # protobuf-valid FileDescriptorProto (however adversarial its shape or
    # names) must either decode cleanly or raise decode_file_descriptor's
    # own documented ValueError bail-out -- never an unhandled KeyError,
    # IndexError, or anything else that would crash the CLI mid-scan.
    try:
        schema = decode_file_descriptor(raw, "fuzz", "0x0")
    except ValueError:
        return
    assert isinstance(schema, RecoveredSchema)
