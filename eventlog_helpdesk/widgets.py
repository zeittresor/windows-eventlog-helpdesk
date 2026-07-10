from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QWidget


class BarChartWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[tuple[str, int]] = []
        self._title = ""
        self._empty_text = "No data"
        self.setMinimumHeight(230)

    def set_empty_text(self, text: str) -> None:
        self._empty_text = text
        self.update()

    def set_data(self, title: str, data: list[tuple[str, int]]) -> None:
        self._title = title
        self._data = list(data[:12])
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        painter.setPen(palette.text().color())
        title_font = QFont(self.font())
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(QRectF(12, 8, self.width() - 24, 26), Qt.AlignmentFlag.AlignLeft, self._title)
        if not self._data:
            painter.setFont(self.font())
            painter.setPen(palette.placeholderText().color())
            painter.drawText(self.rect().adjusted(12, 38, -12, -12), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return
        max_value = max(value for _, value in self._data) or 1
        top = 42
        bottom = 12
        available = max(self.height() - top - bottom, 20)
        row_height = available / len(self._data)
        label_width = min(max(self.width() * 0.36, 120), 310)
        bar_left = label_width + 18
        bar_width = max(self.width() - bar_left - 55, 30)
        painter.setFont(self.font())
        accent = palette.highlight().color()
        muted = palette.mid().color()
        for index, (label, value) in enumerate(self._data):
            y = top + index * row_height
            text_rect = QRectF(10, y, label_width, row_height)
            clipped = label if len(label) <= 38 else label[:35] + "…"
            painter.setPen(palette.text().color())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, clipped)
            height = max(row_height * 0.55, 6)
            bar_rect = QRectF(bar_left, y + (row_height - height) / 2, bar_width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(muted)
            painter.drawRoundedRect(bar_rect, 3, 3)
            value_rect = QRectF(bar_left, bar_rect.y(), bar_width * (value / max_value), height)
            painter.setBrush(accent)
            painter.drawRoundedRect(value_rect, 3, 3)
            painter.setPen(palette.text().color())
            painter.drawText(QRectF(bar_left + bar_width + 8, y, 45, row_height), Qt.AlignmentFlag.AlignVCenter, str(value))
