import importlib.util
import json
from pathlib import Path
from types import ModuleType

from google.protobuf.descriptor_pb2 import FieldDescriptorProto, FileDescriptorSet


def load_diagnostics() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "diagnose_real_app.py"
    spec = importlib.util.spec_from_file_location("diagnose_real_app", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load diagnose_real_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnostics = load_diagnostics()
load_recovered = diagnostics.load_recovered
load_truth = diagnostics.load_truth


def test_truth_package_override_rewrites_message_types(tmp_path: Path) -> None:
    descriptors = FileDescriptorSet()
    schema = descriptors.file.add(name="schema.proto", package="source")
    parent = schema.message_type.add(name="Parent")
    schema.message_type.add(name="Child")
    field = parent.field.add(
        name="child",
        number=1,
        type=FieldDescriptorProto.TYPE_MESSAGE,
        type_name=".source.Child",
    )
    field.label = FieldDescriptorProto.LABEL_OPTIONAL
    truth = tmp_path / "truth.desc"
    truth.write_bytes(descriptors.SerializeToString())

    recovered = load_truth(truth, "schema.proto", "runtime")

    assert [message.name for message in recovered.messages] == [
        "runtime.Parent",
        "runtime.Child",
    ]
    assert recovered.messages[0].fields[0].proto_type == "runtime.Child"


def test_recovered_descriptor_scopes_to_top_level_roots(tmp_path: Path) -> None:
    descriptors = FileDescriptorSet()
    schema = descriptors.file.add(name="recovered.proto", package="runtime")
    keep = schema.message_type.add(name="Keep")
    keep.nested_type.add(name="Nested")
    schema.message_type.add(name="Unrelated")
    recovered = tmp_path / "recovered.desc"
    recovered.write_bytes(descriptors.SerializeToString())

    result = load_recovered(recovered, "runtime", {"Keep"})

    assert [message.name for message in result.messages] == [
        "runtime.Keep",
        "runtime.Keep.Nested",
    ]


def test_recovered_json_scores_top_level_and_message_local_enums(
    tmp_path: Path,
) -> None:
    recovery = tmp_path / "recovery.json"
    recovery.write_text(
        json.dumps(
            {
                "schemas": [
                    {
                        "package": "example",
                        "enums": [
                            {"name": "State", "values": [{"name": "OFF", "number": 0}]}
                        ],
                        "messages": [
                            {
                                "name": "Record",
                                "fields": [
                                    {
                                        "number": 1,
                                        "name": "state",
                                        "type_name": "State",
                                        "label": "optional",
                                        "oneof": None,
                                    },
                                    {
                                        "number": 2,
                                        "name": "kind",
                                        "type_name": "Kind",
                                        "label": "optional",
                                        "oneof": None,
                                    },
                                ],
                                "enums": [
                                    {
                                        "name": "Kind",
                                        "values": [{"name": "NONE", "number": 0}],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    schema = load_recovered(recovery, "example")

    assert schema.enums[0].name == "State"
    assert schema.messages[0].enums[0].name == "Kind"
    assert schema.messages[0].fields[0].proto_type == "example.State"
    assert [field.wire_type for field in schema.messages[0].fields] == [0, 0]
