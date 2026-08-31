from pathlib import Path

from protoloom.tui.results import RecoveryOutput, SchemaRecord
from protoloom.tui.state import AppState, Screen


def test_log_stays_bounded_under_rapid_output() -> None:
    state = AppState()

    for index in range(100_000):
        state.append_log(f"line {index}")

    assert len(state.log) == 1000
    assert state.log[0] == "line 99000"
    assert state.log[-1] == "line 99999"


def test_filters_large_schema_collection() -> None:
    schemas = tuple(
        SchemaRecord(f"Schema{index}.proto", "large.package", {})
        for index in range(646)
    )
    state = AppState(output=RecoveryOutput(Path("out"), schemas, ()))

    state.query = "Schema64"

    assert [item.name for item in state.visible_schemas()] == [
        "Schema64.proto",
        "Schema640.proto",
        "Schema641.proto",
        "Schema642.proto",
        "Schema643.proto",
        "Schema644.proto",
        "Schema645.proto",
    ]


def test_screen_change_clears_error_and_selection_clamps() -> None:
    state = AppState(error="bad", selected=9)

    state.show(Screen.RESULTS)
    state.clamp_selection()

    assert state.screen is Screen.RESULTS
    assert state.error is None
    assert state.selected == 0
