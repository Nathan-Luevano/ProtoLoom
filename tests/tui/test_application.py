import asyncio
import json
from pathlib import Path

from google.protobuf.descriptor_pb2 import FileDescriptorProto, FileDescriptorSet
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from protoloom.tui.application import TuiApplication
from protoloom.tui.results import RecoveryOutput, SchemaRecord
from protoloom.tui.state import Screen


class ResizableOutput(DummyOutput):
    size = Size(rows=24, columns=80)

    def get_size(self) -> Size:
        return self.size


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
