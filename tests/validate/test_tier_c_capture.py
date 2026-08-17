import hashlib
import json
from pathlib import Path
from typing import Any, cast

from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

from protoloom.validate.roundtrip import RoundTripResult, roundtrip_descriptor_set

FIXTURE = Path("benchmarks/corpora/tier-c-bitwarden")


def _manifest() -> dict[str, Any]:
    raw: object = json.loads((FIXTURE / "manifest.json").read_text())
    assert isinstance(raw, dict)
    return raw


def _section(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    section = manifest[name]
    assert isinstance(section, dict)
    return section


def _result(manifest: dict[str, Any], name: str) -> RoundTripResult:
    payload = (FIXTURE / "payload.bin").read_bytes()
    section = _section(manifest, name)
    descriptor = (FIXTURE / str(section["descriptor_file"])).read_bytes()
    return roundtrip_descriptor_set(descriptor, str(section["message"]), payload)


def test_tier_c_artifact_hashes_are_pinned() -> None:
    manifest = _manifest()
    capture = _section(manifest, "capture")
    payload = (FIXTURE / str(capture["payload_file"])).read_bytes()
    assert len(payload) == capture["payload_size"]
    assert hashlib.sha256(payload).hexdigest() == capture["payload_sha256"]
    for name in ("truth", "recovered"):
        section = _section(manifest, name)
        descriptor = (FIXTURE / str(section["descriptor_file"])).read_bytes()
        assert hashlib.sha256(descriptor).hexdigest() == section["descriptor_sha256"]


def test_real_capture_semantically_round_trips_with_both_schemas() -> None:
    manifest = _manifest()
    expected = _section(manifest, "expected")
    for name in ("truth", "recovered"):
        result = _result(manifest, name)
        target = expected[name]
        assert isinstance(target, dict)
        assert result.decoded is target["decoded"]
        assert result.semantically_equal is target["semantically_equal"]
        assert result.byte_identical is target["byte_identical"]
        assert result.output_size == target["output_size"]


def test_capture_contains_one_disposable_totp_entry() -> None:
    manifest = _manifest()
    truth = _section(manifest, "truth")
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(
        (FIXTURE / str(truth["descriptor_file"])).read_bytes()
    )
    pool = descriptor_pool.DescriptorPool()
    for descriptor in descriptor_set.file:
        pool.Add(descriptor)
    message_type = pool.FindMessageTypeByName(str(truth["message"]))
    message = cast(Any, message_factory.GetMessageClass(message_type)())
    message.ParseFromString((FIXTURE / "payload.bin").read_bytes())
    assert len(message.otp_parameters) == 1
    entry = message.otp_parameters[0]
    assert len(entry.secret) == 10
    assert entry.type == 2
    assert message.batch_size == 1
    assert message.version == 2


def test_byte_difference_is_only_explicit_default_canonicalization() -> None:
    manifest = _manifest()
    truth = _section(manifest, "truth")
    descriptor_set = descriptor_pb2.FileDescriptorSet.FromString(
        (FIXTURE / str(truth["descriptor_file"])).read_bytes()
    )
    pool = descriptor_pool.DescriptorPool()
    for descriptor in descriptor_set.file:
        pool.Add(descriptor)
    message_type = pool.FindMessageTypeByName(str(truth["message"]))
    message = message_factory.GetMessageClass(message_type)()
    payload = (FIXTURE / "payload.bin").read_bytes()
    message.ParseFromString(payload)
    canonical = message.SerializeToString(deterministic=True)
    assert payload.endswith(b"\x20\x00")
    assert canonical == payload[:-2]
