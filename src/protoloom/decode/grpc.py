from protoloom.container.dex import DexFile
from protoloom.decode.lite import _resolve_message_type
from protoloom.extract.grpc import GrpcServiceEvidence, enum_constant_names
from protoloom.model import (
    Confidence,
    Evidence,
    RecoveredSchema,
    Service,
    ServiceMethod,
)

# io.grpc.MethodDescriptor.MethodType's own real constant names -- read from
# the enum constructor's surviving name argument (see
# extract.grpc.enum_constant_names), not guessed from ordinal position.
_CLIENT_STREAMING = {"CLIENT_STREAMING", "BIDI_STREAMING"}
_SERVER_STREAMING = {"SERVER_STREAMING", "BIDI_STREAMING"}


def _service_name(descriptor: str) -> str:
    simple = descriptor.removeprefix("L").removesuffix(";").rsplit("/", 1)[-1]
    return simple.removesuffix("Grpc") or simple


def decode_grpc_service(
    dex: DexFile, evidence: GrpcServiceEvidence, source: str
) -> RecoveredSchema:
    kind_by_field: dict[str, dict[str, str]] = {}
    dependencies: list[str] = []
    methods: list[ServiceMethod] = []
    for item in evidence.methods:
        input_type, input_import = _resolve_message_type(item.request_descriptor)
        output_type, output_import = _resolve_message_type(item.response_descriptor)
        for path in (input_import, output_import):
            if path is not None:
                dependencies.append(path)
        client_streaming = False
        server_streaming = False
        confidence = Confidence.HIGH
        if item.type_field is None:
            confidence = Confidence.MEDIUM
        else:
            owner, field_name = item.type_field
            names = kind_by_field.setdefault(owner, enum_constant_names(dex, owner))
            kind = next(
                (value for value, field in names.items() if field == field_name), None
            )
            if kind is None:
                confidence = Confidence.MEDIUM
            else:
                client_streaming = kind in _CLIENT_STREAMING
                server_streaming = kind in _SERVER_STREAMING
        methods.append(
            ServiceMethod(
                item.name,
                input_type,
                output_type,
                confidence,
                [Evidence(source, evidence.class_descriptor, "grpc MethodDescriptor")],
                client_streaming=client_streaming,
                server_streaming=server_streaming,
            )
        )
    service = Service(
        _service_name(evidence.class_descriptor),
        methods,
        Confidence.HIGH,
        [Evidence(source, evidence.class_descriptor, "protoc-gen-grpc-java stub")],
    )
    package = (
        evidence.class_descriptor.removeprefix("L").removesuffix(";").rsplit("/", 1)[0]
    )
    return RecoveredSchema(
        name=f"{service.name}.proto",
        package=package.replace("/", "."),
        services=[service],
        dependencies=sorted(dict.fromkeys(dependencies)),
        evidence=service.evidence,
    )
