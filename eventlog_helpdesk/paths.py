from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
LANG_DIR = PROJECT_DIR / "lang"
THEMES_DIR = PROJECT_DIR / "themes"
HELP_DIR = PROJECT_DIR / "help"
RESOURCES_DIR = PROJECT_DIR / "resources"
LOG_DIR = PROJECT_DIR / "logs"
