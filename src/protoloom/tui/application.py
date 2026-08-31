from pathlib import Path
from time import monotonic

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples, to_formatted_text
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import DynamicContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import Container
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import Output
from prompt_toolkit.widgets import Button, Checkbox, TextArea

from protoloom.tui.jobs import ExtractionJob, ExtractionRequest
from protoloom.tui.render import render_schema, render_summary
from protoloom.tui.results import OutputError, load_output
from protoloom.tui.state import AppState, Screen


class TuiApplication:
    def __init__(
        self, app_input: Input | None = None, app_output: Output | None = None
    ) -> None:
        self.state = AppState()
        self.home_selection = 0
        self.bindings = KeyBindings()
        self.body = FormattedTextControl(self._body_text, focusable=True)
        self.home = Window(self.body, wrap_lines=True)
        self.source = TextArea(height=1, prompt=" Input: ", multiline=False)
        self.output = TextArea(height=1, prompt=" Output: ", multiline=False)
        self.heuristic = Checkbox("Allow heuristic lite recovery")
        self.jadx = Checkbox("Retain jadx context")
        self.output.text = "out"
        self.setup = HSplit(
            [
                self.source,
                self.output,
                self.heuristic,
                self.jadx,
                VSplit(
                    [
                        Button("Start", handler=self._start),
                        Button("Back", handler=self._show_home),
                    ]
                ),
            ],
            padding=1,
        )
        self.open_path = TextArea(height=1, prompt=" Directory: ", multiline=False)
        self.open_form = HSplit(
            [
                self.open_path,
                VSplit(
                    [
                        Button("Open", handler=self._open_output),
                        Button("Back", handler=self._show_home),
                    ]
                ),
            ],
            padding=1,
        )
        self.running = Window(FormattedTextControl(self._running_text), wrap_lines=True)
        self.help_control = FormattedTextControl(self._help_text, focusable=True)
        self.help = Window(self.help_control, wrap_lines=True)
        self.results_control = FormattedTextControl(self._results_text, focusable=True)
        self.detail_control = FormattedTextControl(self._detail_text)
        self.search = TextArea(height=1, prompt=" Search: ", multiline=False)
        self.search.buffer.on_text_changed += self._search_changed
        result_columns = VSplit(
            [
                Window(self.results_control, width=40, wrap_lines=True),
                Window(self.detail_control, wrap_lines=True),
            ]
        )
        self.results = HSplit([self.search, result_columns])
        self.job = ExtractionJob()
        self._bind_keys()
        self.screen = DynamicContainer(self._screen_container)
        root = HSplit(
            [
                Window(FormattedTextControl(self._header_text), height=1),
                self.screen,
                Window(FormattedTextControl(self._footer_text), height=1),
            ]
        )
        self.application: Application[None] = Application(
            layout=Layout(root, focused_element=self.body),
            key_bindings=self.bindings,
            full_screen=True,
            mouse_support=False,
            refresh_interval=1,
            input=app_input,
            output=app_output,
        )

    def _header_text(self) -> str:
        error = f" — {self.state.error}" if self.state.error else ""
        return f" ProtoLoom — {self.state.screen.value}{error} "

    def _body_text(self) -> StyleAndTextTuples:
        if self.state.screen is not Screen.HOME:
            return [("", "\n  Loading…")]
        items = ("New extraction", "Open existing output", "Quit")
        result: StyleAndTextTuples = [("", "\n")]
        for index, label in enumerate(items):
            marker = ">" if index == self.home_selection else " "
            style = "reverse" if index == self.home_selection else ""
            result.append((style, f"  {marker} {label}\n"))
        return result

    def _footer_text(self) -> str:
        if self.state.screen in {Screen.SETUP, Screen.OPEN}:
            return " Tab/Shift-Tab move  Space toggle  Enter activate  Esc back "
        if self.state.screen is Screen.RESULTS:
            return " ↑/↓ move  / search  Esc back  ? help "
        if self.state.screen is Screen.RUNNING:
            return " Ctrl-C cancel  ? help "
        return " ↑/↓ move  Enter select  ? help  q quit "

    def _screen_container(self) -> Container:
        if self.state.help_visible:
            return self.help
        if self.state.screen is Screen.HOME:
            return self.home
        if self.state.screen is Screen.SETUP:
            return self.setup
        if self.state.screen is Screen.OPEN:
            return self.open_form
        if self.state.screen is Screen.RUNNING:
            return self.running
        return self.results

    def _show_home(self) -> None:
        self.state.show(Screen.HOME)
        self.application.layout.focus(self.body)

    def _restore_focus(self) -> None:
        if self.state.screen is Screen.OPEN:
            self.application.layout.focus(self.open_path)
        elif self.state.screen is Screen.SETUP:
            self.application.layout.focus(self.source)
        elif self.state.screen is Screen.RESULTS:
            self.application.layout.focus(self.results_control)
        else:
            self.application.layout.focus(self.body)

    def _running_text(self) -> str:
        elapsed = monotonic() - self.state.started_at if self.state.started_at else 0
        status = (
            f"\n  {self.state.error}\n"
            if self.state.error
            else f"\n  Extracting… {elapsed:.0f}s\n"
        )
        if self.state.cancel_pending:
            status = "\n  Cancel extraction? Press Ctrl-C again; Esc keeps running.\n"
        return status + "\n".join(f"  {line}" for line in self.state.log)

    def _help_text(self) -> str:
        return (
            "\n  Keyboard help\n\n"
            "  ↑/↓          Move selection\n"
            "  Enter        Select or activate\n"
            "  Tab/Shift-Tab Move between form controls\n"
            "  /            Search schemas\n"
            "  Esc          Back or close\n"
            "  Ctrl-C       Cancel a running extraction\n"
            "  ?            Open or close this help\n"
            "  q            Quit from Home\n"
        )

    def _open_output(self) -> None:
        try:
            self.state.output = load_output(Path(self.open_path.text).expanduser())
        except OutputError as error:
            self.state.fail(str(error))
            return
        self.state.show(Screen.RESULTS)
        self.application.layout.focus(self.results_control)

    def _results_text(self) -> StyleAndTextTuples:
        output = self.state.output
        if output is None:
            return [("", "\n  Choose an output directory from Home.")]
        result = list(to_formatted_text(ANSI(render_summary(output))))
        result.append(("bold", "\n Schemas\n"))
        for index, schema in enumerate(self.state.visible_schemas()):
            marker = ">" if index == self.state.selected else " "
            style = "reverse" if index == self.state.selected else ""
            result.append((style, f" {marker} {schema.name}\n"))
        return result

    def _detail_text(self) -> ANSI | str:
        schemas = self.state.visible_schemas()
        if not schemas:
            return "\n No matching schemas"
        self.state.clamp_selection()
        return ANSI(render_schema(schemas[self.state.selected]))

    def _search_changed(self, buffer: Buffer) -> None:
        self.state.query = buffer.text
        self.state.selected = 0
        self.application.invalidate()

    def _start(self) -> None:
        source = Path(self.source.text).expanduser()
        output = Path(self.output.text).expanduser()
        if not source.is_file():
            self.state.fail(f"Input file does not exist: {source}")
            return
        self.state.log.clear()
        self.state.show(Screen.RUNNING)
        self.state.started_at = monotonic()
        request = ExtractionRequest(
            source, output, self.heuristic.checked, self.jadx.checked
        )
        self.application.create_background_task(self._run_job(request))

    async def _run_job(self, request: ExtractionRequest) -> None:
        def append(line: str) -> None:
            self.state.append_log(line)
            self.application.invalidate()

        result = await self.job.run(request, append)
        self.state.cancel_pending = False
        if result.cancelled:
            self.state.fail("Extraction cancelled")
        elif result.returncode:
            self.state.fail(f"Extraction failed with status {result.returncode}")
        else:
            try:
                self.state.output = load_output(request.output)
            except OutputError as error:
                self.state.fail(str(error))
            else:
                self.state.show(Screen.RESULTS)
                self.application.layout.focus(self.results_control)
        self.application.invalidate()

    def _bind_keys(self) -> None:
        help_visible = Condition(lambda: self.state.help_visible)
        on_home = Condition(
            lambda: self.state.screen is Screen.HOME and not self.state.help_visible
        )
        on_results = Condition(
            lambda: self.state.screen is Screen.RESULTS and not self.state.help_visible
        )
        on_running = Condition(
            lambda: self.state.screen is Screen.RUNNING and not self.state.help_visible
        )

        @self.bindings.add("up", filter=on_home)
        def move_up(event: KeyPressEvent) -> None:
            self.home_selection = (self.home_selection - 1) % 3
            event.app.invalidate()

        @self.bindings.add("down", filter=on_home)
        def move_down(event: KeyPressEvent) -> None:
            self.home_selection = (self.home_selection + 1) % 3
            event.app.invalidate()

        @self.bindings.add("enter", filter=on_home)
        def choose(event: KeyPressEvent) -> None:
            if self.home_selection == 0:
                self.state.show(Screen.SETUP)
                event.app.layout.focus(self.source)
            elif self.home_selection == 1:
                self.state.show(Screen.OPEN)
                event.app.layout.focus(self.open_path)
            else:
                event.app.exit()

        @self.bindings.add("q", filter=on_home)
        def quit_application(event: KeyPressEvent) -> None:
            event.app.exit()

        @self.bindings.add("up", filter=on_results & ~has_focus(self.search))
        def previous_schema(event: KeyPressEvent) -> None:
            self.state.selected -= 1
            self.state.clamp_selection()
            event.app.invalidate()

        @self.bindings.add("down", filter=on_results & ~has_focus(self.search))
        def next_schema(event: KeyPressEvent) -> None:
            self.state.selected += 1
            self.state.clamp_selection()
            event.app.invalidate()

        @self.bindings.add("/", filter=on_results & ~has_focus(self.search))
        def focus_search(event: KeyPressEvent) -> None:
            event.app.layout.focus(self.search)

        @self.bindings.add("escape", filter=has_focus(self.search))
        def leave_search(event: KeyPressEvent) -> None:
            event.app.layout.focus(self.results_control)

        @self.bindings.add("tab", filter=~help_visible)
        def focus_next(event: KeyPressEvent) -> None:
            event.app.layout.focus_next()

        @self.bindings.add("s-tab", filter=~help_visible)
        def focus_previous(event: KeyPressEvent) -> None:
            event.app.layout.focus_previous()

        @self.bindings.add("?", eager=True)
        def toggle_help(event: KeyPressEvent) -> None:
            self.state.help_visible = not self.state.help_visible
            if self.state.help_visible:
                event.app.layout.focus(self.help_control)
            else:
                self._restore_focus()
            event.app.invalidate()

        @self.bindings.add("escape", filter=help_visible, eager=True)
        def close_help(event: KeyPressEvent) -> None:
            self.state.help_visible = False
            self._restore_focus()

        @self.bindings.add("c-c", filter=on_running)
        def cancel_job(event: KeyPressEvent) -> None:
            if not self.job.running:
                self._show_home()
            elif self.state.cancel_pending:
                event.app.create_background_task(self.job.cancel())
            else:
                self.state.cancel_pending = True
                event.app.invalidate()

        @self.bindings.add("escape", filter=on_running)
        def keep_running(event: KeyPressEvent) -> None:
            self.state.cancel_pending = False
            event.app.invalidate()

        @self.bindings.add(
            "escape",
            filter=~on_home & ~on_running & ~has_focus(self.search) & ~help_visible,
        )
        def go_back(event: KeyPressEvent) -> None:
            self._show_home()


def run() -> None:
    TuiApplication().application.run()
