from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from protoloom.decode.grpc import decode_grpc_service
from protoloom.extract.grpc import GrpcMethodEvidence, GrpcServiceEvidence
from protoloom.model import Confidence


@dataclass(frozen=True, slots=True)
class _M:
    class_index: int
    name: str
    params: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _EM:
    method_index: int


@dataclass(frozen=True, slots=True)
class _F:
    class_index: int
    name: str


def const_string(reg: int, index: int) -> tuple[int, int]:
    return (0x1A | reg << 8, index)


def sput_object(reg: int, index: int) -> tuple[int, int]:
    return (0x69 | reg << 8, index)


def invoke_direct3(
    method_index: int, this: int, name: int, ordinal: int
) -> tuple[int, int, int]:
    packed = this | (name << 4) | (ordinal << 8)
    return (0x70 | 3 << 12, method_index, packed)


def const4(reg: int, value: int) -> tuple[int]:
    return (0x12 | reg << 8 | (value & 0xF) << 12,)


_METHODS = [
    _M(1, "<init>", ("Ljava/lang/String;", "I")),  # 0
    _M(1, "<clinit>"),  # 1
]
_FIELDS = [_F(1, "l"), _F(1, "m")]
_STRINGS = ("UNARY", "SERVER_STREAMING")
_CLINIT_CODE = (
    *const_string(0, 0),
    *const4(1, 0),
    *invoke_direct3(0, 9, 0, 1),
    *sput_object(9, 0),
    *const_string(0, 1),
    *const4(1, 2),
    *invoke_direct3(0, 9, 0, 1),
    *sput_object(9, 1),
)


def _resolve(item: Any) -> _M:
    if hasattr(item, "method_index"):
        return cast(_M, _METHODS[item.method_index])
    return cast(_M, item)


def _dex() -> Any:
    return SimpleNamespace(
        types=("Lsvc/FooGrpc;", "Lgrpc/MethodType;"),
        strings=_STRINGS,
        fields=_FIELDS,
        methods=_METHODS,
        field_name=lambda item: item.name,
        method_name=lambda item: _resolve(item).name,
        method_parameter_types=lambda item: _resolve(item).params,
        iter_code_items=lambda: ((_EM(1), SimpleNamespace(instructions=_CLINIT_CODE)),),
    )


def test_decode_resolves_streaming_and_types() -> None:
    evidence = GrpcServiceEvidence(
        "Lsvc/FooGrpc;",
        (
            GrpcMethodEvidence(
                "Unary", "Lsvc/Req;", "Lsvc/Res;", ("Lgrpc/MethodType;", "l")
            ),
            GrpcMethodEvidence(
                "Stream", "Lsvc/Req;", "Lsvc/Res;", ("Lgrpc/MethodType;", "m")
            ),
        ),
    )

    schema = decode_grpc_service(_dex(), evidence, "test.dex")

    assert schema.name == "Foo.proto"
    assert schema.package == "svc"
    service = schema.services[0]
    assert service.name == "Foo"
    by_name = {method.name: method for method in service.methods}
    assert by_name["Unary"].input_type == "Req"
    assert by_name["Unary"].output_type == "Res"
    assert by_name["Unary"].client_streaming is False
    assert by_name["Unary"].server_streaming is False
    assert by_name["Unary"].confidence == Confidence.HIGH
    assert by_name["Stream"].server_streaming is True
    assert by_name["Stream"].client_streaming is False


def test_decode_well_known_request_type_adds_dependency() -> None:
    evidence = GrpcServiceEvidence(
        "Lsvc/FooGrpc;",
        (
            GrpcMethodEvidence(
                "Call",
                "Lcom/google/protobuf/Empty;",
                "Lcom/google/protobuf/BoolValue;",
                None,
            ),
        ),
    )

    schema = decode_grpc_service(_dex(), evidence, "test.dex")

    method = schema.services[0].methods[0]
    assert method.input_type == ".google.protobuf.Empty"
    assert method.output_type == ".google.protobuf.BoolValue"
    assert method.confidence == Confidence.MEDIUM
    assert "google/protobuf/empty.proto" in schema.dependencies
    assert "google/protobuf/wrappers.proto" in schema.dependencies


def test_decode_unresolved_streaming_kind_stays_unary_but_lower_confidence() -> None:
    evidence = GrpcServiceEvidence(
        "Lsvc/FooGrpc;",
        (
            GrpcMethodEvidence(
                "Mystery", "Lsvc/Req;", "Lsvc/Res;", ("Lgrpc/MethodType;", "unknown")
            ),
        ),
    )

    schema = decode_grpc_service(_dex(), evidence, "test.dex")

    method = schema.services[0].methods[0]
    assert method.client_streaming is False
    assert method.server_streaming is False
    assert method.confidence == Confidence.MEDIUM
