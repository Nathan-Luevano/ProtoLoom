from prompt_toolkit import Application
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from protoloom.tui.state import AppState, Screen


class TuiApplication:
    def __init__(self) -> None:
        self.state = AppState()
        self.home_selection = 0
        self.bindings = KeyBindings()
        self._bind_keys()
        self.body = FormattedTextControl(self._body_text, focusable=True)
        root = HSplit(
            [
                Window(FormattedTextControl(self._header_text), height=1),
                Window(self.body, wrap_lines=True),
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
        return " ↑/↓ move  Enter select  ? help  q quit "

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
            elif self.home_selection == 1:
                self.state.show(Screen.RESULTS)
            else:
                event.app.exit()

        @self.bindings.add("q", filter=on_home)
        def quit_application(event: KeyPressEvent) -> None:
            event.app.exit()


def run() -> None:
    TuiApplication().application.run()
