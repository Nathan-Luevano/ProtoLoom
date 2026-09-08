import json

from protoloom.emit.jsonout import emit_json
from protoloom.model import Message, RecoveredSchema


def test_json_output_records_canonical_artifact_manifest() -> None:
    schema = RecoveredSchema(name="demo.proto", messages=[Message("Demo")])

    encoded = emit_json(
        [schema],
        [],
        ["report.md", "demo.proto", "recovery.json", "demo.proto"],
    )
    payload = json.loads(encoded)

    assert payload["artifacts"] == ["demo.proto", "recovery.json", "report.md"]
    assert payload["conflicts"] == []
    assert payload["schemas"][0]["name"] == "demo.proto"
    assert encoded.endswith("\n")
