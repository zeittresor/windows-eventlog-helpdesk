from __future__ import annotations

import json
from pathlib import Path

from .paths import THEMES_DIR


class ThemeManager:
    def __init__(self, themes_dir: Path = THEMES_DIR) -> None:
        self.themes_dir = themes_dir

    def available_themes(self) -> list[tuple[str, str]]:
        themes: list[tuple[str, str]] = []
        for path in sorted(self.themes_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            themes.append((path.stem, str(data.get("name", path.stem.title()))))
        return themes

    def load(self, theme_id: str) -> dict[str, str]:
        path = self.themes_dir / f"{theme_id}.json"
        if not path.is_file():
            path = self.themes_dir / "dark.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "window",
            "panel",
            "panel_alt",
            "text",
            "muted",
            "accent",
            "accent_hover",
            "border",
            "selection",
            "selection_text",
            "input",
            "button",
            "button_hover",
            "disabled",
            "disabled_text",
            "tooltip",
            "tooltip_text",
        }
        missing = sorted(required.difference(data))
        if missing:
            raise ValueError(f"Theme {theme_id} misses keys: {', '.join(missing)}")
        return {key: str(value) for key, value in data.items()}

    def stylesheet(self, theme_id: str) -> str:
        c = self.load(theme_id)
        return f"""
        QWidget {{
            background-color: {c['window']};
            color: {c['text']};
            font-size: 10pt;
        }}
        QMainWindow, QDialog {{ background-color: {c['window']}; }}
        QGroupBox {{
            background-color: {c['panel']};
            border: 1px solid {c['border']};
            border-radius: 7px;
            margin-top: 12px;
            padding-top: 10px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
            color: {c['accent']};
        }}
        QFrame#Card {{
            background-color: {c['panel']};
            border: 1px solid {c['border']};
            border-radius: 8px;
        }}
        QLabel#CardValue {{ font-size: 20pt; font-weight: 700; color: {c['accent']}; }}
        QLabel#Muted {{ color: {c['muted']}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QTextBrowser, QSpinBox, QDoubleSpinBox,
        QComboBox, QDateTimeEdit, QListWidget, QTreeWidget, QTableView, QTableWidget {{
            background-color: {c['input']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 5px;
            padding: 4px;
            selection-background-color: {c['selection']};
            selection-color: {c['selection_text']};
        }}
        QTreeWidget::item, QListWidget::item {{ padding: 4px; }}
        QTreeWidget::item:selected, QListWidget::item:selected,
        QTableView::item:selected, QTableWidget::item:selected {{
            background-color: {c['selection']};
            color: {c['selection_text']};
        }}
        QHeaderView::section {{
            background-color: {c['panel_alt']};
            color: {c['text']};
            border: 0;
            border-right: 1px solid {c['border']};
            border-bottom: 1px solid {c['border']};
            padding: 6px;
            font-weight: 600;
        }}
        QPushButton, QToolButton {{
            background-color: {c['button']};
            color: {c['text']};
            border: 1px solid {c['border']};
            border-radius: 5px;
            padding: 6px 10px;
            min-height: 22px;
        }}
        QPushButton:hover, QToolButton:hover {{
            background-color: {c['button_hover']};
            border-color: {c['accent']};
        }}
        QPushButton:pressed, QToolButton:pressed {{ background-color: {c['selection']}; }}
        QPushButton:disabled, QToolButton:disabled {{
            background-color: {c['disabled']};
            color: {c['disabled_text']};
            border-color: {c['border']};
        }}
        QPushButton#PrimaryButton {{
            background-color: {c['accent']};
            color: {c['selection_text']};
            font-weight: 700;
        }}
        QPushButton#PrimaryButton:hover {{ background-color: {c['accent_hover']}; }}
        QTabWidget::pane {{
            border: 1px solid {c['border']};
            background-color: {c['panel']};
            border-radius: 5px;
        }}
        QTabBar::tab {{
            background-color: {c['panel_alt']};
            color: {c['text']};
            border: 1px solid {c['border']};
            padding: 8px 13px;
            margin-right: 2px;
            min-width: 95px;
        }}
        QTabBar::tab:selected {{
            background-color: {c['accent']};
            color: {c['selection_text']};
            font-weight: 700;
        }}
        QMenuBar, QMenu, QStatusBar {{ background-color: {c['panel']}; color: {c['text']}; }}
        QMenuBar::item:selected, QMenu::item:selected {{
            background-color: {c['selection']};
            color: {c['selection_text']};
        }}
        QProgressBar {{
            background-color: {c['input']};
            border: 1px solid {c['border']};
            border-radius: 5px;
            text-align: center;
            min-height: 18px;
        }}
        QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: 4px; }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {c['panel_alt']};
            border: none;
            margin: 0;
        }}
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
            background: {c['accent']};
            border-radius: 5px;
            min-height: 24px;
            min-width: 24px;
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QSplitter::handle {{ background-color: {c['border']}; }}
        QToolTip {{
            background-color: {c['tooltip']};
            color: {c['tooltip_text']};
            border: 1px solid {c['accent']};
            padding: 5px;
        }}
        QCheckBox, QRadioButton {{ spacing: 7px; }}
        QComboBox QAbstractItemView {{
            background-color: {c['input']};
            color: {c['text']};
            selection-background-color: {c['selection']};
            selection-color: {c['selection_text']};
        }}
        """
