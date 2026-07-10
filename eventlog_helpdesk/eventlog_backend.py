from __future__ import annotations

import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from threading import Event
from typing import Callable, Iterable

from .models import EventRecord, sort_events_desc

ProgressCallback = Callable[[int, int], None]
StatusCallback = Callable[[str], None]
ChunkCallback = Callable[[str], None]

_LEVEL_NAMES = {
    0: "LogAlways",
    1: "Critical",
    2: "Error",
    3: "Warning",
    4: "Information",
    5: "Verbose",
}


def _noop_progress(_value: int, _total: int) -> None:
    return


def _noop_status(_text: str) -> None:
    return


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _decode_output(data: bytes) -> str:
    if not data:
        return ""
    encodings = ["utf-8-sig", "utf-16", "utf-16-le"]
    if os.name == "nt":
        encodings.append("mbcs")
    encodings.extend(["cp1252", "latin-1"])
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            if "\x00" not in text[:200]:
                return text
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _creation_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _run_command(
    args: list[str],
    *,
    cancel_event: Event | None = None,
    status_callback: StatusCallback = _noop_status,
    timeout_seconds: int = 600,
) -> tuple[str, str, int]:
    cancel_event = cancel_event or Event()
    status_callback("Running: " + " ".join(args))
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "wevtutil.exe was not found. This application must run on Windows."
        ) from exc

    started = time.monotonic()
    while True:
        if cancel_event.is_set():
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
            raise RuntimeError("Operation cancelled.")
        try:
            stdout, stderr = process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() - started > timeout_seconds:
                process.kill()
                stdout, stderr = process.communicate()
                raise RuntimeError(
                    f"Command timed out after {timeout_seconds} seconds.\n"
                    + _decode_output(stderr)
                )

    return _decode_output(stdout), _decode_output(stderr), process.returncode


def enumerate_channels(
    *,
    cancel_event: Event | None = None,
    progress_callback: ProgressCallback = _noop_progress,
    status_callback: StatusCallback = _noop_status,
    chunk_callback: ChunkCallback | None = None,
) -> list[str]:
    del chunk_callback
    progress_callback(0, 0)
    stdout, stderr, return_code = _run_command(
        ["wevtutil", "el"],
        cancel_event=cancel_event,
        status_callback=status_callback,
        timeout_seconds=120,
    )
    if return_code != 0:
        raise RuntimeError(stderr.strip() or "Unable to enumerate Windows event channels.")
    channels = sorted({line.strip() for line in stdout.splitlines() if line.strip()}, key=str.casefold)
    progress_callback(len(channels), len(channels) or 1)
    return channels


def _extract_event_blocks(text: str) -> list[str]:
    return re.findall(r"<Event\b[^>]*>.*?</Event>", text, flags=re.IGNORECASE | re.DOTALL)


