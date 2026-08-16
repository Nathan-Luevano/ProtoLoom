from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from html import escape
from typing import Protocol

from protoloom.model import Confidence, Field, Message, RecoveredSchema


class ConflictLike(Protocol):
    @property
    def path(self) -> str: ...

    @property
    def attribute(self) -> str: ...

    @property
    def kept(self) -> str: ...

    @property
    def rejected(self) -> str: ...


def emit_dashboard(
    schemas: list[RecoveredSchema],
    conflicts: Iterable[Mapping[str, object] | ConflictLike] = (),
) -> str:
    messages = [message for schema in schemas for message in _messages(schema.messages)]
    fields = [field for message in messages for field in message.fields]
    counts = Counter(field.confidence for field in fields)
    conflict_rows = list(conflicts)
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>PROTOLOOM recovery dashboard</title>",
        f"<style>{_CSS}</style></head><body>",
        "<main><header><p class=eyebrow>PROTOLOOM</p>",
        "<h1>Recovery dashboard</h1></header>",
        '<section class="metrics" aria-label="Recovery summary">',
        _metric("Schemas", len(schemas)),
        _metric("Messages", len(messages)),
        _metric("Fields", len(fields)),
        _metric("Conflicts", len(conflict_rows)),
        "</section>",
        "<section><h2>Confidence</h2><div class=confidence>",
    ]
    total = len(fields)
    for confidence in Confidence:
        count = counts[confidence]
        percent = count * 100 / total if total else 0.0
        parts.append(
            f"<div><span>{escape(confidence.value)}</span><b>{count}</b>"
            f'<meter min="0" max="100" value="{percent:.2f}">'
            f"{percent:.1f}%</meter></div>"
        )
    parts.extend(("</div></section>", "<section><h2>Recovered fields</h2>"))
    if fields:
        parts.append(
            "<div class=scroll><table><thead><tr><th>Schema</th><th>Message</th>"
            "<th>Field</th><th>Number</th><th>Type</th><th>Confidence</th>"
            "</tr></thead><tbody>"
        )
        for schema in schemas:
            for qualified, field in _qualified_fields(schema.messages):
                parts.append(
                    f"<tr><td>{escape(schema.name)}</td><td>{escape(qualified)}</td>"
                    f"<td>{escape(field.name)}</td><td>{field.number}</td>"
                    f"<td><code>{escape(field.type_name)}</code></td>"
                    f'<td><span class="pill {field.confidence.value}">'
                    f"{escape(field.confidence.value)}</span></td></tr>"
                )
        parts.append("</tbody></table></div>")
    else:
        parts.append('<p class="empty">No fields recovered.</p>')
    parts.extend(("</section>", "<section><h2>Conflicts</h2>"))
    if conflict_rows:
        parts.append("<ul class=conflicts>")
        for conflict in conflict_rows:
            path = escape(_conflict_value(conflict, "path", "unknown"))
            attribute = escape(_conflict_value(conflict, "attribute", "value"))
            kept = escape(_conflict_value(conflict, "kept", ""))
            rejected = escape(_conflict_value(conflict, "rejected", ""))
            parts.append(
                f"<li><b>{path}</b> · {attribute}: kept <code>{kept}</code>, "
                f"rejected <code>{rejected}</code></li>"
            )
        parts.append("</ul>")
    else:
        parts.append('<p class="empty">No conflicts recorded.</p>')
    parts.append("</section></main></body></html>\n")
    return "".join(parts)


def _messages(items: list[Message]) -> Iterable[Message]:
    for item in items:
        yield item
        yield from _messages(item.messages)


def _qualified_fields(
    items: list[Message], prefix: str = ""
) -> Iterable[tuple[str, Field]]:
    for item in items:
        qualified = f"{prefix}.{item.name}" if prefix else item.name
        for field in item.fields:
            yield qualified, field
        yield from _qualified_fields(item.messages, qualified)


def _metric(label: str, value: int) -> str:
    return f"<article><strong>{value}</strong><span>{escape(label)}</span></article>"


def _conflict_value(
    conflict: Mapping[str, object] | ConflictLike, name: str, default: str
) -> str:
    if isinstance(conflict, Mapping):
        return str(conflict.get(name, default))
    return str(getattr(conflict, name))


_CSS = """
:root{color-scheme:dark;--bg:#0b1020;--panel:#151c31;--ink:#eef2ff;
--muted:#9da9c7;--line:#293452;--accent:#78e3c2}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(circle at top,#17213b,var(--bg) 45%);
color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{width:min(1120px,calc(100% - 32px));margin:48px auto 80px}
header{margin-bottom:28px}
.eyebrow{color:var(--accent);font-weight:800;letter-spacing:.16em;margin:0}
h1{font-size:clamp(2.2rem,7vw,4.5rem);line-height:1;margin:.15em 0}
h2{font-size:1.1rem;margin:0 0 16px}
section{background:var(--panel);border:1px solid var(--line);border-radius:16px;
margin:16px 0;padding:22px}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;
background:none;border:0;padding:0}
.metrics article{background:var(--panel);border:1px solid var(--line);
border-radius:14px;padding:18px}
.metrics strong{display:block;font-size:2rem}
.metrics span,.confidence span,.empty{color:var(--muted)}
.confidence{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
.confidence b{display:block;font-size:1.5rem}
.confidence meter{width:100%;accent-color:var(--accent)}
.scroll{overflow:auto}
table{border-collapse:collapse;width:100%;text-align:left}
th,td{border-bottom:1px solid var(--line);padding:11px 10px;white-space:nowrap}
th{color:var(--muted);font-size:.75rem;text-transform:uppercase;
letter-spacing:.08em}
.pill{border:1px solid var(--line);border-radius:999px;padding:3px 8px}
.certain{color:#78e3c2}.high{color:#8ac5ff}.medium{color:#ffd479}
.speculative{color:#ff9aa9}.conflicts{padding-left:20px}
.conflicts li{margin:8px 0}code{color:#d9e2ff}
@media(max-width:700px){.metrics,.confidence{grid-template-columns:repeat(2,1fr)}
main{margin-top:24px}}
""".strip()
