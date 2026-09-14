import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from protoloom.tui.application import TuiApplication
from protoloom.tui.jobs import ExtractionRequest, JobResult
from protoloom.tui.results import RecoveryOutput, SchemaRecord
from protoloom.tui.state import Screen


class ResizableOutput(DummyOutput):
    size = Size(rows=24, columns=80)

    def get_size(self) -> Size:
        return self.size


def test_body_text_shows_loading_off_home() -> None:
    tui = TuiApplication(app_output=DummyOutput())
    tui.state.show(Screen.SETUP)
    assert tui._body_text() == [("", "\n  Loading…")]


def test_show_home_resets_screen_and_focus() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.state.show(Screen.SETUP)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            tui._show_home()

            assert tui.state.screen is Screen.HOME
            assert tui.application.layout.has_focus(tui.body)
            tui.application.exit()
            await task

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("screen", "focus_attr"),
    [
        (Screen.OPEN, "open_path"),
        (Screen.SETUP, "source"),
        (Screen.RESULTS, "results_control"),
    ],
)
def test_help_close_restores_focus_per_screen(screen: Screen, focus_attr: str) -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            if screen is Screen.RESULTS:
                tui.state.output = RecoveryOutput(Path("out"), (), ())
            tui.state.show(screen)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("?")
            await asyncio.sleep(0.05)
            assert tui.state.help_visible is True

            app_input.send_text("?")
            await asyncio.sleep(0.05)
            assert tui.state.help_visible is False
            assert tui.application.layout.has_focus(getattr(tui, focus_attr))
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_open_output_reports_load_failure() -> None:
    tui = TuiApplication(app_output=DummyOutput())
    tui.open_path.text = "/nonexistent-protoloom-output"
    tui._open_output()

    assert tui.state.error is not None
    assert tui.state.screen is not Screen.RESULTS


def test_summary_text_renders_loaded_output() -> None:
    tui = TuiApplication(app_output=DummyOutput())
    tui.state.output = RecoveryOutput(Path("out"), (), ())
    rendered = tui._summary_text()
    assert "Schemas" in str(rendered)


def test_detail_text_reports_no_matches() -> None:
    tui = TuiApplication(app_output=DummyOutput())
    tui.state.output = RecoveryOutput(
        Path("out"), (SchemaRecord("Alpha.proto", "pkg", {}),), ()
    )
    tui.state.query = "nomatch"
    assert tui._detail_text() == "\n No matching schemas"


def test_start_reports_missing_input_file(tmp_path: Path) -> None:
    tui = TuiApplication(app_output=DummyOutput())
    tui.source.text = str(tmp_path / "missing.apk")
    tui._start()
    assert tui.state.error == f"Input file does not exist: {tmp_path / 'missing.apk'}"
    assert tui.state.screen is not Screen.RUNNING


def test_run_job_reports_nonzero_exit(tmp_path: Path) -> None:
    async def exercise() -> None:
        tui = TuiApplication(app_output=DummyOutput())
        request = ExtractionRequest(
            tmp_path / "in", tmp_path / "out", jadx=False, allow_heuristic_lite=False
        )

        async def fake_run(
            req: ExtractionRequest, on_line: Callable[[str], None]
        ) -> JobResult:
            return JobResult(returncode=3, cancelled=False)

        tui.job.run = fake_run  # type: ignore[method-assign,assignment]
        await tui._run_job(request)
        assert tui.state.error == "Extraction failed with status 3"

    asyncio.run(exercise())


def test_run_job_reports_missing_recovery_output(tmp_path: Path) -> None:
    async def exercise() -> None:
        tui = TuiApplication(app_output=DummyOutput())
        request = ExtractionRequest(
            tmp_path / "in", tmp_path / "out", jadx=False, allow_heuristic_lite=False
        )

        async def fake_run(
            req: ExtractionRequest, on_line: Callable[[str], None]
        ) -> JobResult:
            return JobResult(returncode=0, cancelled=False)

        tui.job.run = fake_run  # type: ignore[method-assign,assignment]
        await tui._run_job(request)
        assert tui.state.error is not None
        assert tui.state.screen is not Screen.RESULTS

    asyncio.run(exercise())


