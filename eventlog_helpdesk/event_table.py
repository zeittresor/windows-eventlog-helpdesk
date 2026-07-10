from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PyQt6.QtGui import QColor

from .models import EventRecord, level_rank

SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
EVENT_ROLE = int(Qt.ItemDataRole.UserRole) + 2


class EventTableModel(QAbstractTableModel):
    COLUMNS = [
        ("timestamp", "table.time"),
        ("level", "table.level"),
        ("provider", "table.provider"),
        ("event_id", "table.event_id"),
        ("channel", "table.channel"),
        ("record_id", "table.record_id"),
        ("task", "table.task"),
        ("computer", "table.computer"),
        ("message", "table.message"),
    ]

    def __init__(self, translator, parent=None) -> None:
        super().__init__(parent)
        self.translator = translator
        self.events: list[EventRecord] = []

    def set_events(self, events: list[EventRecord]) -> None:
        self.beginResetModel()
        self.events = list(events)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.events)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self.events)):
            return None
        event = self.events[index.row()]
        field = self.COLUMNS[index.column()][0]
        value = getattr(event, field, "")
        if role == Qt.ItemDataRole.DisplayRole:
            text = str(value or "")
            if field == "message":
                text = " ".join(text.split())
                return text if len(text) <= 260 else text[:257] + "…"
            return text
        if role == Qt.ItemDataRole.ToolTipRole:
            if field == "message":
                return event.message
            return str(value or "")
        if role == SORT_ROLE:
            if field in {"event_id", "record_id"}:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return str(value)
            return str(value or "")
        if role == EVENT_ROLE:
            return event
        if role == Qt.ItemDataRole.BackgroundRole:
            rank = level_rank(event)
            if rank == 1:
                return QColor(150, 20, 20, 105)
            if rank == 2:
                return QColor(125, 20, 20, 72)
            if rank == 3:
                return QColor(160, 120, 15, 55)
        if role == Qt.ItemDataRole.TextAlignmentRole and field in {"event_id", "record_id"}:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.translator.t(self.COLUMNS[section][1])
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def retranslate(self) -> None:
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(self.COLUMNS) - 1)


class EventFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.search_text = ""
        self.level_filter = "all"
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_search_text(self, text: str) -> None:
        self.search_text = text.casefold().strip()
        self.invalidateFilter()

    def set_level_filter(self, level_filter: str) -> None:
        self.level_filter = level_filter
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if model is None or not hasattr(model, "events"):
            return True
        event: EventRecord = model.events[source_row]
        rank = level_rank(event)
        if self.level_filter == "critical" and rank != 1:
            return False
        if self.level_filter == "errors" and rank not in {1, 2}:
            return False
        if self.level_filter == "warnings_errors" and rank not in {1, 2, 3}:
            return False
        if self.level_filter == "information" and rank != 4:
            return False
        if self.search_text and self.search_text not in event.searchable_text():
            return False
        return True
