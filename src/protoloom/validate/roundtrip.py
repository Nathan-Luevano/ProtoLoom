from dataclasses import dataclass

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from google.protobuf.message import DecodeError, EncodeError, Message


@dataclass(frozen=True, slots=True)
class RoundTripResult:
    decoded: bool
    byte_identical: bool
    semantically_equal: bool
    input_size: int
    output_size: int | None
    error: str | None = None


def roundtrip_message(message_class: type[Message], payload: bytes) -> RoundTripResult:
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
    files = descriptor_pb2.FileDescriptorSet()
    try:
        files.ParseFromString(descriptor_set)
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
