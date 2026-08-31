import asyncio
from pathlib import Path

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

            output.size = Size(rows=3, columns=10)
            tui.application.invalidate()
            app_input.send_text("\x1b[B" * 1000)
            await asyncio.sleep(0.2)

            assert tui.state.selected == 645
            tui.application.exit()
            await task

    asyncio.run(exercise())
