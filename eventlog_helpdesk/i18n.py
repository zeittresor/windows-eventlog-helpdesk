from __future__ import annotations

import json
from pathlib import Path

from .paths import LANG_DIR


LANGUAGES = {
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "ru": "Русский",
}

RESPONSE_LANGUAGE_NAMES = {
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "ru": "Русский",
}


class Translator:
    def __init__(self, language: str = "en", lang_dir: Path = LANG_DIR) -> None:
        self.lang_dir = lang_dir
        self.language = "en"
        self._fallback = self._load_file("en")
        self._strings = self._fallback.copy()
        self.set_language(language)

    def _load_file(self, code: str) -> dict[str, str]:
        path = self.lang_dir / f"{code}.json"
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def set_language(self, code: str) -> None:
        if code not in LANGUAGES:
            code = "en"
        self.language = code
        loaded = self._load_file(code)
        self._strings = self._fallback.copy()
        self._strings.update(loaded)

    def t(self, key: str, **kwargs) -> str:
        text = self._strings.get(key, self._fallback.get(key, key))
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, ValueError):
                return text
        return text
