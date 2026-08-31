import json
from pathlib import Path

import pytest

from protoloom.tui.results import OutputError, load_output


def _write(root: Path, value: object) -> None:
    (root / "recovery.json").write_text(json.dumps(value), encoding="utf-8")


def test_loads_recovery_output(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "schemas": [{"name": "sample.proto", "package": "demo"}],
            "conflicts": [{"reason": "different field types"}],
        },
    )
    (tmp_path / "report.md").write_text("Bail-outs: 7\n", encoding="utf-8")

    output = load_output(tmp_path)

    assert output.root == tmp_path
    assert [(item.name, item.package) for item in output.schemas] == [
        ("sample.proto", "demo")
    ]
    assert output.conflicts == ({"reason": "different field types"},)
    assert output.bailouts == 7


@pytest.mark.parametrize(
    "value, message",
    [
        ([], "root must be an object"),
        ({"schemas": {}, "conflicts": []}, "schemas must be a list"),
        ({"schemas": [{}], "conflicts": []}, "requires string name"),
    ],
)
def test_rejects_invalid_shapes(tmp_path: Path, value: object, message: str) -> None:
    _write(tmp_path, value)

    with pytest.raises(OutputError, match=message):
        load_output(tmp_path)


def test_reports_missing_and_malformed_files(tmp_path: Path) -> None:
    with pytest.raises(OutputError, match="cannot read"):
        load_output(tmp_path)
    (tmp_path / "recovery.json").write_text("{", encoding="utf-8")
    with pytest.raises(OutputError, match="line 1, column 2"):
        load_output(tmp_path)


def test_reports_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "recovery.json").write_bytes(b"\xff")

    with pytest.raises(OutputError, match="invalid UTF-8 at byte 0"):
        load_output(tmp_path)