def _first_child(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    for child in list(element):
        if _local_name(child.tag) == name:
            return child
    return None


def _child_text(element: ET.Element | None, name: str) -> str:
    child = _first_child(element, name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _flatten_element(root: ET.Element) -> list[tuple[str, str]]:
    flattened: list[tuple[str, str]] = []

    def walk(element: ET.Element, current_path: str) -> None:
        for attr_name, attr_value in element.attrib.items():
            flattened.append((f"{current_path}/@{_local_name(attr_name)}", str(attr_value)))
        text = (element.text or "").strip()
        if text:
            flattened.append((current_path, text))
        children = list(element)
        totals = Counter(_local_name(child.tag) for child in children)
        seen: Counter[str] = Counter()
        for child in children:
            child_name = _local_name(child.tag)
            seen[child_name] += 1
            segment = f"{child_name}[{seen[child_name]}]" if totals[child_name] > 1 else child_name
            walk(child, f"{current_path}/{segment}")

    walk(root, _local_name(root.tag))
    return flattened


def parse_event_xml(xml_text: str, source: str) -> EventRecord:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        return EventRecord(
            source=source,
            message="The event XML could not be parsed.",
            raw_xml=xml_text,
            fields=[("Parser/Error", str(exc))],
            parse_error=str(exc),
        )

    system = _first_child(root, "System")
    rendering = _first_child(root, "RenderingInfo")

    provider = _first_child(system, "Provider")
    event_id_element = _first_child(system, "EventID")
    level_text = _child_text(rendering, "Level")
    level_value: int | None = None
    raw_level = _child_text(system, "Level")
    if raw_level:
        try:
            level_value = int(raw_level)
        except ValueError:
            level_value = None
    if not level_text and level_value is not None:
        level_text = _LEVEL_NAMES.get(level_value, raw_level)

    time_created = _first_child(system, "TimeCreated")
    correlation = _first_child(system, "Correlation")
    execution = _first_child(system, "Execution")
    security = _first_child(system, "Security")

    task_text = _child_text(rendering, "Task") or _child_text(system, "Task")
    opcode_text = _child_text(rendering, "Opcode") or _child_text(system, "Opcode")
    rendered_keywords = _first_child(rendering, "Keywords")
    rendered_keyword_values: list[str] = []
    if rendered_keywords is not None:
        rendered_keyword_values = [
            (child.text or "").strip()
            for child in list(rendered_keywords)
            if (child.text or "").strip()
        ]
    keywords_text = ", ".join(rendered_keyword_values) or _child_text(system, "Keywords")

    message = _child_text(rendering, "Message")
    if not message:
        event_data = _first_child(root, "EventData")
        if event_data is not None:
            values: list[str] = []
            for data in list(event_data):
                value = (data.text or "").strip()
                name = data.attrib.get("Name", "")
                if name and value:
                    values.append(f"{name}: {value}")
                elif value:
                    values.append(value)
            message = "\n".join(values)

    return EventRecord(
        source=source,
        channel=_child_text(system, "Channel"),
        provider=(provider.attrib.get("Name", "") if provider is not None else ""),
        event_id=((event_id_element.text or "").strip() if event_id_element is not None else ""),
        level_value=level_value,
        level=level_text,
        timestamp=(time_created.attrib.get("SystemTime", "") if time_created is not None else ""),
        record_id=_child_text(system, "EventRecordID"),
        computer=_child_text(system, "Computer"),
        task=task_text,
        opcode=opcode_text,
        keywords=keywords_text,
        user_id=(security.attrib.get("UserID", "") if security is not None else ""),
        message=message,
        fields=_flatten_element(root),
        raw_xml=xml_text,
    )


def _query(
    target: str,
    *,
    is_log_file: bool,
    limit: int,
    reverse: bool,
    xpath: str,
    cancel_event: Event | None,
    progress_callback: ProgressCallback,
    status_callback: StatusCallback,
) -> list[EventRecord]:
    progress_callback(0, 0)
    args = ["wevtutil", "qe", target, "/f:RenderedXml"]
    if reverse:
        args.append("/rd:true")
    if is_log_file:
        args.append("/lf:true")
    if limit > 0:
        args.append(f"/c:{limit}")
    if xpath.strip():
        args.append(f"/q:{xpath.strip()}")

    stdout, stderr, return_code = _run_command(
        args,
        cancel_event=cancel_event,
        status_callback=status_callback,
        timeout_seconds=1800 if limit == 0 else 600,
    )
    if return_code != 0:
        message = stderr.strip() or stdout.strip() or f"wevtutil exited with code {return_code}."
        if "access is denied" in message.casefold() or "zugriff verweigert" in message.casefold():
            message += "\nRun the application as administrator to read protected channels such as Security."
        raise RuntimeError(message)

    blocks = _extract_event_blocks(stdout)
    total = len(blocks)
    progress_callback(0, total or 1)
    events: list[EventRecord] = []
    for index, block in enumerate(blocks, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Operation cancelled.")
        events.append(parse_event_xml(block, target))
        if index == total or index % 25 == 0:
            progress_callback(index, total or 1)
            status_callback(f"Parsed {index:,} of {total:,} events")
    return sort_events_desc(events)


def query_channel(
    channel: str,
    *,
    limit: int = 1000,
    reverse: bool = True,
    xpath: str = "",
    cancel_event: Event | None = None,
    progress_callback: ProgressCallback = _noop_progress,
    status_callback: StatusCallback = _noop_status,
    chunk_callback: ChunkCallback | None = None,
) -> list[EventRecord]:
    del chunk_callback
    return _query(
        channel,
        is_log_file=False,
        limit=limit,
        reverse=reverse,
        xpath=xpath,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        status_callback=status_callback,
    )


def query_log_file(
    file_path: str | Path,
    *,
    limit: int = 1000,
    reverse: bool = True,
    xpath: str = "",
    cancel_event: Event | None = None,
    progress_callback: ProgressCallback = _noop_progress,
    status_callback: StatusCallback = _noop_status,
    chunk_callback: ChunkCallback | None = None,
) -> list[EventRecord]:
    del chunk_callback
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    return _query(
        str(path),
        is_log_file=True,
        limit=limit,
        reverse=reverse,
        xpath=xpath,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
        status_callback=status_callback,
    )


def query_channels(
    channels: Iterable[str],
    *,
    limit: int = 1000,
    reverse: bool = True,
    xpath: str = "",
    cancel_event: Event | None = None,
    progress_callback: ProgressCallback = _noop_progress,
    status_callback: StatusCallback = _noop_status,
    chunk_callback: ChunkCallback | None = None,
) -> list[EventRecord]:
    del chunk_callback
    channel_list = list(dict.fromkeys(channels))
    if not channel_list:
        return []
    combined: list[EventRecord] = []
    per_channel_limit = 0 if limit == 0 else max(limit, 1)
    total_channels = len(channel_list)
    for index, channel in enumerate(channel_list, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Operation cancelled.")
        status_callback(f"Reading {channel} ({index}/{total_channels})")
        # Channel progress is more useful here than the per-event parser progress.
        local_events = query_channel(
            channel,
            limit=per_channel_limit,
            reverse=reverse,
            xpath=xpath,
            cancel_event=cancel_event,
            progress_callback=lambda _v, _t: None,
            status_callback=status_callback,
        )
        combined.extend(local_events)
        progress_callback(index, total_channels)
    combined = sort_events_desc(combined)
    return combined if limit == 0 else combined[:limit]
