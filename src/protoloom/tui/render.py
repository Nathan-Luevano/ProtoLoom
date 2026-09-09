from io import StringIO

from rich.console import Console, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.tree import Tree

from protoloom.tui.results import RecoveryOutput, SchemaRecord

MAX_SCHEMA_RENDER_ITEMS = 10_000


class _RenderBudget:
    def __init__(self, remaining: int) -> None:
        self.remaining = remaining
        self.omitted = False

    def consume(self, parent: Tree) -> bool:
        if self.remaining == 0:
            if not self.omitted:
                parent.add("Further items omitted")
                self.omitted = True
            return False
        self.remaining -= 1
        return True


def _render(item: RenderableType, width: int) -> str:
    stream = StringIO()
    console = Console(
        file=stream,
        width=max(20, width),
        force_terminal=True,
        color_system="standard",
    )
    console.print(item)
    return stream.getvalue()


def render_summary(output: RecoveryOutput, width: int = 80) -> str:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Directory", str(output.root))
    table.add_row("Schemas", str(len(output.schemas)))
    table.add_row(
        "Bail-outs", str(output.bailouts) if output.bailouts is not None else "n/a"
    )
    table.add_row("Conflicts", str(len(output.conflicts)))
    return _render(table, width)


def _objects(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _add_messages(
    parent: Tree, value: object, budget: _RenderBudget, depth: int = 0
) -> None:
    if depth == 16:
        parent.add("Further nesting omitted")
        return
    for message in _objects(value):
        if not budget.consume(parent):
            return
        name = message.get("name", "unnamed")
        confidence = message.get("confidence", "unknown")
        branch = parent.add(
            f"[bold]{escape(str(name))}[/bold] {escape(f'[{confidence}]')}"
        )
        for field in _objects(message.get("fields")):
            if not budget.consume(branch):
                return
            field_confidence = field.get("confidence", "unknown")
            branch.add(
                escape(
                    f"{field.get('number', '?')}  {field.get('name', 'unnamed')}: "
                    f"{field.get('type_name', 'unknown')} [{field_confidence}]"
                )
            )
        _add_messages(branch, message.get("messages"), budget, depth + 1)


def render_schema(
    schema: SchemaRecord, width: int = 80, max_items: int = MAX_SCHEMA_RENDER_ITEMS
) -> str:
    if max_items <= 0:
        raise ValueError("schema render limit must be positive")
    budget = _RenderBudget(max_items)
    root = Tree(f"[bold]{escape(schema.name)}[/bold]")
    root.add(f"Package: {escape(schema.package or '(none)')}")
    messages = root.add("Messages")
    _add_messages(messages, schema.data.get("messages"), budget)
    enums = root.add("Enums")
    for enum in _objects(schema.data.get("enums")):
        if not budget.consume(enums):
            break
        branch = enums.add(f"[bold]{escape(str(enum.get('name', 'unnamed')))}[/bold]")
        for value in _objects(enum.get("values")):
            if not budget.consume(branch):
                break
            branch.add(
                escape(f"{value.get('name', 'unnamed')} = {value.get('number', '?')}")
            )
    return _render(root, width)
