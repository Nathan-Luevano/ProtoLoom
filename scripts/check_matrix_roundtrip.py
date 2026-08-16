import argparse
from pathlib import Path

from google.protobuf import descriptor_pb2

from protoloom.validate.roundtrip import roundtrip_descriptor_set


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--truth", type=Path, required=True)
    parser.add_argument("--recovered", type=Path, required=True)
    args = parser.parse_args()

    truth = descriptor_pb2.FileDescriptorSet.FromString(args.truth.read_bytes())
    truth_names = {
        f"{file.package}.{message.name}" if file.package else message.name
        for file in truth.file
        for message in file.message_type
    }
    if "matrix.Everything" not in truth_names:
        raise SystemExit("truth descriptor does not contain matrix.Everything")
    payload = bytes.fromhex(
        "082a10111a05616c696365220200ff2a0301ac0232080a06696e73696465"
        "3a070a056368696c6440014a080a0477696e731007520663686f73656e6001"
    )

    recovered_bytes = args.recovered.read_bytes()
    recovered = descriptor_pb2.FileDescriptorSet.FromString(recovered_bytes)
    candidates = [
        (
            len(message.field),
            f"{file.package}.{message.name}" if file.package else message.name,
        )
        for file in recovered.file
        for message in file.message_type
    ]
    if not candidates:
        raise SystemExit("recovered descriptor set has no messages")
    _, recovered_name = max(candidates)
    result = roundtrip_descriptor_set(recovered_bytes, recovered_name, payload)
    print(f"round_trip_rate: {100.0 if result.byte_identical else 0.0:.2f}% (1/1)")
    if not result.byte_identical:
        raise SystemExit(result.error or "payload was not byte-identical")


if __name__ == "__main__":
    main()
