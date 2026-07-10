from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(slots=True)
class EventRecord:
    source: str
    channel: str = ""
    provider: str = ""
    event_id: str = ""
    level_value: int | None = None
    level: str = ""
    timestamp: str = ""
    record_id: str = ""
    computer: str = ""
    task: str = ""
    opcode: str = ""
    keywords: str = ""
    user_id: str = ""
    message: str = ""
    fields: list[tuple[str, str]] = field(default_factory=list)
    raw_xml: str = ""
    parse_error: str = ""

    def searchable_text(self) -> str:
        base = [
            self.timestamp,
            self.level,
            self.provider,
            self.event_id,
            self.channel,
            self.record_id,
            self.computer,
            self.task,
            self.opcode,
            self.keywords,
            self.user_id,
            self.message,
        ]
        base.extend(f"{key} {value}" for key, value in self.fields)
        return "\n".join(part for part in base if part).casefold()

    def evidence_label(self) -> str:
        parts = [self.timestamp or "unknown time", self.channel or self.source]
        if self.provider:
            parts.append(self.provider)
        if self.event_id:
            parts.append(f"Event ID {self.event_id}")
        if self.record_id:
            parts.append(f"Record {self.record_id}")
        return " | ".join(parts)


def level_rank(event: EventRecord) -> int:
    if event.level_value is not None:
        return event.level_value
    normalized = event.level.casefold()
    if "critical" in normalized or "kritisch" in normalized or "critique" in normalized or "крит" in normalized:
        return 1
    if "error" in normalized or "fehler" in normalized or "erreur" in normalized or "ошиб" in normalized:
        return 2
    if "warn" in normalized or "warnung" in normalized or "avert" in normalized or "предуп" in normalized:
        return 3
    if "information" in normalized or "информа" in normalized:
        return 4
    if "verbose" in normalized or "ausführ" in normalized or "détaill" in normalized or "подроб" in normalized:
        return 5
    return 0


def sort_events_desc(events: Iterable[EventRecord]) -> list[EventRecord]:
    return sorted(events, key=lambda event: event.timestamp or "", reverse=True)