def test_keyboard_opens_existing_output_form() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("\x1b[B\r")
            await asyncio.sleep(0.05)

            assert tui.state.screen is Screen.OPEN
            assert tui.application.layout.has_focus(tui.open_path)
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_keyboard_loads_existing_output(tmp_path: Path) -> None:
    (tmp_path / "recovery.json").write_text(
        json.dumps({"schemas": [{"name": "sample.proto"}], "conflicts": []}),
        encoding="utf-8",
    )

    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)
            app_input.send_text(f"\x1b[B\r{tmp_path}")
            await asyncio.sleep(0.05)
            app_input.send_text("\t")
            await asyncio.sleep(0.05)
            app_input.send_text("\r")
            await asyncio.sleep(0.05)

            assert tui.state.screen is Screen.RESULTS
            assert tui.state.output is not None
            assert tui.state.output.schemas[0].name == "sample.proto"
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_keyboard_runs_extraction_to_results(tmp_path: Path) -> None:
    descriptor = FileDescriptorProto(name="sample.proto", syntax="proto3")
    descriptor.message_type.add(name="Sample")
    source = tmp_path / "sample.desc"
    source.write_bytes(FileDescriptorSet(file=[descriptor]).SerializeToString())

    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.source.text = str(source)
            tui.output.text = str(tmp_path / "out")
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)
            app_input.send_text("\r")
            await asyncio.sleep(0.05)
            app_input.send_text("\t\t\t\t\r")
            for _ in range(100):
                if tui.state.screen is Screen.RESULTS:
                    break
                await asyncio.sleep(0.02)

            assert tui.state.screen is Screen.RESULTS
            assert tui.state.output is not None
            assert tui.state.output.schemas[0].name == "sample.proto"
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_keyboard_reports_extraction_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input"
    source.touch()
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (str(tmp_path / "missing-command"),),
    )

    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.source.text = str(source)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)
            app_input.send_text("\r")
            await asyncio.sleep(0.05)
            app_input.send_text("\t\t\t\t\r")
            for _ in range(100):
                if tui.state.error:
                    break
                await asyncio.sleep(0.01)

            assert (
                tui.state.error == "Cannot start extraction: No such file or directory"
            )
            assert tui.job.running is False
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_keyboard_confirms_running_job_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input"
    source.touch()
    script = "import time; print('ready', flush=True); time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )

    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.source.text = str(source)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)
            app_input.send_text("\r")
            await asyncio.sleep(0.05)
            app_input.send_text("\t\t\t\t")
            await asyncio.sleep(0.05)
            app_input.send_text("\r")
            while not tui.state.log:
                await asyncio.sleep(0.01)

            app_input.send_text("\x03")
            await asyncio.sleep(0.05)
            assert tui.state.cancel_pending is True
            assert tui.job.running is True
            app_input.send_text("\x03")
            while tui.job.running:
                await asyncio.sleep(0.01)

            assert tui.state.error == "Extraction cancelled"
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_help_toggles_without_mouse_input() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("?")
            await asyncio.sleep(0.05)
            assert tui.state.help_visible is True
            assert tui.application.layout.has_focus(tui.help_control)

            app_input.send_text("?")
            await asyncio.sleep(0.05)
            assert tui.state.help_visible is False
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_question_mark_remains_typable_in_paths() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)
            app_input.send_text("\r?")
            await asyncio.sleep(0.05)

            assert tui.source.text == "?"
            assert tui.state.help_visible is False
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_slash_focuses_search_and_escape_returns_to_results() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.state.output = RecoveryOutput(
                Path("out"),
                (SchemaRecord("Alpha.proto", "pkg", {}),),
                (),
            )
            tui.state.show(Screen.RESULTS)
            tui.application.layout.focus(tui.results_control)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("/alpha")
            await asyncio.sleep(0.05)
            assert tui.application.layout.has_focus(tui.search)
            assert tui.state.query == "alpha"
            app_input.send_text("\x1b\r")
            await asyncio.sleep(0.05)
            assert tui.application.layout.has_focus(tui.results_control)
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_home_up_arrow_wraps_selection() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("\x1b[A")
            await asyncio.sleep(0.05)
            assert tui.home_selection == 2
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_home_q_key_exits_application() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("q")
            await asyncio.sleep(0.05)
            await task

    asyncio.run(exercise())


