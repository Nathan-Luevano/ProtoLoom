from protoloom.model import Confidence, Message, RecoveredSchema

MAX_REPORT_ITEMS = 1_000_000
MAX_REPORT_DEPTH = 10_000
MAX_REPORT_OUTPUT_BYTES = 64 * 1024 * 1024


def _messages(schema: RecoveredSchema, max_items: int, max_depth: int) -> list[Message]:
    pending = [(message, 1) for message in schema.messages]
    result: list[Message] = []
    while pending:
        message, depth = pending.pop()
        if depth > max_depth:
            raise ValueError(f"report exceeds message depth {max_depth}")
        if len(result) >= max_items:
            raise ValueError(f"report exceeds {max_items} messages")
        result.append(message)
        pending.extend((child, depth + 1) for child in message.messages)
    return result


def emit_report(
    schemas: list[RecoveredSchema],
    bailouts: list[str],
    *,
    max_items: int = MAX_REPORT_ITEMS,
    max_depth: int = MAX_REPORT_DEPTH,
    max_bytes: int = MAX_REPORT_OUTPUT_BYTES,
) -> str:
    if min(max_items, max_depth, max_bytes) <= 0:
        raise ValueError("report limits must be positive")
    if len(schemas) > max_items:
        raise ValueError(f"report exceeds {max_items} schemas")
    if len(bailouts) > max_items:
        raise ValueError(f"report exceeds {max_items} bailouts")
    field_counts = {confidence: 0 for confidence in Confidence}
    message_count = 0
    field_count = 0
    for schema in schemas:
        messages = _messages(schema, max_items - message_count, max_depth)
        message_count += len(messages)
        if message_count > max_items:
            raise ValueError(f"report exceeds {max_items} messages")
        for message in messages:
            for field in message.fields:
                field_counts[field.confidence] += 1
                field_count += 1
                if field_count > max_items:
                    raise ValueError(f"report exceeds {max_items} fields")
    lines = ["# PROTOLOOM recovery report", ""]
    lines.append(f"Recovered {len(schemas)} files and {message_count} messages.")
    lines.extend(("", "## Field confidence", ""))
    for confidence in Confidence:
        lines.append(f"- {confidence.value}: {field_counts[confidence]}")
    lines.extend(("", f"Bail-outs: {len(bailouts)}"))
    for reason in bailouts:
        lines.append(f"- {_single_line(reason)}")
    result = "\n".join(lines) + "\n"
    if len(result.encode("utf-8")) > max_bytes:
        raise ValueError(f"report exceeds {max_bytes} bytes")
    return result


def _single_line(value: str) -> str:
    return " ".join(value.splitlines())
