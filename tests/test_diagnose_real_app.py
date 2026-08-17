import importlib.util
import json
from pathlib import Path
from types import ModuleType


def load_diagnostics() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "diagnose_real_app.py"
    spec = importlib.util.spec_from_file_location("diagnose_real_app", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load diagnose_real_app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


load_recovered = load_diagnostics().load_recovered


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