def test_home_quit_selection_exits_application() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("\x1b[B\x1b[B\r")
            await asyncio.sleep(0.05)
            await task

    asyncio.run(exercise())


def test_results_up_arrow_moves_selection() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            schemas = (
                SchemaRecord("Alpha.proto", "pkg", {}),
                SchemaRecord("Beta.proto", "pkg", {}),
            )
            tui.state.output = RecoveryOutput(Path("out"), schemas, ())
            tui.state.selected = 1
            tui.state.show(Screen.RESULTS)
            tui.application.layout.focus(tui.results_control)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("\x1b[A")
            await asyncio.sleep(0.05)
            assert tui.state.selected == 0
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_shift_tab_moves_focus_backward() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.state.show(Screen.SETUP)
            tui.application.layout.focus(tui.output)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("\x1b[Z")
            await asyncio.sleep(0.05)
            assert tui.application.layout.has_focus(tui.source)
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_escape_closes_help_and_restores_home_focus() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("?")
            await asyncio.sleep(0.05)
            assert tui.state.help_visible is True

            app_input.send_text("\x1b")
            await asyncio.sleep(0.6)
            assert tui.state.help_visible is False
            assert tui.application.layout.has_focus(tui.body)
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_cancel_key_returns_home_when_job_finished() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.state.show(Screen.RUNNING)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("\x03")
            await asyncio.sleep(0.05)
            assert tui.state.screen is Screen.HOME
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_escape_returns_home_when_job_finished() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.state.show(Screen.RUNNING)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("\x1b")
            await asyncio.sleep(0.6)
            assert tui.state.screen is Screen.HOME
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_escape_clears_cancel_pending_while_job_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input"
    source.touch()
    script = "import time; print('ready', flush=True); time.sleep(30)"
    monkeypatch.setattr(
        ExtractionRequest,
        "command",
        lambda self: (sys.executable, "-c", script),
    )

    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            tui.source.text = str(source)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)
            app_input.send_text("\r")
            await asyncio.sleep(0.05)
            app_input.send_text("\t\t\t\t\r")
            while not tui.state.log:
                await asyncio.sleep(0.01)

            app_input.send_text("\x03")
            await asyncio.sleep(0.05)
            assert tui.state.cancel_pending is True

            app_input.send_text("\x1b")
            await asyncio.sleep(0.6)
            assert tui.state.cancel_pending is False
            assert tui.state.screen is Screen.RUNNING

            await tui.job.cancel()
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_escape_from_setup_returns_home() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            tui = TuiApplication(app_input, DummyOutput())
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            app_input.send_text("\r")
            await asyncio.sleep(0.05)
            after_enter = tui.state.screen
            assert after_enter is Screen.SETUP

            app_input.send_text("\x1b")
            await asyncio.sleep(0.6)
            after_escape = tui.state.screen
            assert after_escape is Screen.HOME
            tui.application.exit()
            await task

    asyncio.run(exercise())


def test_large_results_survive_rapid_input_and_tiny_resize() -> None:
    async def exercise() -> None:
        with create_pipe_input() as app_input:
            output = ResizableOutput()
            tui = TuiApplication(app_input, output)
            schemas = tuple(
                SchemaRecord(f"Schema{index}.proto", "pkg", {}) for index in range(646)
            )
            tui.state.output = RecoveryOutput(Path("out"), schemas, ())
            tui.state.show(Screen.RESULTS)
            tui.application.layout.focus(tui.results_control)
            task = asyncio.create_task(tui.application.run_async())
            await asyncio.sleep(0.05)

            output.size = Size(rows=10, columns=20)
            tui.application.invalidate()
            app_input.send_text("\x1b[B" * 1000)
            await asyncio.sleep(0.2)

            assert tui.state.selected == 645
            assert tui.results_control.create_content(20, 10).cursor_position.y == 646
            tui.application.exit()
            await task

    asyncio.run(exercise())
