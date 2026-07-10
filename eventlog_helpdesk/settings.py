from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings, QStandardPaths

from .version import APP_NAME, ORGANIZATION


class AppSettings:
    DEFAULTS = {
        "interface/language": "en",
        "interface/theme": "aurora",
        "interface/tooltips": True,
        "event/default_limit": 1000,
        "event/reverse": True,
        "event/auto_save_markdown": True,
        "report/auto_save_pdf": True,
        "analysis/response_language": "en",
        "analysis/include_snapshot": True,
        "analysis/include_raw_xml": False,
        "analysis/scope": "warnings_errors",
        "analysis/mode": "auto",
        "analysis/context_char_budget": 90000,
        "ollama/endpoint": "http://127.0.0.1:11434",
        "ollama/model": "",
        "ollama/timeout": 900,
        "ollama/temperature": 0.15,
        "ollama/num_predict": 4096,
        "ollama/num_ctx": 32768,
        "ollama/keep_alive": "10m",
        "tts/enabled": False,
        "tts/auto_analysis": False,
        "tts/voice": "",
        "tts/rate": 0,
        "tts/volume": 100,
    }

    def __init__(self) -> None:
        self._settings = QSettings(ORGANIZATION, APP_NAME)

    @staticmethod
    def default_output_dir() -> Path:
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        base = Path(documents) if documents else Path.home() / "Documents"
        return base / "Windows EventLog Helpdesk Reports"

    def value(self, key: str, default=None, value_type=None):
        if default is None:
            default = self.DEFAULTS.get(key)
        if value_type is None and default is not None:
            value_type = type(default)
        if value_type is None:
            return self._settings.value(key, default)
        return self._settings.value(key, default, type=value_type)

    def set_value(self, key: str, value) -> None:
        self._settings.setValue(key, value)

    def output_dir(self) -> Path:
        raw = self._settings.value("report/output_dir", "", type=str).strip()
        path = Path(raw) if raw else self.default_output_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def set_output_dir(self, path: str | Path) -> None:
        self.set_value("report/output_dir", str(Path(path)))

    def sync(self) -> None:
        self._settings.sync()
