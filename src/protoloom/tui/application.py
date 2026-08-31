from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import DynamicContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Button, Checkbox, TextArea

from protoloom.tui.state import AppState, Screen


class TuiApplication:
    def __init__(self) -> None:
        self.state = AppState()
        self.home_selection = 0
        self.bindings = KeyBindings()
        self._bind_keys()
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
                        Button("Start", handler=self._start_placeholder),
                        Button("Back", handler=self._show_home),
                    ]
                ),
            ],
            padding=1,
        )
        self.running = Window(FormattedTextControl(self._running_text), wrap_lines=True)
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
        )

    def _header_text(self) -> str:
        return f" ProtoLoom — {self.state.screen.value} "

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
        if self.state.screen is Screen.SETUP:
            return " Tab/Shift-Tab move  Space toggle  Enter activate  Esc back "
        return " ↑/↓ move  Enter select  ? help  q quit "

    def _screen_container(self) -> Window | HSplit:
        if self.state.screen is Screen.HOME:
            return self.home
        if self.state.screen is Screen.SETUP:
            return self.setup
        return self.running

    def _show_home(self) -> None:
        self.state.show(Screen.HOME)
        self.application.layout.focus(self.body)

    def _running_text(self) -> str:
        status = (
            f"\n  {self.state.error}\n" if self.state.error else "\n  Extracting…\n"
        )
        return status + "\n".join(f"  {line}" for line in self.state.log)

    def _start_placeholder(self) -> None:
        self.state.fail("Extraction runner is not connected yet")

    def _bind_keys(self) -> None:
        on_home = Condition(lambda: self.state.screen is Screen.HOME)

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
                self.state.show(Screen.RESULTS)
            else:
                event.app.exit()

        @self.bindings.add("q", filter=on_home)
        def quit_application(event: KeyPressEvent) -> None:
            event.app.exit()

        @self.bindings.add("tab")
        def focus_next(event: KeyPressEvent) -> None:
            event.app.layout.focus_next()

        @self.bindings.add("s-tab")
        def focus_previous(event: KeyPressEvent) -> None:
            event.app.layout.focus_previous()

        @self.bindings.add("escape", filter=~on_home)
        def go_back(event: KeyPressEvent) -> None:
            self._show_home()


def run() -> None:
    TuiApplication().application.run()
