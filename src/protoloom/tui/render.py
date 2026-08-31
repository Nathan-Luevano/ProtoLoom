from io import StringIO

from rich.console import Console, RenderableType
from rich.table import Table
from rich.text import Text

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


def render_schema(schema: SchemaRecord, width: int = 80) -> str:
    lines = Text()
    lines.append(schema.name, style="bold")
    lines.append(f"\nPackage: {schema.package or '(none)'}")
    for key, label in (("messages", "Messages"), ("enums", "Enums")):
        value = schema.data.get(key, [])
        count = len(value) if isinstance(value, list) else 0
        lines.append(f"\n{label}: {count}")
    return _render(lines, width)
