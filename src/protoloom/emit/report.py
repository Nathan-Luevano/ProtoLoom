from protoloom.model import Confidence, RecoveredSchema


def _message_count(schema: RecoveredSchema) -> int:
    pending = list(schema.messages)
    count = 0
    while pending:
        message = pending.pop()
        count += 1
        pending.extend(message.messages)
    return count


def emit_report(schemas: list[RecoveredSchema], bailouts: list[str]) -> str:
    field_counts = {confidence: 0 for confidence in Confidence}
    for schema in schemas:
        pending = list(schema.messages)
        while pending:
            message = pending.pop()
            pending.extend(message.messages)
            for field in message.fields:
                field_counts[field.confidence] += 1
    lines = ["# PROTOLOOM recovery report", ""]
    message_count = sum(map(_message_count, schemas))
    lines.append(f"Recovered {len(schemas)} files and {message_count} messages.")
    lines.extend(("", "## Field confidence", ""))
    for confidence in Confidence:
        lines.append(f"- {confidence.value}: {field_counts[confidence]}")
    lines.extend(("", f"Bail-outs: {len(bailouts)}"))
    for reason in bailouts:
        lines.append(f"- {reason}")
    return "\n".join(lines) + "\n"
