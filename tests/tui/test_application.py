import asyncio

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from protoloom.tui.application import TuiApplication
from protoloom.tui.state import Screen


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
