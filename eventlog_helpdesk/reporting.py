from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import markdown as markdown_lib
from PyQt6.QtCore import QMarginsF, QRectF, Qt, QUrl
from PyQt6.QtGui import QColor, QFont, QImage, QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument

from .models import EventRecord, level_rank


def _bar_chart(data: list[tuple[str, int]], title: str, width: int = 1300, height: int = 620) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    title_font = QFont("Arial", 22)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.setPen(QColor("#17212b"))
    painter.drawText(QRectF(35, 20, width - 70, 50), Qt.AlignmentFlag.AlignLeft, title)
    if not data:
        painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "No data")
        painter.end()
        return image
    data = data[:12]
    max_value = max(value for _, value in data) or 1
    top = 90
    bottom = 35
    label_width = 410
    value_width = 90
    available = height - top - bottom
    row_height = available / len(data)
    bar_left = label_width + 45
    bar_width = width - bar_left - value_width - 35
    body_font = QFont("Arial", 14)
    painter.setFont(body_font)
    for idx, (label, value) in enumerate(data):
        y = top + idx * row_height
        label_rect = QRectF(25, y, label_width, row_height)
        clipped = label if len(label) <= 48 else label[:45] + "…"
        painter.setPen(QColor("#23313f"))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, clipped)
        h = max(row_height * 0.54, 10)
        bg_rect = QRectF(bar_left, y + (row_height - h) / 2, bar_width, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#d7e0e8"))
        painter.drawRoundedRect(bg_rect, 5, 5)
        fg_rect = QRectF(bar_left, bg_rect.y(), bar_width * value / max_value, h)
        painter.setBrush(QColor("#167d96"))
        painter.drawRoundedRect(fg_rect, 5, 5)
        painter.setPen(QColor("#17212b"))
        painter.drawText(QRectF(bar_left + bar_width + 12, y, value_width, row_height), Qt.AlignmentFlag.AlignVCenter, str(value))
    painter.end()
    return image


def _table(headers: list[str], rows: list[list[object]]) -> str:
    parts = ["<table><thead><tr>"]
    parts.extend(f"<th>{html.escape(header)}</th>" for header in headers)
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        parts.extend(f"<td>{html.escape(str(value))}</td>" for value in row)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def generate_pdf_report(
    *,
    events: list[EventRecord],
    source: str,
    analysis_markdown: str,
    system_snapshot_markdown: str,
    output_path: str | Path,
    title: str = "Windows EventLog Helpdesk Report",
) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    writer.setTitle(title)
    writer.setCreator("Windows EventLog Helpdesk")
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(144)
    writer.setPageMargins(QMarginsF(13, 13, 13, 13), QPageLayout.Unit.Millimeter)

    doc = QTextDocument()
    doc.setDocumentMargin(18)

    level_counts = Counter(event.level or "Unknown" for event in events)
    provider_counts = Counter(event.provider or "Unknown" for event in events)
    event_id_counts = Counter(event.event_id or "Unknown" for event in events)
    channel_counts = Counter(event.channel or event.source or "Unknown" for event in events)
    severity_counts = {
        "Critical": sum(1 for e in events if level_rank(e) == 1),
        "Error": sum(1 for e in events if level_rank(e) == 2),
        "Warning": sum(1 for e in events if level_rank(e) == 3),
        "Information": sum(1 for e in events if level_rank(e) == 4),
        "Other": sum(1 for e in events if level_rank(e) not in {1, 2, 3, 4}),
    }
    severity_image = _bar_chart(list(severity_counts.items()), "Event severity distribution")
    provider_image = _bar_chart(provider_counts.most_common(10), "Top event providers")
    doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl("report://severity"), severity_image)
    doc.addResource(QTextDocument.ResourceType.ImageResource, QUrl("report://providers"), provider_image)

    generated = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    css = """
    body { font-family: Arial, sans-serif; color: #17212b; font-size: 10pt; }
    h1 { color: #0f586c; border-bottom: 2px solid #0f586c; padding-bottom: 6px; }
    h2 { color: #1a6f82; margin-top: 18px; }
    h3 { color: #334c59; }
    table { border-collapse: collapse; width: 100%; margin: 8px 0 14px 0; }
    th { background: #dcecf1; font-weight: bold; }
    th, td { border: 1px solid #a8bac2; padding: 5px; vertical-align: top; }
    code { font-family: Consolas, monospace; background: #eef3f5; }
    pre { font-family: Consolas, monospace; background: #eef3f5; padding: 8px; white-space: pre-wrap; }
    blockquote { border-left: 4px solid #4b95a8; margin-left: 0; padding-left: 10px; color: #425862; }
    .meta { background: #eef6f8; border: 1px solid #c2dbe2; padding: 8px; }
    img { width: 100%; max-width: 700px; }
    """
    overview_rows = [
        ["Source", source],
        ["Generated", generated],
        ["Events included", len(events)],
        ["Critical", severity_counts["Critical"]],
        ["Errors", severity_counts["Error"]],
        ["Warnings", severity_counts["Warning"]],
        ["Information", severity_counts["Information"]],
    ]
    top_event_rows = [[event_id, count] for event_id, count in event_id_counts.most_common(15)]
    top_channel_rows = [[channel, count] for channel, count in channel_counts.most_common(15)]
    analysis_html = markdown_lib.markdown(
        html.escape(analysis_markdown) if analysis_markdown else "_No Ollama analysis was included._",
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    snapshot_html = markdown_lib.markdown(
        html.escape(system_snapshot_markdown) if system_snapshot_markdown else "_No system snapshot was included._",
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    document_html = f"""
    <html><head><meta charset="utf-8"><style>{css}</style></head><body>
    <h1>{html.escape(title)}</h1>
    <div class="meta">{_table(["Property", "Value"], overview_rows)}</div>
    <h2>Visual overview</h2>
    <img src="report://severity" />
    <img src="report://providers" />
    <h2>Top event IDs</h2>
    {_table(["Event ID", "Count"], top_event_rows)}
    <h2>Top channels</h2>
    {_table(["Channel", "Count"], top_channel_rows)}
    <h2>Ollama diagnosis</h2>
    {analysis_html}
    <h2>System snapshot</h2>
    {snapshot_html}
    <h2>Report integrity note</h2>
    <p>This PDF is a derived summary. The corresponding Markdown export is the complete evidence record and preserves each event's full flattened XML field map and original raw XML.</p>
    </body></html>
    """
    doc.setHtml(document_html)
    doc.print(writer)
    return str(path)
