from io import StringIO

from rich.console import Console, RenderableType
from rich.table import Table
from rich.tree import Tree

from protoloom.tui.results import RecoveryOutput, SchemaRecord


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
    table.add_row("Conflicts", str(len(output.conflicts)))
    return _render(table, width)


def _objects(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, dict))


def _add_messages(parent: Tree, value: object) -> None:
    for message in _objects(value):
        name = message.get("name", "unnamed")
        confidence = message.get("confidence", "unknown")
        branch = parent.add(f"[bold]{name}[/bold] [{confidence}]")
        for field in _objects(message.get("fields")):
            field_confidence = field.get("confidence", "unknown")
            branch.add(
                f"{field.get('number', '?')}  {field.get('name', 'unnamed')}: "
                f"{field.get('type_name', 'unknown')} [{field_confidence}]"
            )
        _add_messages(branch, message.get("messages"))


def render_schema(schema: SchemaRecord, width: int = 80) -> str:
    root = Tree(f"[bold]{schema.name}[/bold]")
    root.add(f"Package: {schema.package or '(none)'}")
    messages = root.add("Messages")
    _add_messages(messages, schema.data.get("messages"))
    enums = root.add("Enums")
    for enum in _objects(schema.data.get("enums")):
        branch = enums.add(f"[bold]{enum.get('name', 'unnamed')}[/bold]")
        for value in _objects(enum.get("values")):
            branch.add(f"{value.get('name', 'unnamed')} = {value.get('number', '?')}")
    return _render(root, width)
