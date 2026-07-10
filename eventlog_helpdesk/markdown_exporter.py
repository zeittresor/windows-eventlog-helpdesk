from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from .models import EventRecord, level_rank

ProgressCallback = Callable[[int, int], None]
StatusCallback = Callable[[str], None]


def _escape_table(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _safe_fence(text: str) -> str:
    return text.replace("```", "` ` `")


def sanitize_filename(value: str, fallback: str = "EventLog") -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", value).strip(" ._")
    value = re.sub(r"\s+", "_", value)
    return value[:100] or fallback


def aggregate_counts(events: Iterable[EventRecord]) -> dict[str, Counter[str]]:
    event_list = list(events)
    return {
        "levels": Counter((event.level or "Unknown") for event in event_list),
        "providers": Counter((event.provider or "Unknown") for event in event_list),
        "event_ids": Counter((event.event_id or "Unknown") for event in event_list),
        "channels": Counter((event.channel or event.source or "Unknown") for event in event_list),
    }


def summary_markdown(events: list[EventRecord], title: str, source: str) -> str:
    counts = aggregate_counts(events)
    critical = sum(1 for e in events if level_rank(e) == 1)
    errors = sum(1 for e in events if level_rank(e) == 2)
    warnings = sum(1 for e in events if level_rank(e) == 3)
    info = sum(1 for e in events if level_rank(e) == 4)
    lines = [
        f"# {title}",
        "",
        f"- **Source:** `{source}`",
        f"- **Generated:** {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}",
        f"- **Events:** {len(events):,}",
        f"- **Critical:** {critical:,}",
        f"- **Errors:** {errors:,}",
        f"- **Warnings:** {warnings:,}",
        f"- **Information:** {info:,}",
        "",
        "## Top providers",
        "",
        "| Provider | Count |",
        "|---|---:|",
    ]
    lines.extend(f"| {_escape_table(name)} | {count:,} |" for name, count in counts["providers"].most_common(15))
    lines.extend(["", "## Top event IDs", "", "| Event ID | Count |", "|---|---:|"])
    lines.extend(f"| {_escape_table(name)} | {count:,} |" for name, count in counts["event_ids"].most_common(15))
    return "\n".join(lines)


def event_to_complete_markdown(event: EventRecord, index: int) -> str:
    out = StringIO()
    heading_parts = [f"Event {index}"]
    if event.timestamp:
        heading_parts.append(event.timestamp)
    if event.level:
        heading_parts.append(event.level)
    if event.provider:
        heading_parts.append(event.provider)
    if event.event_id:
        heading_parts.append(f"ID {event.event_id}")
    out.write("### " + " — ".join(heading_parts) + "\n\n")
    summary_rows = [
        ("Source", event.source),
        ("Channel", event.channel),
        ("Provider", event.provider),
        ("Event ID", event.event_id),
        ("Level", event.level),
        ("Numeric level", "" if event.level_value is None else event.level_value),
        ("Timestamp", event.timestamp),
        ("Record ID", event.record_id),
        ("Computer", event.computer),
        ("Task / category", event.task),
        ("Opcode", event.opcode),
        ("Keywords", event.keywords),
        ("User SID", event.user_id),
        ("Parser error", event.parse_error),
    ]
    out.write("| Field | Value |\n|---|---|\n")
    for name, value in summary_rows:
        out.write(f"| {name} | {_escape_table(value)} |\n")
    out.write("\n#### Rendered message\n\n")
    out.write((event.message or "_No rendered message was available._") + "\n\n")
    out.write("#### Complete XML field map\n\n")
    out.write("| XML path | Value |\n|---|---|\n")
    for path, value in event.fields:
        out.write(f"| `{_escape_table(path)}` | {_escape_table(value)} |\n")
    out.write("\n#### Raw event XML\n\n```xml\n")
    out.write(_safe_fence(event.raw_xml))
    out.write("\n```\n")
    return out.getvalue()


def events_to_markdown(
    events: list[EventRecord],
    *,
    title: str,
    source: str,
    system_snapshot: str = "",
    cancel_event: Event | None = None,
    progress_callback: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
) -> str:
    progress_callback = progress_callback or (lambda _v, _t: None)
    status_callback = status_callback or (lambda _s: None)
    cancel_event = cancel_event or Event()
    out = StringIO()
    out.write(summary_markdown(events, title, source))
    out.write("\n\n")
    out.write(
        "> Data-completeness note: every exported event includes a normalized summary, the complete flattened XML field map, and the original raw XML.\n"
    )
    if system_snapshot.strip():
        out.write("\n## Optional system snapshot\n\n")
        out.write(system_snapshot.strip())
        out.write("\n")
    out.write("\n## Complete events\n\n")
    total = len(events)
    progress_callback(0, total or 1)
    for index, event_record in enumerate(events, start=1):
        if cancel_event.is_set():
            raise RuntimeError("Operation cancelled.")
        out.write(event_to_complete_markdown(event_record, index))
        out.write("\n---\n\n")
        if index == total or index % 20 == 0:
            progress_callback(index, total or 1)
            status_callback(f"Converted {index:,} of {total:,} events to Markdown")
    return out.getvalue()


def write_markdown(
    events: list[EventRecord],
    *,
    title: str,
    source: str,
    output_path: str | Path,
    system_snapshot: str = "",
    cancel_event: Event | None = None,
    progress_callback: ProgressCallback | None = None,
    status_callback: StatusCallback | None = None,
    chunk_callback=None,
) -> dict[str, str]:
    del chunk_callback
    markdown_text = events_to_markdown(
        events,
        title=title,
        source=source,
        system_snapshot=system_snapshot,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        status_callback=status_callback,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown_text, encoding="utf-8-sig")
    return {"path": str(path), "markdown": markdown_text}


def event_to_ai_markdown(event: EventRecord, include_raw_xml: bool = False, max_message_chars: int = 5000) -> str:
    message = event.message.strip()
    if len(message) > max_message_chars:
        message = message[:max_message_chars] + "\n[message truncated by application]"
    selected_fields: list[tuple[str, str]] = []
    for path, value in event.fields:
        lowered = path.casefold()
        if any(token in lowered for token in ("eventdata", "userdata", "binary", "correlation", "execution")):
            selected_fields.append((path, value))
        if len(selected_fields) >= 80:
            break
    out = [
        f"### {event.evidence_label()}",
        f"- Level: {event.level or event.level_value or 'Unknown'}",
        f"- Computer: {event.computer or 'Unknown'}",
        f"- Task/category: {event.task or 'Unknown'}",
        f"- Opcode: {event.opcode or 'Unknown'}",
        f"- Keywords: {event.keywords or 'Unknown'}",
        f"- User SID: {event.user_id or 'Unknown'}",
        "",
        "Message:",
        message or "[No rendered message]",
    ]
    if selected_fields:
        out.extend(["", "Relevant structured fields:"])
        out.extend(f"- `{path}`: {value}" for path, value in selected_fields)
    if include_raw_xml:
        out.extend(["", "Raw XML:", "```xml", _safe_fence(event.raw_xml), "```"])
    return "\n".join(out)


def split_events_for_ai(
    events: list[EventRecord],
    *,
    char_budget: int,
    include_raw_xml: bool,
) -> list[str]:
    budget = max(char_budget, 8_000)
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for event in events:
        block = event_to_ai_markdown(event, include_raw_xml=include_raw_xml)
        if current and current_size + len(block) + 2 > budget:
            chunks.append("\n\n".join(current))
            current = []
            current_size = 0
        if len(block) > budget:
            block = block[:budget] + "\n[Event block truncated by application]"
        current.append(block)
        current_size += len(block) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks or ["[No events were supplied]"]
