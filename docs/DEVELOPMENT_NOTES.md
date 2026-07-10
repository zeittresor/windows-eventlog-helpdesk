# Development Notes

## Version

- Application: Windows EventLog Helpdesk
- Version: 0.1.0
- Date: 2026-07-10

## Design goals

1. Preserve complete Windows event evidence in Markdown.
2. Keep the GUI responsive during log loading, conversion, Ollama requests, system collection, and TTS.
3. Make the AI output operationally useful rather than verbose or generic.
4. Keep all analysis local by default.
5. Fit comfortably on Full HD displays with a Windows taskbar and on 2560×1080 ultrawide setups.
6. Use responsive splitters, layouts, scroll areas, and no tall fixed windows.
7. Support English, German, French, and Russian with runtime switching.
8. Apply themes to the complete UI with usable contrast.

## Module overview

- `eventlog_backend.py` — `wevtutil` channel enumeration and live/offline queries, XML extraction, complete field flattening.
- `models.py` — event record representation and severity helpers.
- `markdown_exporter.py` — complete evidence export, compact AI evidence, chunk construction.
- `ollama_client.py` — model discovery plus streamed generate/chat requests.
- `analysis_engine.py` — strict diagnostic prompt, scope selection, automatic hierarchical chunk analysis, chat grounding.
- `system_snapshot.py` — local system context collection.
- `reporting.py` — PDF generation with calculated chart images and tables.
- `tts.py` — Windows SAPI voice discovery and non-blocking speech.
- `workers.py` — cancellable QRunnable wrapper and signals.
- `event_table.py` — sortable/filterable event model.
- `themes.py`, `i18n.py`, `settings.py` — presentation and persistent configuration.
- `main_window.py` — tab composition and orchestration.

## Event-data completeness

`parse_event_xml()` stores the original event block verbatim in `raw_xml`. `_flatten_element()` emits every element text and every attribute using stable indexed paths for duplicate sibling elements. The complete Markdown writer always includes both representations.

The AI path is deliberately separate from the evidence path. Ollama receives compact Markdown by default so that context is used for diagnostically relevant fields rather than repeated XML syntax. Raw XML can be enabled explicitly for AI context without changing export integrity.

## Threading

All potentially slow tasks use `TaskWorker` on the global `QThreadPool`:

- channel discovery;
- live/offline event loading;
- Markdown conversion and writing;
- Ollama model discovery;
- Ollama analysis and chat;
- system snapshot collection;
- Windows TTS.

Workers expose result, error, progress, status, streamed text, and finished signals. Cancellation uses `threading.Event`. `wevtutil` is terminated when cancellation is observed.

PDF generation currently runs in the GUI thread because Qt text/PDF objects may depend on GUI resources. The report is comparatively small; move it to a dedicated GUI-safe rendering service if future reports become heavy.

## Analysis pipeline

Auto mode estimates the supplied evidence against a character budget. When one request is unsuitable:

1. events are split into bounded Markdown chunks;
2. each chunk receives a concise evidence triage pass;
3. the final pass receives aggregate counts, the optional snapshot, a direct evidence sample, and all chunk findings;
4. the final response is streamed to the analysis field.

The system prompt contains explicit prompt-injection resistance for log messages and XML values, evidence citation requirements, confidence handling, and safe remediation rules.

## Future enhancements

- True streaming export for multi-gigabyte EVTX files without keeping all records in memory.
- Date-range UI compiled into Event Log XPath.
- Saved XPath/query profiles and provider/Event-ID suppression rules.
- Optional remote-computer event log access with explicit credential and trust controls.
- Structured JSON findings from Ollama for sortable severity/confidence/remediation tables.
- Correlation graph between providers, Event IDs, services, and time clusters.
- Service/process lookup from an event's PID at collection time where applicable.
- Optional report signing or SHA-256 manifest for evidence packages.
- Separate portable build and signed Windows executable.
