from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from protoloom.extract.grpc import enum_constant_names, scan_grpc_services


@dataclass(frozen=True, slots=True)
class _M:
    class_index: int
    name: str
    params: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _EM:
    method_index: int
    code_offset: int


@dataclass(frozen=True, slots=True)
class _F:
    class_index: int
    name: str


@dataclass(frozen=True, slots=True)
class _Class:
    class_index: int


def const_string(reg: int, index: int) -> tuple[int, int]:
    return (0x1A | reg << 8, index)


def sget_object(reg: int, index: int) -> tuple[int, int]:
    return (0x62 | reg << 8, index)


def sput_object(reg: int, index: int) -> tuple[int, int]:
    return (0x69 | reg << 8, index)


def invoke_static0(method_index: int) -> tuple[int, int, int]:
    return (0x71, method_index, 0)


def invoke_direct3(
    method_index: int, this: int, name: int, ordinal: int
) -> tuple[int, int, int]:
    packed = this | (name << 4) | (ordinal << 8)
    return (0x70 | 3 << 12, method_index, packed)


def const4(reg: int, value: int) -> tuple[int]:
    return (0x12 | reg << 8 | (value & 0xF) << 12,)


def return_object(reg: int) -> tuple[int]:
    return (0x11 | reg << 8,)


_STRINGS = ("svc.Foo", "Unary", "Stream", "UNARY", "SERVER_STREAMING")
_METHODS_BY_INDEX = {
    0: _M(2, "getDefaultInstance"),
    1: _M(3, "getDefaultInstance"),
    2: _M(1, "<init>", ("Ljava/lang/String;", "I")),
    3: _M(1, "<clinit>"),
    10: _M(0, "getUnaryMethod"),
    11: _M(0, "getStreamMethod"),
}
_METHODS = [
    _METHODS_BY_INDEX.get(index, _M(0, ""))
    for index in range(max(_METHODS_BY_INDEX) + 1)
]
_FIELDS = [_F(0, "cachedUnary"), _F(1, "l"), _F(1, "m")]

_UNARY_CODE = (
    *sget_object(0, 0),
    *const_string(1, 0),
    *const_string(2, 1),
    *sget_object(3, 1),
    *invoke_static0(0),
    *invoke_static0(1),
    *return_object(0),
)
_STREAM_CODE = (
    *sget_object(0, 0),
    *const_string(1, 0),
    *const_string(2, 2),
    *sget_object(3, 2),
    *invoke_static0(0),
    *invoke_static0(1),
    *return_object(0),
)
_CLINIT_CODE = (
    *const_string(0, 3),
    *const4(1, 0),
    *invoke_direct3(2, 9, 0, 1),
    *sput_object(9, 1),
    *const_string(0, 4),
    *const4(1, 2),
    *invoke_direct3(2, 9, 0, 1),
    *sput_object(9, 2),
)


def _resolve(item: Any) -> _M:
    if hasattr(item, "method_index"):
        return cast(_M, _METHODS[item.method_index])
    return cast(_M, item)


def _dex() -> Any:
    code_items = {
        100: SimpleNamespace(instructions=_UNARY_CODE),
        200: SimpleNamespace(instructions=_STREAM_CODE),
    }
    return SimpleNamespace(
        types=("Lsvc/FooGrpc;", "Lgrpc/MethodType;", "Lsvc/Req;", "Lsvc/Res;"),
        classes=(_Class(0),),
        strings=_STRINGS,
        fields=_FIELDS,
        methods=_METHODS,
        field_name=lambda item: item.name,
        method_name=lambda item: _resolve(item).name,
        method_parameter_types=lambda item: _resolve(item).params,
        class_methods=lambda item: (
            (_EM(10, 100), _EM(11, 200)) if item.class_index == 0 else ()
        ),
        code_item=lambda offset: code_items[offset],
        iter_code_items=lambda: (
            (_EM(3, 999), SimpleNamespace(instructions=_CLINIT_CODE)),
        ),
    )


def test_scan_recovers_unary_and_streaming_methods() -> None:
    services = scan_grpc_services(_dex())

    assert len(services) == 1
    service = services[0]
    assert service.class_descriptor == "Lsvc/FooGrpc;"
    by_name = {method.name: method for method in service.methods}
    assert by_name["Unary"].request_descriptor == "Lsvc/Req;"
    assert by_name["Unary"].response_descriptor == "Lsvc/Res;"
    assert by_name["Unary"].type_field == ("Lgrpc/MethodType;", "l")
    assert by_name["Stream"].type_field == ("Lgrpc/MethodType;", "m")


def test_enum_constant_names_survive_inlined_constructor() -> None:
    names = enum_constant_names(_dex(), "Lgrpc/MethodType;")

    assert names == {"UNARY": "l", "SERVER_STREAMING": "m"}


def test_scan_ignores_classes_without_grpc_shape() -> None:
    dex = _dex()
    dex.classes = (*dex.classes, _Class(4))
    dex.types = (*dex.types, "LUnrelatedGrpc;")

    services = scan_grpc_services(dex)

    assert len(services) == 1
