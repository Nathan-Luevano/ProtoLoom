from dataclasses import dataclass

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import DecodeError, EncodeError, Message

MAX_ROUNDTRIP_PAYLOAD_SIZE = 256 * 1024 * 1024
MAX_ROUNDTRIP_DESCRIPTOR_SIZE = 64 * 1024 * 1024
MAX_ROUNDTRIP_FILES = 10_000


@dataclass(frozen=True, slots=True)
class RoundTripResult:
    decoded: bool
    byte_identical: bool
    semantically_equal: bool
    input_size: int
    output_size: int | None
    error: str | None = None


def roundtrip_message(message_class: type[Message], payload: bytes) -> RoundTripResult:
    if len(payload) > MAX_ROUNDTRIP_PAYLOAD_SIZE:
        return _failure(
            len(payload),
            f"payload exceeds {MAX_ROUNDTRIP_PAYLOAD_SIZE} bytes",
        )
    message = message_class()
    try:
        message.ParseFromString(payload)
        encoded = message.SerializeToString(deterministic=True)
        reparsed = message_class()
        reparsed.ParseFromString(encoded)
    except (DecodeError, EncodeError, ValueError, TypeError) as error:
        return RoundTripResult(False, False, False, len(payload), None, str(error))
    return RoundTripResult(
        True,
        encoded == payload,
        reparsed == message,
        len(payload),
        len(encoded),
    )


def roundtrip_descriptor_set(
    descriptor_set: bytes, message_name: str, payload: bytes
) -> RoundTripResult:
    if len(payload) > MAX_ROUNDTRIP_PAYLOAD_SIZE:
        return _failure(
            len(payload),
            f"payload exceeds {MAX_ROUNDTRIP_PAYLOAD_SIZE} bytes",
        )
    if len(descriptor_set) > MAX_ROUNDTRIP_DESCRIPTOR_SIZE:
        return _failure(
            len(payload),
            f"descriptor set exceeds {MAX_ROUNDTRIP_DESCRIPTOR_SIZE} bytes",
        )
    files = descriptor_pb2.FileDescriptorSet()
    try:
        files.ParseFromString(descriptor_set)
        if len(files.file) > MAX_ROUNDTRIP_FILES:
            raise ValueError(f"descriptor set exceeds {MAX_ROUNDTRIP_FILES} files")
        pool = descriptor_pool.DescriptorPool()
        pending = list(files.file)
        while pending:
            deferred = []
            for file_descriptor in pending:
                try:
                    pool.Add(file_descriptor)
                except TypeError:
                    deferred.append(file_descriptor)
            if len(deferred) == len(pending):
                names = ", ".join(item.name for item in deferred)
                raise ValueError(f"descriptor dependencies cannot be resolved: {names}")
            pending = deferred
        descriptor = pool.FindMessageTypeByName(message_name.lstrip("."))
        message_class = message_factory.GetMessageClass(descriptor)
    except (DecodeError, KeyError, TypeError, ValueError) as error:
        return RoundTripResult(False, False, False, len(payload), None, str(error))
    return roundtrip_message(message_class, payload)


def _failure(input_size: int, error: str) -> RoundTripResult:
    return RoundTripResult(False, False, False, input_size, None, error)
