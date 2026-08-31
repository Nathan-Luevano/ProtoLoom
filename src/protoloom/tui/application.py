from prompt_toolkit import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from protoloom.tui.state import AppState, Screen


class TuiApplication:
    def __init__(self) -> None:
        self.state = AppState()
        self.home_selection = 0
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


def run() -> None:
    TuiApplication().application.run()
