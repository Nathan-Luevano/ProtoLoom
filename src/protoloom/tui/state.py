from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from protoloom.tui.results import RecoveryOutput, SchemaRecord


class Screen(StrEnum):
    HOME = "home"
    OPEN = "open output"
    SETUP = "setup"
    RUNNING = "running"
    RESULTS = "results"


@dataclass(slots=True)
class AppState:
    screen: Screen = Screen.HOME
    output: RecoveryOutput | None = None
    error: str | None = None
    help_visible: bool = False
    cancel_pending: bool = False
    query: str = ""
    selected: int = 0
    log: deque[str] = field(default_factory=lambda: deque(maxlen=1000))

    def show(self, screen: Screen) -> None:
        self.screen = screen
        self.error = None

    def fail(self, message: str) -> None:
        self.error = message

    def append_log(self, line: str) -> None:
        self.log.append(line)

    def visible_schemas(self) -> tuple[SchemaRecord, ...]:
        if self.output is None:
            return ()
        query = self.query.casefold().strip()
        if not query:
            return self.output.schemas
        return tuple(
            item
            for item in self.output.schemas
            if query in item.name.casefold() or query in item.package.casefold()
        )

    def clamp_selection(self) -> None:
        self.selected = max(0, min(self.selected, len(self.visible_schemas()) - 1))
