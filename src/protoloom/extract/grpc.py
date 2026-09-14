from collections import Counter
from dataclasses import dataclass

from protoloom.container.dex import DexFile
from protoloom.extract.lite import _instructions, _invoke_registers

_CONST_STRING = {0x1A, 0x1B}
_INVOKE_STATIC = 0x71
_INVOKE_DIRECT = 0x70
_SPUT_OBJECT = 0x69
_SGET_OBJECT = 0x62


@dataclass(frozen=True, slots=True)
class GrpcMethodEvidence:
    name: str
    request_descriptor: str
    response_descriptor: str
    # (declaring class, field name) of the sget'd MethodDescriptor.MethodType
    # constant -- streaming-ness is resolved from this at decode time, not
    # here, since interpreting a fixed io.grpc enum contract is meaning, not
    # mechanical extraction.
    type_field: tuple[str, str] | None


@dataclass(frozen=True, slots=True)
class GrpcServiceEvidence:
    class_descriptor: str
    methods: tuple[GrpcMethodEvidence, ...]


def _method_strings_and_evidence(
    dex: DexFile, code: tuple[int, ...], owner_type_index: int
) -> tuple[list[str], list[str], tuple[str, str] | None]:
    strings: list[str] = []
    defaults: list[str] = []
    type_field: tuple[str, str] | None = None
    for instruction in _instructions(code):
        opcode = instruction.opcode
        units = instruction.units
        if opcode in _CONST_STRING:
            index = units[1] if opcode == 0x1A else units[1] | units[2] << 16
            if index < len(dex.strings):
                strings.append(dex.strings[index])
        elif (
            opcode == _SGET_OBJECT and type_field is None and units[1] < len(dex.fields)
        ):
            field = dex.fields[units[1]]
            # The method's own cached-descriptor field (checked at entry,
            # e.g. `if (getFooMethod == null) {...}`) is declared on this
            # same Grpc holder class -- the real MethodType constant is
            # always a different, io.grpc-owned enum class.
            if field.class_index != owner_type_index:
                type_field = (dex.types[field.class_index], dex.field_name(field))
        elif opcode == _INVOKE_STATIC and units[1] < len(dex.methods):
            target = dex.methods[units[1]]
            if dex.method_name(target) == "getDefaultInstance":
                defaults.append(dex.types[target.class_index])
    return strings, defaults, type_field


def scan_grpc_services(dex: DexFile) -> tuple[GrpcServiceEvidence, ...]:
    # protoc-gen-grpc-java always names the generated stub holder
    # "<Service>Grpc" and gives it one no-arg static getXxxMethod() per RPC,
    # each building and caching a MethodDescriptor. That naming convention
    # is baked into the code generator's own template, not something R8
    # renames away in the unobfuscated real apps this has been verified
    # against; the bytecode shape inside each method is checked too, so an
    # unrelated "...Grpc"-named class with no matching methods is silently
    # skipped rather than misread.
    services: list[GrpcServiceEvidence] = []
    for item in dex.classes:
        descriptor = dex.types[item.class_index]
        simple_name = descriptor.removeprefix("L").removesuffix(";").rsplit("/", 1)[-1]
        if "$" in simple_name or not simple_name.endswith("Grpc"):
            continue
        candidates = []
        for method in dex.class_methods(item):
            if not method.code_offset:
                continue
            name = dex.method_name(method)
            if (
                name.startswith("get")
                and name.endswith("Method")
                and not dex.method_parameter_types(method)
            ):
                candidates.append(method)
        if not candidates:
            continue
        raw = [
            _method_strings_and_evidence(
                dex, dex.code_item(method.code_offset).instructions, item.class_index
            )
            for method in candidates
        ]
        counts = Counter(value for strings, _, _ in raw for value in strings)
        if not counts:
            continue
        service_name_value, service_hits = counts.most_common(1)[0]
        if service_hits < 2:
            continue
        methods: list[GrpcMethodEvidence] = []
        for strings, defaults, type_field in raw:
            distinct = {value for value in strings if value != service_name_value}
            if len(distinct) != 1 or len(defaults) != 2:
                continue
            methods.append(
                GrpcMethodEvidence(
                    next(iter(distinct)), defaults[0], defaults[1], type_field
                )
            )
        if methods:
            services.append(GrpcServiceEvidence(descriptor, tuple(methods)))
    return tuple(services)


def enum_constant_names(dex: DexFile, descriptor: str) -> dict[str, str]:
    # A standard Java enum's compiler-synthesized constructor call passes
    # the constant's own source name as a plain string argument to
    # Enum(String, int) -- and R8 can inline that trivial constructor
    # straight into <clinit>, replacing the call with one directly against
    # java.lang.Enum itself. Matching on the (String, int) signature rather
    # than the declaring class handles both cases, and reading the actual
    # name argument (e.g. "SERVER_STREAMING") is more robust than counting
    # declaration-order ordinals: it keeps working even if R8 reorders orv
    # drops the field names, as long as it needs the real name for possible
    # name()/toString()/valueOf() use elsewhere -- which is common enough
    # that R8 doesn't strip it by default.
    if descriptor not in dex.types:
        return {}
    type_index = dex.types.index(descriptor)
    names: dict[str, str] = {}
    for method, code in dex.iter_code_items():
        raw_method = dex.methods[method.method_index]
        if (
            raw_method.class_index != type_index
            or dex.method_name(raw_method) != "<clinit>"
        ):
            continue
        registers: dict[int, str] = {}
        pending_name: str | None = None
        for instruction in _instructions(code.instructions):
            opcode = instruction.opcode
            units = instruction.units
            if opcode in _CONST_STRING:
                index = units[1] if opcode == 0x1A else units[1] | units[2] << 16
                if index < len(dex.strings):
                    registers[units[0] >> 8] = dex.strings[index]
            elif opcode == _INVOKE_DIRECT and units[1] < len(dex.methods):
                target = dex.methods[units[1]]
                if dex.method_name(target) == "<init>" and dex.method_parameter_types(
                    target
                ) == ("Ljava/lang/String;", "I"):
                    args = _invoke_registers(instruction)
                    if len(args) >= 2 and args[1] in registers:
                        pending_name = registers[args[1]]
            elif (
                opcode == _SPUT_OBJECT
                and pending_name is not None
                and units[1] < len(dex.fields)
            ):
                field = dex.fields[units[1]]
                if dex.types[field.class_index] == descriptor:
                    names[pending_name] = dex.field_name(field)
                pending_name = None
    return names
