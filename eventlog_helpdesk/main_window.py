from __future__ import annotations

import html
import logging
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

import markdown as markdown_lib
from PyQt6.QtCore import QItemSelection, QItemSelectionModel, QThreadPool, QTimer, Qt, QUrl
from PyQt6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon, QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .analysis_engine import analyze_events, chat_about_events, select_events_for_scope
from .event_table import EVENT_ROLE, EventFilterProxyModel, EventTableModel
from .eventlog_backend import enumerate_channels, query_channel, query_channels, query_log_file
from .i18n import LANGUAGES, RESPONSE_LANGUAGE_NAMES, Translator
from .markdown_exporter import sanitize_filename, write_markdown
from .models import EventRecord, level_rank
from .ollama_client import OllamaClient
from .paths import HELP_DIR, RESOURCES_DIR
from .reporting import generate_pdf_report
from .settings import AppSettings
from .system_snapshot import collect_system_snapshot
from .themes import ThemeManager
from .tts import list_voices, speak_text
from .version import APP_NAME, RELEASE_DATE, SOURCE_URL, VERSION
from .widgets import BarChartWidget
from .workers import TaskWorker

LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = AppSettings()
        self.translator = Translator(self.settings.value("interface/language", "en", str))
        self.theme_manager = ThemeManager()
        self.thread_pool = QThreadPool.globalInstance()
        self.workers: dict[str, TaskWorker] = {}
        self.tooltip_targets: list[tuple[QWidget, str]] = []

        self.events: list[EventRecord] = []
        self.current_source = ""
        self.current_source_kind = ""
        self.current_source_payload: object = None
        self.current_markdown = ""
        self.current_markdown_path = ""
        self.current_analysis = ""
        self.current_analysis_path = ""
        self.system_snapshot = ""
        self.chat_history: list[dict[str, str]] = []
        self._pending_chat_question = ""
        self._streamed_chat_answer = ""
        self._streamed_analysis = ""
        self._last_worker_error = ""
        self._channels: list[str] = []

        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        icon_path = RESOURCES_DIR / "app_icon.svg"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._build_ui()
        self._restore_settings_to_ui()
        self._apply_theme()
        self.retranslate_ui()
        self._restore_settings_to_ui()
        self._update_analysis_input_label()
        self._resize_for_screen()
        self._wire_delayed_startup()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    def t(self, key: str, **kwargs) -> str:
        return self.translator.t(key, **kwargs)

    def _resize_for_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1400, 840)
            return
        available = screen.availableGeometry()
        width = min(1500, max(1080, int(available.width() * 0.92)))
        height = min(900, max(700, int(available.height() * 0.91)))
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    def _build_ui(self) -> None:
        self._build_menu()
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)

        self.event_tab = self._build_event_tab()
        self.dashboard_tab = self._build_dashboard_tab()
        self.analysis_tab = self._build_analysis_tab()
        self.chat_tab = self._build_chat_tab()
        self.system_tab = self._build_system_tab()
        self.reports_tab = self._build_reports_tab()
        self.settings_tab = self._build_settings_tab()
        self.help_tab = self._build_help_tab()

        for widget in (
            self.event_tab,
            self.dashboard_tab,
            self.analysis_tab,
            self.chat_tab,
            self.system_tab,
            self.reports_tab,
            self.settings_tab,
            self.help_tab,
        ):
            self.tabs.addTab(widget, "")

        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        self.worker_count_label = QLabel()
        self.statusBar().addPermanentWidget(self.worker_count_label)

    def _build_menu(self) -> None:
        self.menu_file = self.menuBar().addMenu("")
        self.action_import = QAction(self)
        self.action_import.triggered.connect(self.import_evtx)
        self.menu_file.addAction(self.action_import)
        self.action_save_markdown = QAction(self)
        self.action_save_markdown.triggered.connect(self.save_markdown_as)
        self.menu_file.addAction(self.action_save_markdown)
        self.action_export_pdf = QAction(self)
        self.action_export_pdf.triggered.connect(self.export_pdf_as)
        self.menu_file.addAction(self.action_export_pdf)
        self.menu_file.addSeparator()
        self.action_exit = QAction(self)
        self.action_exit.triggered.connect(self.close)
        self.menu_file.addAction(self.action_exit)

        self.menu_view = self.menuBar().addMenu("")
        self.action_refresh_channels = QAction(self)
        self.action_refresh_channels.triggered.connect(self.refresh_channels)
        self.menu_view.addAction(self.action_refresh_channels)
        self.action_refresh_models = QAction(self)
        self.action_refresh_models.triggered.connect(self.refresh_models)
        self.menu_view.addAction(self.action_refresh_models)
        self.action_open_output = QAction(self)
        self.action_open_output.triggered.connect(self.open_output_folder)
        self.menu_view.addAction(self.action_open_output)

        self.menu_help = self.menuBar().addMenu("")
        self.action_help = QAction(self)
        self.action_help.triggered.connect(lambda: self.tabs.setCurrentWidget(self.help_tab))
        self.menu_help.addAction(self.action_help)
        self.action_about = QAction(self)
        self.action_about.triggered.connect(self.show_about)
        self.menu_help.addAction(self.action_about)

    def _build_event_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        action_row = QHBoxLayout()
        self.btn_refresh_channels = QPushButton()
        self.btn_refresh_channels.clicked.connect(self.refresh_channels)
        action_row.addWidget(self.btn_refresh_channels)
        self.btn_import_evtx = QPushButton()
        self.btn_import_evtx.clicked.connect(self.import_evtx)
        action_row.addWidget(self.btn_import_evtx)
        self.btn_load_selected = QPushButton()
        self.btn_load_selected.setObjectName("PrimaryButton")
        self.btn_load_selected.clicked.connect(self.load_selected_source)
        action_row.addWidget(self.btn_load_selected)
        self.btn_cancel_load = QPushButton()
        self.btn_cancel_load.clicked.connect(lambda: self.cancel_worker("event_load"))
        action_row.addWidget(self.btn_cancel_load)
        action_row.addStretch(1)
        self.label_event_limit = QLabel()
        action_row.addWidget(self.label_event_limit)
        self.spin_event_limit = QSpinBox()
        self.spin_event_limit.setRange(0, 1_000_000)
        self.spin_event_limit.setSpecialValueText("All")
        self.spin_event_limit.setMaximumWidth(140)
        action_row.addWidget(self.spin_event_limit)
        root.addLayout(action_row)

        query_row = QHBoxLayout()
        self.label_xpath = QLabel()
        query_row.addWidget(self.label_xpath)
        self.edit_xpath = QLineEdit()
        self.edit_xpath.setClearButtonEnabled(True)
        query_row.addWidget(self.edit_xpath, 1)
        self.label_current_source = QLabel()
        self.label_current_source.setObjectName("Muted")
        self.label_current_source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        query_row.addWidget(self.label_current_source, 1)
        root.addLayout(query_row)

        outer_split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(outer_split, 1)

        tree_group = QGroupBox()
        tree_layout = QVBoxLayout(tree_group)
        self.event_tree = QTreeWidget()
        self.event_tree.setHeaderHidden(True)
        self.event_tree.setUniformRowHeights(True)
        self.event_tree.itemDoubleClicked.connect(lambda _item, _column: self.load_selected_source())
        self.event_tree.currentItemChanged.connect(self._on_tree_selection_changed)
        tree_layout.addWidget(self.event_tree)
        outer_split.addWidget(tree_group)

        right_split = QSplitter(Qt.Orientation.Vertical)
        outer_split.addWidget(right_split)
        outer_split.setStretchFactor(0, 0)
        outer_split.setStretchFactor(1, 1)
        outer_split.setSizes([330, 1050])

        table_container = QWidget()
        table_layout = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)
        filter_row = QHBoxLayout()
        self.label_search = QLabel()
        filter_row.addWidget(self.label_search)
        self.edit_event_search = QLineEdit()
        self.edit_event_search.setClearButtonEnabled(True)
        self.edit_event_search.textChanged.connect(self._filter_events)
        filter_row.addWidget(self.edit_event_search, 1)
        self.label_level_filter = QLabel()
        filter_row.addWidget(self.label_level_filter)
        self.combo_level_filter = QComboBox()
        self.combo_level_filter.currentIndexChanged.connect(self._filter_events)
        filter_row.addWidget(self.combo_level_filter)
        self.label_visible_count = QLabel()
        self.label_visible_count.setObjectName("Muted")
        filter_row.addWidget(self.label_visible_count)
        table_layout.addLayout(filter_row)

        self.event_model = EventTableModel(self.translator, self)
        self.event_proxy = EventFilterProxyModel(self)
        self.event_proxy.setSourceModel(self.event_model)
        self.event_table = QTableView()
        self.event_table.setModel(self.event_proxy)
        self.event_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.event_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.event_table.setSortingEnabled(True)
        self.event_table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.event_table.verticalHeader().setVisible(False)
        self.event_table.horizontalHeader().setStretchLastSection(True)
        self.event_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.event_table.selectionModel().selectionChanged.connect(self._event_selection_changed)
        table_layout.addWidget(self.event_table, 1)
        right_split.addWidget(table_container)

        detail_group = QGroupBox()
        self.event_detail_group = detail_group
        detail_layout = QVBoxLayout(detail_group)
        self.event_detail = QPlainTextEdit()
        self.event_detail.setReadOnly(True)
        self.event_detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        detail_layout.addWidget(self.event_detail)
        right_split.addWidget(detail_group)
        right_split.setSizes([540, 260])

        bottom_row = QHBoxLayout()
        self.btn_save_markdown = QPushButton()
        self.btn_save_markdown.clicked.connect(self.save_markdown_as)
        bottom_row.addWidget(self.btn_save_markdown)
        self.btn_analyze_loaded = QPushButton()
        self.btn_analyze_loaded.setObjectName("PrimaryButton")
        self.btn_analyze_loaded.clicked.connect(self._go_to_analysis_and_run)
        bottom_row.addWidget(self.btn_analyze_loaded)
        self.btn_clear_events = QPushButton()
        self.btn_clear_events.clicked.connect(self.clear_events)
        bottom_row.addWidget(self.btn_clear_events)
        bottom_row.addStretch(1)
        self.event_progress = QProgressBar()
        self.event_progress.setMinimumWidth(300)
        self.event_progress.setVisible(False)
        bottom_row.addWidget(self.event_progress)
        root.addLayout(bottom_row)

        self._register_tooltip(self.btn_refresh_channels, "tip.refresh_channels")
        self._register_tooltip(self.btn_import_evtx, "tip.import_evtx")
        self._register_tooltip(self.btn_load_selected, "tip.load_selected")
        self._register_tooltip(self.btn_cancel_load, "tip.cancel")
        self._register_tooltip(self.spin_event_limit, "tip.event_limit")
        self._register_tooltip(self.edit_xpath, "tip.xpath")
        self._register_tooltip(self.edit_event_search, "tip.search")
        self._register_tooltip(self.combo_level_filter, "tip.level_filter")
        self._register_tooltip(self.btn_save_markdown, "tip.save_markdown")
        self._register_tooltip(self.btn_analyze_loaded, "tip.analyze")
        return tab

    def _build_dashboard_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(10, 10, 10, 10)
        cards = QHBoxLayout()
        self.dashboard_cards: dict[str, tuple[QLabel, QLabel]] = {}
        for key in ("total", "critical", "errors", "warnings", "information"):
            frame = QFrame()
            frame.setObjectName("Card")
            layout = QVBoxLayout(frame)
            title = QLabel()
            title.setObjectName("Muted")
            value = QLabel("0")
            value.setObjectName("CardValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(title)
            layout.addWidget(value)
            cards.addWidget(frame, 1)
            self.dashboard_cards[key] = (title, value)
        root.addLayout(cards)

        charts_split = QSplitter(Qt.Orientation.Horizontal)
        self.chart_providers = BarChartWidget()
        self.chart_event_ids = BarChartWidget()
        charts_split.addWidget(self.chart_providers)
        charts_split.addWidget(self.chart_event_ids)
        charts_split.setSizes([700, 700])
        root.addWidget(charts_split, 1)

        tables_split = QSplitter(Qt.Orientation.Horizontal)
        self.table_top_channels = QTableWidget(0, 2)
        self.table_top_channels.verticalHeader().setVisible(False)
        self.table_top_channels.horizontalHeader().setStretchLastSection(True)
        self.table_top_channels.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_timeline = QTableWidget(0, 2)
        self.table_timeline.verticalHeader().setVisible(False)
        self.table_timeline.horizontalHeader().setStretchLastSection(True)
        self.table_timeline.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tables_split.addWidget(self.table_top_channels)
        tables_split.addWidget(self.table_timeline)
        tables_split.setSizes([700, 700])
        root.addWidget(tables_split, 1)
        return tab

    def _build_analysis_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        config_group = QGroupBox()
        grid = QGridLayout(config_group)
        self.label_analysis_model = QLabel()
        grid.addWidget(self.label_analysis_model, 0, 0)
        self.combo_analysis_model = QComboBox()
        self.combo_analysis_model.setEditable(False)
        self.combo_analysis_model.currentTextChanged.connect(self._sync_model_selection)
        grid.addWidget(self.combo_analysis_model, 0, 1)
        self.btn_refresh_models = QPushButton()
        self.btn_refresh_models.clicked.connect(self.refresh_models)
        grid.addWidget(self.btn_refresh_models, 0, 2)
        self.label_response_language = QLabel()
        grid.addWidget(self.label_response_language, 0, 3)
        self.combo_response_language = QComboBox()
        self.combo_response_language.currentIndexChanged.connect(self._response_language_changed)
        grid.addWidget(self.combo_response_language, 0, 4)

        self.label_analysis_scope = QLabel()
        grid.addWidget(self.label_analysis_scope, 1, 0)
        self.combo_analysis_scope = QComboBox()
        grid.addWidget(self.combo_analysis_scope, 1, 1)
        self.label_analysis_mode = QLabel()
        grid.addWidget(self.label_analysis_mode, 1, 2)
        self.combo_analysis_mode = QComboBox()
        grid.addWidget(self.combo_analysis_mode, 1, 3)
        self.check_include_snapshot = QCheckBox()
        grid.addWidget(self.check_include_snapshot, 1, 4)
        self.check_include_raw_xml = QCheckBox()
        grid.addWidget(self.check_include_raw_xml, 2, 0, 1, 2)
        self.label_analysis_input = QLabel()
        self.label_analysis_input.setObjectName("Muted")
        grid.addWidget(self.label_analysis_input, 2, 2, 1, 3)
        root.addWidget(config_group)

        action_row = QHBoxLayout()
        self.btn_run_analysis = QPushButton()
        self.btn_run_analysis.setObjectName("PrimaryButton")
        self.btn_run_analysis.clicked.connect(self.run_analysis)
        action_row.addWidget(self.btn_run_analysis)
        self.btn_cancel_analysis = QPushButton()
        self.btn_cancel_analysis.clicked.connect(lambda: self.cancel_worker("analysis"))
        action_row.addWidget(self.btn_cancel_analysis)
        self.btn_save_analysis = QPushButton()
        self.btn_save_analysis.clicked.connect(self.save_analysis_as)
        action_row.addWidget(self.btn_save_analysis)
        self.btn_export_pdf = QPushButton()
        self.btn_export_pdf.clicked.connect(self.export_pdf_as)
        action_row.addWidget(self.btn_export_pdf)
        self.btn_speak_analysis = QPushButton()
        self.btn_speak_analysis.clicked.connect(lambda: self.speak(self.current_analysis))
        action_row.addWidget(self.btn_speak_analysis)
        action_row.addStretch(1)
        self.analysis_progress = QProgressBar()
        self.analysis_progress.setMinimumWidth(330)
        self.analysis_progress.setVisible(False)
        action_row.addWidget(self.analysis_progress)
        root.addLayout(action_row)

        self.analysis_status = QLabel()
        self.analysis_status.setObjectName("Muted")
        root.addWidget(self.analysis_status)
        self.analysis_output = QPlainTextEdit()
        self.analysis_output.setReadOnly(True)
        self.analysis_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        root.addWidget(self.analysis_output, 1)

        self._register_tooltip(self.combo_analysis_model, "tip.model")
        self._register_tooltip(self.btn_refresh_models, "tip.refresh_models")
        self._register_tooltip(self.combo_response_language, "tip.response_language")
        self._register_tooltip(self.combo_analysis_scope, "tip.analysis_scope")
        self._register_tooltip(self.combo_analysis_mode, "tip.analysis_mode")
        self._register_tooltip(self.check_include_snapshot, "tip.include_snapshot")
        self._register_tooltip(self.check_include_raw_xml, "tip.include_raw_xml")
        self._register_tooltip(self.btn_run_analysis, "tip.run_analysis")
        self._register_tooltip(self.btn_speak_analysis, "tip.speak")
        return tab

    def _build_chat_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(10, 10, 10, 10)
        top = QHBoxLayout()
        self.label_chat_model = QLabel()
        top.addWidget(self.label_chat_model)
        self.combo_chat_model = QComboBox()
        self.combo_chat_model.currentTextChanged.connect(self._sync_model_selection)
        top.addWidget(self.combo_chat_model, 1)
        self.label_chat_context = QLabel()
        self.label_chat_context.setObjectName("Muted")
        top.addWidget(self.label_chat_context, 2)
        root.addLayout(top)

        self.chat_history_view = QTextBrowser()
        self.chat_history_view.setOpenExternalLinks(False)
        root.addWidget(self.chat_history_view, 2)

        prompt_answer_split = QSplitter(Qt.Orientation.Horizontal)
        prompt_group = QGroupBox()
        self.chat_prompt_group = prompt_group
        prompt_layout = QVBoxLayout(prompt_group)
        self.chat_question = QTextEdit()
        self.chat_question.setMinimumHeight(140)
        prompt_layout.addWidget(self.chat_question, 1)
        prompt_buttons = QHBoxLayout()
        self.btn_chat_send = QPushButton()
        self.btn_chat_send.setObjectName("PrimaryButton")
        self.btn_chat_send.clicked.connect(self.send_chat_question)
        prompt_buttons.addWidget(self.btn_chat_send)
        self.btn_chat_stop = QPushButton()
        self.btn_chat_stop.clicked.connect(lambda: self.cancel_worker("chat"))
        prompt_buttons.addWidget(self.btn_chat_stop)
        self.btn_chat_clear = QPushButton()
        self.btn_chat_clear.clicked.connect(self.clear_chat)
        prompt_buttons.addWidget(self.btn_chat_clear)
        prompt_buttons.addStretch(1)
        prompt_layout.addLayout(prompt_buttons)

        answer_group = QGroupBox()
        self.chat_answer_group = answer_group
        answer_layout = QVBoxLayout(answer_group)
        self.chat_answer = QPlainTextEdit()
        self.chat_answer.setReadOnly(True)
        answer_layout.addWidget(self.chat_answer, 1)
        answer_buttons = QHBoxLayout()
        self.btn_chat_speak = QPushButton()
        self.btn_chat_speak.clicked.connect(lambda: self.speak(self.chat_answer.toPlainText()))
        answer_buttons.addWidget(self.btn_chat_speak)
        self.btn_chat_save = QPushButton()
        self.btn_chat_save.clicked.connect(self.save_chat_as)
        answer_buttons.addWidget(self.btn_chat_save)
        answer_buttons.addStretch(1)
        answer_layout.addLayout(answer_buttons)

        prompt_answer_split.addWidget(prompt_group)
        prompt_answer_split.addWidget(answer_group)
        prompt_answer_split.setSizes([650, 750])
        root.addWidget(prompt_answer_split, 1)
        self.chat_progress = QProgressBar()
        self.chat_progress.setVisible(False)
        root.addWidget(self.chat_progress)

        self._register_tooltip(self.chat_question, "tip.chat_question")
        self._register_tooltip(self.btn_chat_send, "tip.chat_send")
        self._register_tooltip(self.btn_chat_clear, "tip.chat_clear")
        self._register_tooltip(self.btn_chat_save, "tip.chat_save")
        return tab

    def _build_system_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(10, 10, 10, 10)
        action_row = QHBoxLayout()
        self.btn_collect_snapshot = QPushButton()
        self.btn_collect_snapshot.setObjectName("PrimaryButton")
        self.btn_collect_snapshot.clicked.connect(self.collect_snapshot)
        action_row.addWidget(self.btn_collect_snapshot)
        self.btn_clear_snapshot = QPushButton()
        self.btn_clear_snapshot.clicked.connect(self._clear_snapshot)
        action_row.addWidget(self.btn_clear_snapshot)
        self.btn_save_snapshot = QPushButton()
        self.btn_save_snapshot.clicked.connect(self.save_snapshot_as)
        action_row.addWidget(self.btn_save_snapshot)
        action_row.addStretch(1)
        self.snapshot_progress = QProgressBar()
        self.snapshot_progress.setVisible(False)
        self.snapshot_progress.setMinimumWidth(320)
        action_row.addWidget(self.snapshot_progress)
        root.addLayout(action_row)
        self.snapshot_notice = QLabel()
        self.snapshot_notice.setWordWrap(True)
        self.snapshot_notice.setObjectName("Muted")
        root.addWidget(self.snapshot_notice)
        self.snapshot_editor = QPlainTextEdit()
        self.snapshot_editor.textChanged.connect(self._snapshot_edited)
        root.addWidget(self.snapshot_editor, 1)
        self._register_tooltip(self.btn_collect_snapshot, "tip.collect_snapshot")
        self._register_tooltip(self.snapshot_editor, "tip.snapshot_editor")
        return tab

    def _build_reports_tab(self) -> QWidget:
        tab = QWidget()
        root = QVBoxLayout(tab)
        root.setContentsMargins(10, 10, 10, 10)
        row = QHBoxLayout()
        self.btn_reports_refresh = QPushButton()
        self.btn_reports_refresh.clicked.connect(self.refresh_reports)
        row.addWidget(self.btn_reports_refresh)
        self.btn_reports_open = QPushButton()
        self.btn_reports_open.clicked.connect(self.open_selected_report)
        row.addWidget(self.btn_reports_open)
        self.btn_reports_folder = QPushButton()
        self.btn_reports_folder.clicked.connect(self.open_output_folder)
        row.addWidget(self.btn_reports_folder)
        self.btn_reports_generate_pdf = QPushButton()
        self.btn_reports_generate_pdf.clicked.connect(self.export_pdf_as)
        row.addWidget(self.btn_reports_generate_pdf)
        row.addStretch(1)
        self.label_output_path = QLabel()
        self.label_output_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label_output_path.setObjectName("Muted")
        row.addWidget(self.label_output_path, 2)
        root.addLayout(row)
        self.reports_table = QTableWidget(0, 4)
        self.reports_table.verticalHeader().setVisible(False)
        self.reports_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.reports_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.reports_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.reports_table.horizontalHeader().setStretchLastSection(True)
        self.reports_table.doubleClicked.connect(lambda _index: self.open_selected_report())
        root.addWidget(self.reports_table, 1)
        return tab

    def _build_settings_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        tab_layout.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(12, 12, 12, 12)

        interface_group = QGroupBox()
        interface_form = QFormLayout(interface_group)
        self.combo_interface_language = QComboBox()
        for code, name in LANGUAGES.items():
            self.combo_interface_language.addItem(name, code)
        self.combo_interface_language.currentIndexChanged.connect(self._interface_language_changed)
        interface_form.addRow(self._make_label("settings.interface_language"), self.combo_interface_language)
        self.combo_theme = QComboBox()
        for theme_id, name in self.theme_manager.available_themes():
            self.combo_theme.addItem(name, theme_id)
        self.combo_theme.currentIndexChanged.connect(self._theme_changed)
        interface_form.addRow(self._make_label("settings.theme"), self.combo_theme)
        self.check_tooltips = QCheckBox()
        self.check_tooltips.toggled.connect(self._tooltips_changed)
        interface_form.addRow(self._make_label("settings.tooltips"), self.check_tooltips)
        root.addWidget(interface_group)

        storage_group = QGroupBox()
        storage_form = QFormLayout(storage_group)
        output_widget = QWidget()
        output_layout = QHBoxLayout(output_widget)
        output_layout.setContentsMargins(0, 0, 0, 0)
        self.edit_output_dir = QLineEdit()
        output_layout.addWidget(self.edit_output_dir, 1)
        self.btn_browse_output = QPushButton()
        self.btn_browse_output.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(self.btn_browse_output)
        storage_form.addRow(self._make_label("settings.output_dir"), output_widget)
        self.check_auto_markdown = QCheckBox()
        storage_form.addRow(self._make_label("settings.auto_markdown"), self.check_auto_markdown)
        self.check_auto_pdf = QCheckBox()
        storage_form.addRow(self._make_label("settings.auto_pdf"), self.check_auto_pdf)
        root.addWidget(storage_group)

        ollama_group = QGroupBox()
        ollama_form = QFormLayout(ollama_group)
        self.edit_ollama_endpoint = QLineEdit()
        ollama_form.addRow(self._make_label("settings.ollama_endpoint"), self.edit_ollama_endpoint)
        self.spin_ollama_timeout = QSpinBox()
        self.spin_ollama_timeout.setRange(30, 7200)
        self.spin_ollama_timeout.setSuffix(" s")
        ollama_form.addRow(self._make_label("settings.ollama_timeout"), self.spin_ollama_timeout)
        self.spin_temperature = QDoubleSpinBox()
        self.spin_temperature.setRange(0.0, 2.0)
        self.spin_temperature.setDecimals(2)
        self.spin_temperature.setSingleStep(0.05)
        ollama_form.addRow(self._make_label("settings.temperature"), self.spin_temperature)
        self.spin_num_predict = QSpinBox()
        self.spin_num_predict.setRange(128, 32768)
        ollama_form.addRow(self._make_label("settings.num_predict"), self.spin_num_predict)
        self.spin_num_ctx = QSpinBox()
        self.spin_num_ctx.setRange(2048, 1_048_576)
        self.spin_num_ctx.setSingleStep(2048)
        ollama_form.addRow(self._make_label("settings.num_ctx"), self.spin_num_ctx)
        self.spin_context_budget = QSpinBox()
        self.spin_context_budget.setRange(12_000, 2_000_000)
        self.spin_context_budget.setSingleStep(10_000)
        self.spin_context_budget.setSuffix(" chars")
        ollama_form.addRow(self._make_label("settings.context_budget"), self.spin_context_budget)
        self.edit_keep_alive = QLineEdit()
        ollama_form.addRow(self._make_label("settings.keep_alive"), self.edit_keep_alive)
        root.addWidget(ollama_group)

        tts_group = QGroupBox()
        tts_form = QFormLayout(tts_group)
        self.check_tts_enabled = QCheckBox()
        tts_form.addRow(self._make_label("settings.tts_enabled"), self.check_tts_enabled)
        self.check_tts_auto_analysis = QCheckBox()
        tts_form.addRow(self._make_label("settings.tts_auto"), self.check_tts_auto_analysis)
        voice_widget = QWidget()
        voice_layout = QHBoxLayout(voice_widget)
        voice_layout.setContentsMargins(0, 0, 0, 0)
        self.combo_tts_voice = QComboBox()
        voice_layout.addWidget(self.combo_tts_voice, 1)
        self.btn_refresh_voices = QPushButton()
        self.btn_refresh_voices.clicked.connect(self.refresh_voices)
        voice_layout.addWidget(self.btn_refresh_voices)
        tts_form.addRow(self._make_label("settings.tts_voice"), voice_widget)
        self.spin_tts_rate = QSpinBox()
        self.spin_tts_rate.setRange(-10, 10)
        tts_form.addRow(self._make_label("settings.tts_rate"), self.spin_tts_rate)
        self.spin_tts_volume = QSpinBox()
        self.spin_tts_volume.setRange(0, 100)
        self.spin_tts_volume.setSuffix(" %")
        tts_form.addRow(self._make_label("settings.tts_volume"), self.spin_tts_volume)
        root.addWidget(tts_group)

        event_group = QGroupBox()
        event_form = QFormLayout(event_group)
        self.spin_default_limit = QSpinBox()
        self.spin_default_limit.setRange(0, 1_000_000)
        self.spin_default_limit.setSpecialValueText("All")
        event_form.addRow(self._make_label("settings.default_limit"), self.spin_default_limit)
        self.label_completeness = QLabel()
        self.label_completeness.setWordWrap(True)
        self.label_completeness.setObjectName("Muted")
        event_form.addRow(self._make_label("settings.export_integrity"), self.label_completeness)
        root.addWidget(event_group)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_save_settings = QPushButton()
        self.btn_save_settings.setObjectName("PrimaryButton")
        self.btn_save_settings.clicked.connect(self.save_settings)
        buttons.addWidget(self.btn_save_settings)
        root.addLayout(buttons)
        root.addStretch(1)

        self.settings_group_boxes = [
            interface_group,
            storage_group,
            ollama_group,
            tts_group,
            event_group,
        ]
        self._register_tooltip(self.edit_ollama_endpoint, "tip.ollama_endpoint")
        self._register_tooltip(self.spin_context_budget, "tip.context_budget")
        self._register_tooltip(self.check_auto_markdown, "tip.auto_markdown")
        self._register_tooltip(self.check_auto_pdf, "tip.auto_pdf")
        self._register_tooltip(self.check_tooltips, "tip.tooltips_toggle")
        return tab

    def _build_help_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        self.help_browser = QTextBrowser()
        self.help_browser.setOpenExternalLinks(True)
        layout.addWidget(self.help_browser)
        return tab

    def _make_label(self, key: str) -> QLabel:
        label = QLabel()
        label.setProperty("translation_key", key)
        return label

    def _register_tooltip(self, widget: QWidget, key: str) -> None:
        self.tooltip_targets.append((widget, key))

    def _wire_delayed_startup(self) -> None:
        QTimer.singleShot(250, self.refresh_channels)
        QTimer.singleShot(650, self.refresh_models)
        QTimer.singleShot(900, self.refresh_voices)
        QTimer.singleShot(1000, self.refresh_reports)

    # ------------------------------------------------------------------
    # Translation and theme
    # ------------------------------------------------------------------
    def retranslate_ui(self) -> None:
        self.setWindowTitle(f"{self.t('app.title')} v{VERSION}")
        self.menu_file.setTitle(self.t("menu.file"))
        self.menu_view.setTitle(self.t("menu.view"))
        self.menu_help.setTitle(self.t("menu.help"))
        self.action_import.setText(self.t("action.import_evtx"))
        self.action_save_markdown.setText(self.t("action.save_markdown"))
        self.action_export_pdf.setText(self.t("action.export_pdf"))
        self.action_exit.setText(self.t("action.exit"))
        self.action_refresh_channels.setText(self.t("action.refresh_channels"))
        self.action_refresh_models.setText(self.t("action.refresh_models"))
        self.action_open_output.setText(self.t("action.open_output"))
        self.action_help.setText(self.t("action.help"))
        self.action_about.setText(self.t("action.about"))

        tab_keys = [
            "tab.events",
            "tab.dashboard",
            "tab.analysis",
            "tab.chat",
            "tab.system",
            "tab.reports",
            "tab.settings",
            "tab.help",
        ]
        for index, key in enumerate(tab_keys):
            self.tabs.setTabText(index, self.t(key))

        # Event tab
        self.btn_refresh_channels.setText(self.t("button.refresh_channels"))
        self.btn_import_evtx.setText(self.t("button.import_evtx"))
        self.btn_load_selected.setText(self.t("button.load_selected"))
        self.btn_cancel_load.setText(self.t("button.cancel"))
        self.label_event_limit.setText(self.t("label.event_limit"))
        self.label_xpath.setText(self.t("label.xpath"))
        self.edit_xpath.setPlaceholderText(self.t("placeholder.xpath"))
        self.label_search.setText(self.t("label.search"))
        self.edit_event_search.setPlaceholderText(self.t("placeholder.search_events"))
        self.label_level_filter.setText(self.t("label.level_filter"))
        self._fill_combo(self.combo_level_filter, [
            ("filter.all", "all"),
            ("filter.critical", "critical"),
            ("filter.errors", "errors"),
            ("filter.warnings_errors", "warnings_errors"),
            ("filter.information", "information"),
        ])
        self.btn_save_markdown.setText(self.t("button.save_markdown"))
        self.btn_analyze_loaded.setText(self.t("button.analyze_loaded"))
        self.btn_clear_events.setText(self.t("button.clear"))
        self.spin_event_limit.setSpecialValueText(self.t("state.all"))
        self.event_detail_group.setTitle(self.t("group.event_details"))

        # Dashboard
        for key, (title, _value) in self.dashboard_cards.items():
            title.setText(self.t(f"dashboard.{key}"))
        self.chart_providers.set_empty_text(self.t("dashboard.no_data"))
        self.chart_event_ids.set_empty_text(self.t("dashboard.no_data"))
        self.chart_providers.set_data(self.t("dashboard.top_providers"), self._provider_chart_data())
        self.chart_event_ids.set_data(self.t("dashboard.top_event_ids"), self._event_id_chart_data())
        self.table_top_channels.setHorizontalHeaderLabels([self.t("dashboard.channel"), self.t("dashboard.count")])
        self.table_timeline.setHorizontalHeaderLabels([self.t("dashboard.hour"), self.t("dashboard.count")])

        # Analysis
        self.label_analysis_model.setText(self.t("label.model"))
        self.btn_refresh_models.setText(self.t("button.refresh_models"))
        self.label_response_language.setText(self.t("label.response_language"))
        self._fill_language_combo(self.combo_response_language)
        self.label_analysis_scope.setText(self.t("label.analysis_scope"))
        self._fill_combo(self.combo_analysis_scope, [
            ("scope.warnings_errors", "warnings_errors"),
            ("scope.errors", "errors"),
            ("scope.all", "all"),
            ("scope.selected", "selected"),
        ])
        self.label_analysis_mode.setText(self.t("label.analysis_mode"))
        self._fill_combo(self.combo_analysis_mode, [
            ("mode.auto", "auto"),
            ("mode.single", "single"),
            ("mode.chunked", "chunked"),
        ])
        self.check_include_snapshot.setText(self.t("check.include_snapshot"))
        self.check_include_raw_xml.setText(self.t("check.include_raw_xml"))
        self.btn_run_analysis.setText(self.t("button.run_analysis"))
        self.btn_cancel_analysis.setText(self.t("button.cancel"))
        self.btn_save_analysis.setText(self.t("button.save_analysis"))
        self.btn_export_pdf.setText(self.t("button.export_pdf"))
        self.btn_speak_analysis.setText(self.t("button.speak"))
        self._update_analysis_input_label()

        # Chat
        self.label_chat_model.setText(self.t("label.model"))
        self.label_chat_context.setText(self.t("chat.context", events=len(self.events), analysis=self.t("state.yes") if self.current_analysis else self.t("state.no")))
        self.chat_prompt_group.setTitle(self.t("group.chat_question"))
        self.chat_answer_group.setTitle(self.t("group.chat_answer"))
        self.chat_question.setPlaceholderText(self.t("placeholder.chat_question"))
        self.btn_chat_send.setText(self.t("button.send"))
        self.btn_chat_stop.setText(self.t("button.stop"))
        self.btn_chat_clear.setText(self.t("button.clear_chat"))
        self.btn_chat_speak.setText(self.t("button.speak"))
        self.btn_chat_save.setText(self.t("button.save_conversation"))
        self._render_chat_history()

        # System
        self.btn_collect_snapshot.setText(self.t("button.collect_snapshot"))
        self.btn_clear_snapshot.setText(self.t("button.clear"))
        self.btn_save_snapshot.setText(self.t("button.save_snapshot"))
        self.snapshot_notice.setText(self.t("system.notice"))

        # Reports
        self.btn_reports_refresh.setText(self.t("button.refresh"))
        self.btn_reports_open.setText(self.t("button.open_selected"))
        self.btn_reports_folder.setText(self.t("button.open_folder"))
        self.btn_reports_generate_pdf.setText(self.t("button.export_pdf"))
        self.reports_table.setHorizontalHeaderLabels([
            self.t("reports.modified"),
            self.t("reports.type"),
            self.t("reports.name"),
            self.t("reports.size"),
        ])
        self.label_output_path.setText(str(self.settings.output_dir()))

        # Settings
        for label in self.settings_tab.findChildren(QLabel):
            key = label.property("translation_key")
            if key:
                label.setText(self.t(str(key)))
        group_keys = [
            "group.interface",
            "group.storage",
            "group.ollama",
            "group.tts",
            "group.event_defaults",
        ]
        for group, key in zip(self.settings_group_boxes, group_keys):
            group.setTitle(self.t(key))
        self.check_tooltips.setText(self.t("check.enabled"))
        self.btn_browse_output.setText(self.t("button.browse"))
        self.check_auto_markdown.setText(self.t("check.enabled"))
        self.check_auto_pdf.setText(self.t("check.enabled"))
        self.check_tts_enabled.setText(self.t("check.enabled"))
        self.check_tts_auto_analysis.setText(self.t("check.enabled"))
        self.btn_refresh_voices.setText(self.t("button.refresh"))
        self.label_completeness.setText(self.t("settings.export_integrity_text"))
        self.spin_default_limit.setSpecialValueText(self.t("state.all"))
        self.btn_save_settings.setText(self.t("button.save_settings"))

        self.event_model.retranslate()
        self._populate_event_tree(self._channels)
        self._load_help_page()
        self._apply_tooltips()
        self._update_visible_count()
        self._set_status(self.t("status.ready"))

    def _fill_combo(self, combo: QComboBox, items: list[tuple[str, str]]) -> None:
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for key, value in items:
            combo.addItem(self.t(key), value)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _fill_language_combo(self, combo: QComboBox) -> None:
        current = combo.currentData() or self.settings.value("analysis/response_language", "en", str)
        combo.blockSignals(True)
        combo.clear()
        for code, name in RESPONSE_LANGUAGE_NAMES.items():
            combo.addItem(name, code)
        index = combo.findData(current)
        combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _interface_language_changed(self) -> None:
        code = self.combo_interface_language.currentData()
        if not code:
            return
        self.settings.set_value("interface/language", code)
        self.translator.set_language(str(code))
        self.retranslate_ui()

    def _response_language_changed(self) -> None:
        code = self.combo_response_language.currentData()
        if code:
            self.settings.set_value("analysis/response_language", code)

    def _theme_changed(self) -> None:
        theme_id = self.combo_theme.currentData()
        if theme_id:
            self.settings.set_value("interface/theme", theme_id)
            self._apply_theme()

    def _apply_theme(self) -> None:
        theme_id = self.settings.value("interface/theme", "aurora", str)
        try:
            QApplication.instance().setStyleSheet(self.theme_manager.stylesheet(theme_id))
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Could not apply theme")
            self.statusBar().showMessage(str(exc), 5000)
        self.chart_providers.update()
        self.chart_event_ids.update()

    def _tooltips_changed(self, enabled: bool) -> None:
        self.settings.set_value("interface/tooltips", enabled)
        self._apply_tooltips()

    def _apply_tooltips(self) -> None:
        enabled = self.settings.value("interface/tooltips", True, bool)
        for widget, key in self.tooltip_targets:
            widget.setToolTip(self.t(key) if enabled else "")

    def _load_help_page(self) -> None:
        code = self.translator.language
        path = HELP_DIR / f"{code}.html"
        if not path.is_file():
            path = HELP_DIR / "en.html"
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            text = text.replace("{{VERSION}}", VERSION).replace("{{RELEASE_DATE}}", RELEASE_DATE).replace("{{SOURCE_URL}}", SOURCE_URL)
            self.help_browser.setHtml(text)
        else:
            self.help_browser.setPlainText(self.t("help.unavailable"))

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------
    def _restore_settings_to_ui(self) -> None:
        self._set_combo_data(self.combo_interface_language, self.settings.value("interface/language", "en", str))
        self._set_combo_data(self.combo_theme, self.settings.value("interface/theme", "aurora", str))
        self.check_tooltips.setChecked(self.settings.value("interface/tooltips", True, bool))
        self.spin_event_limit.setValue(self.settings.value("event/default_limit", 1000, int))
        self.spin_default_limit.setValue(self.settings.value("event/default_limit", 1000, int))
        self.check_auto_markdown.setChecked(self.settings.value("event/auto_save_markdown", True, bool))
        self.check_auto_pdf.setChecked(self.settings.value("report/auto_save_pdf", True, bool))
        self.edit_output_dir.setText(str(self.settings.output_dir()))
        self.edit_ollama_endpoint.setText(self.settings.value("ollama/endpoint", "http://127.0.0.1:11434", str))
        self.spin_ollama_timeout.setValue(self.settings.value("ollama/timeout", 900, int))
        self.spin_temperature.setValue(self.settings.value("ollama/temperature", 0.15, float))
        self.spin_num_predict.setValue(self.settings.value("ollama/num_predict", 4096, int))
        self.spin_num_ctx.setValue(self.settings.value("ollama/num_ctx", 32768, int))
        self.spin_context_budget.setValue(self.settings.value("analysis/context_char_budget", 90000, int))
        self.edit_keep_alive.setText(self.settings.value("ollama/keep_alive", "10m", str))
        self.check_tts_enabled.setChecked(self.settings.value("tts/enabled", False, bool))
        self.check_tts_auto_analysis.setChecked(self.settings.value("tts/auto_analysis", False, bool))
        self.spin_tts_rate.setValue(self.settings.value("tts/rate", 0, int))
        self.spin_tts_volume.setValue(self.settings.value("tts/volume", 100, int))
        self.check_include_snapshot.setChecked(self.settings.value("analysis/include_snapshot", True, bool))
        self.check_include_raw_xml.setChecked(self.settings.value("analysis/include_raw_xml", False, bool))
        self._set_combo_data(self.combo_analysis_scope, self.settings.value("analysis/scope", "warnings_errors", str))
        self._set_combo_data(self.combo_analysis_mode, self.settings.value("analysis/mode", "auto", str))
        self._fill_language_combo(self.combo_response_language)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save_settings(self) -> None:
        self.settings.set_value("interface/language", self.combo_interface_language.currentData())
        self.settings.set_value("interface/theme", self.combo_theme.currentData())
        self.settings.set_value("interface/tooltips", self.check_tooltips.isChecked())
        self.settings.set_output_dir(self.edit_output_dir.text().strip() or self.settings.default_output_dir())
        self.settings.set_value("event/default_limit", self.spin_default_limit.value())
        self.settings.set_value("event/auto_save_markdown", self.check_auto_markdown.isChecked())
        self.settings.set_value("report/auto_save_pdf", self.check_auto_pdf.isChecked())
        self.settings.set_value("ollama/endpoint", self.edit_ollama_endpoint.text().strip())
        self.settings.set_value("ollama/timeout", self.spin_ollama_timeout.value())
        self.settings.set_value("ollama/temperature", self.spin_temperature.value())
        self.settings.set_value("ollama/num_predict", self.spin_num_predict.value())
        self.settings.set_value("ollama/num_ctx", self.spin_num_ctx.value())
        self.settings.set_value("analysis/context_char_budget", self.spin_context_budget.value())
        self.settings.set_value("ollama/keep_alive", self.edit_keep_alive.text().strip())
        self.settings.set_value("tts/enabled", self.check_tts_enabled.isChecked())
        self.settings.set_value("tts/auto_analysis", self.check_tts_auto_analysis.isChecked())
        self.settings.set_value("tts/rate", self.spin_tts_rate.value())
        self.settings.set_value("tts/volume", self.spin_tts_volume.value())
        self.settings.set_value("tts/voice", self.combo_tts_voice.currentData() or "")
        self.settings.set_value("analysis/include_snapshot", self.check_include_snapshot.isChecked())
        self.settings.set_value("analysis/include_raw_xml", self.check_include_raw_xml.isChecked())
        self.settings.set_value("analysis/scope", self.combo_analysis_scope.currentData() or "warnings_errors")
        self.settings.set_value("analysis/mode", self.combo_analysis_mode.currentData() or "auto")
        self.settings.set_value("analysis/response_language", self.combo_response_language.currentData() or "en")
        self.settings.set_value("ollama/model", self.combo_analysis_model.currentText().strip())
        self.settings.sync()
        self.spin_event_limit.setValue(self.spin_default_limit.value())
        self.label_output_path.setText(str(self.settings.output_dir()))
        self._apply_tooltips()
        self._set_status(self.t("status.settings_saved"))
        self.refresh_reports()

    def browse_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, self.t("dialog.output_dir"), self.edit_output_dir.text())
        if selected:
            self.edit_output_dir.setText(selected)

    # ------------------------------------------------------------------
    # Generic worker management
    # ------------------------------------------------------------------
    def start_worker(
        self,
        name: str,
        function: Callable,
        *,
        kwargs: dict | None = None,
        on_result: Callable[[object], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_chunk: Callable[[str], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        if name in self.workers:
            self.cancel_worker(name)
        worker = TaskWorker(function, **(kwargs or {}))
        self.workers[name] = worker
        if on_result:
            worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error or (lambda detail: self._show_worker_error(name, detail)))
        worker.signals.progress.connect(on_progress or (lambda value, total: self._set_status(f"{value}/{total}")))
        worker.signals.status.connect(on_status or self._set_status)
        if on_chunk:
            worker.signals.chunk.connect(on_chunk)

        def finished() -> None:
            self.workers.pop(name, None)
            self._update_worker_count()
            if on_finished:
                on_finished()

        worker.signals.finished.connect(finished)
        self._update_worker_count()
        self.thread_pool.start(worker)

    def cancel_worker(self, name: str) -> None:
        worker = self.workers.get(name)
        if worker:
            worker.cancel()
            self._set_status(self.t("status.cancelling"))

    def _show_worker_error(self, name: str, detail: str) -> None:
        LOGGER.error("Worker %s failed:\n%s", name, detail)
        self._last_worker_error = detail
        message = detail.strip().splitlines()[-1] if detail.strip() else self.t("error.unknown")
        if "cancelled" in message.casefold() or "canceled" in message.casefold() or "abgebrochen" in message.casefold():
            self._set_status(self.t("status.cancelled"))
            return
        QMessageBox.critical(self, self.t("error.title"), message)
        self._set_status(message)

    def _update_worker_count(self) -> None:
        count = len(self.workers)
        self.worker_count_label.setText(self.t("status.background_tasks", count=count))

    @staticmethod
    def _update_progress_bar(bar: QProgressBar, value: int, total: int) -> None:
        bar.setVisible(True)
        if total <= 0:
            bar.setRange(0, 0)
        else:
            bar.setRange(0, total)
            bar.setValue(max(0, min(value, total)))

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # ------------------------------------------------------------------
    # Event sources, tree, loading and selection
    # ------------------------------------------------------------------
    def refresh_channels(self) -> None:
        self.btn_refresh_channels.setEnabled(False)
        self._update_progress_bar(self.event_progress, 0, 0)
        self.start_worker(
            "channel_refresh",
            enumerate_channels,
            on_result=self._channels_loaded,
            on_progress=lambda v, t: self._update_progress_bar(self.event_progress, v, t),
            on_finished=lambda: (self.btn_refresh_channels.setEnabled(True), self.event_progress.setVisible(False)),
        )

    def _channels_loaded(self, result: object) -> None:
        self._channels = list(result or [])
        self._populate_event_tree(self._channels)
        self._set_status(self.t("status.channels_loaded", count=len(self._channels)))

    def _populate_event_tree(self, channels: list[str]) -> None:
        selected_payload = self.current_source_payload
        self.event_tree.blockSignals(True)
        self.event_tree.clear()
        root = QTreeWidgetItem([self.t("tree.local_viewer")])
        root.setExpanded(True)
        self.event_tree.addTopLevelItem(root)

        quick = QTreeWidgetItem([self.t("tree.quick_views")])
        quick.setExpanded(True)
        root.addChild(quick)
        quick_admin = QTreeWidgetItem([self.t("tree.quick_admin")])
        quick_admin.setData(0, Qt.ItemDataRole.UserRole, {
            "channels": ["Application", "System"],
            "xpath": "*[System[(Level=1 or Level=2 or Level=3)]]",
            "label": self.t("tree.quick_admin"),
        })
        quick.addChild(quick_admin)
        quick_errors = QTreeWidgetItem([self.t("tree.quick_errors")])
        quick_errors.setData(0, Qt.ItemDataRole.UserRole, {
            "channels": ["Application", "System"],
            "xpath": "*[System[(Level=1 or Level=2)]]",
            "label": self.t("tree.quick_errors"),
        })
        quick.addChild(quick_errors)

        windows_logs = QTreeWidgetItem([self.t("tree.windows_logs")])
        windows_logs.setExpanded(True)
        root.addChild(windows_logs)
        service_logs = QTreeWidgetItem([self.t("tree.app_service_logs")])
        root.addChild(service_logs)

        classic = {
            "Application": "tree.application",
            "Security": "tree.security",
            "Setup": "tree.setup",
            "System": "tree.system",
            "ForwardedEvents": "tree.forwarded",
        }
        channel_items: dict[str, QTreeWidgetItem] = {}
        for channel, key in classic.items():
            if channel in channels:
                item = QTreeWidgetItem([self.t(key)])
                item.setData(0, Qt.ItemDataRole.UserRole, channel)
                windows_logs.addChild(item)
                channel_items[channel] = item

        branch_cache: dict[tuple[str, ...], QTreeWidgetItem] = {(): service_logs}
        for channel in channels:
            if channel in classic:
                continue
            parts = [part for part in channel.split("/") if part]
            if not parts:
                parts = [channel]
            parent = service_logs
            for index, part in enumerate(parts):
                prefix = tuple(parts[: index + 1])
                item = branch_cache.get(prefix)
                if item is None:
                    item = QTreeWidgetItem([part])
                    parent.addChild(item)
                    branch_cache[prefix] = item
                parent = item
            parent.setData(0, Qt.ItemDataRole.UserRole, channel)
            channel_items[channel] = parent

        self.event_tree.blockSignals(False)
        if selected_payload is not None:
            iterator = self.event_tree.findItems("*", Qt.MatchFlag.MatchWildcard | Qt.MatchFlag.MatchRecursive)
            for item in iterator:
                if item.data(0, Qt.ItemDataRole.UserRole) == selected_payload:
                    self.event_tree.setCurrentItem(item)
                    break

    def _on_tree_selection_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None) -> None:
        del previous
        if current is None:
            return
        payload = current.data(0, Qt.ItemDataRole.UserRole)
        if payload:
            label = current.text(0)
            self.label_current_source.setText(self.t("label.selected_source", source=label))

    def load_selected_source(self) -> None:
        item = self.event_tree.currentItem()
        if item is None:
            QMessageBox.information(self, self.t("info.title"), self.t("info.select_log"))
            return
        payload = item.data(0, Qt.ItemDataRole.UserRole)
        if not payload:
            QMessageBox.information(self, self.t("info.title"), self.t("info.select_log"))
            return
        limit = self.spin_event_limit.value()
        xpath = self.edit_xpath.text().strip()
        label = item.text(0)
        if isinstance(payload, dict):
            channels = list(payload.get("channels", []))
            xpath = xpath or str(payload.get("xpath", ""))
            self._start_event_load(
                query_channels,
                {"channels": channels, "limit": limit, "xpath": xpath},
                source_label=str(payload.get("label", label)),
                kind="channels",
                source_payload=payload,
            )
        else:
            self._start_event_load(
                query_channel,
                {"channel": str(payload), "limit": limit, "xpath": xpath},
                source_label=str(payload),
                kind="channel",
                source_payload=payload,
            )

    def import_evtx(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            self.t("dialog.import_evtx"),
            "",
            self.t("dialog.evtx_filter"),
        )
        if not file_path:
            return
        self._start_event_load(
            query_log_file,
            {"file_path": file_path, "limit": self.spin_event_limit.value(), "xpath": self.edit_xpath.text().strip()},
            source_label=file_path,
            kind="file",
            source_payload=file_path,
        )

    def _start_event_load(
        self,
        function: Callable,
        kwargs: dict,
        *,
        source_label: str,
        kind: str,
        source_payload: object,
    ) -> None:
        self.btn_load_selected.setEnabled(False)
        self.btn_import_evtx.setEnabled(False)
        self._update_progress_bar(self.event_progress, 0, 0)
        self._set_status(self.t("status.loading_events", source=source_label))

        def loaded(result: object) -> None:
            self.current_source = source_label
            self.current_source_kind = kind
            self.current_source_payload = source_payload
            self._events_loaded(list(result or []))

        self.start_worker(
            "event_load",
            function,
            kwargs=kwargs,
            on_result=loaded,
            on_progress=lambda v, t: self._update_progress_bar(self.event_progress, v, t),
            on_finished=lambda: self._finish_event_load(),
        )

    def _finish_event_load(self) -> None:
        self.btn_load_selected.setEnabled(True)
        self.btn_import_evtx.setEnabled(True)
        self.event_progress.setVisible(False)

    def _events_loaded(self, events: list[EventRecord]) -> None:
        self.events = events
        self.current_markdown = ""
        self.current_markdown_path = ""
        self.current_analysis = ""
        self.current_analysis_path = ""
        self.analysis_output.clear()
        self.chat_history.clear()
        self.chat_history_view.clear()
        self.chat_answer.clear()
        self.event_model.set_events(events)
        self.event_table.resizeColumnsToContents()
        self.event_table.setColumnWidth(8, max(420, self.event_table.columnWidth(8)))
        self.label_current_source.setText(self.t("label.loaded_source", source=self.current_source))
        self._update_dashboard()
        self._update_visible_count()
        self._update_analysis_input_label()
        self.label_chat_context.setText(self.t("chat.context", events=len(self.events), analysis=self.t("state.no")))
        self._set_status(self.t("status.events_loaded", count=len(events)))
        if self.settings.value("event/auto_save_markdown", True, bool) and events:
            self._auto_save_markdown()

    def _filter_events(self) -> None:
        self.event_proxy.set_search_text(self.edit_event_search.text())
        self.event_proxy.set_level_filter(str(self.combo_level_filter.currentData() or "all"))
        self._update_visible_count()

    def _update_visible_count(self) -> None:
        visible = self.event_proxy.rowCount()
        total = len(self.events)
        self.label_visible_count.setText(self.t("label.visible_count", visible=visible, total=total))

    def _event_selection_changed(self, selected: QItemSelection, deselected: QItemSelection) -> None:
        del selected, deselected
        indexes = self.event_table.selectionModel().selectedRows()
        if not indexes:
            self.event_detail.clear()
            self._update_analysis_input_label()
            return
        event = self.event_proxy.data(indexes[0], EVENT_ROLE)
        if isinstance(event, EventRecord):
            lines = [
                f"{self.t('table.time')}: {event.timestamp}",
                f"{self.t('table.level')}: {event.level} ({event.level_value})",
                f"{self.t('table.provider')}: {event.provider}",
                f"{self.t('table.event_id')}: {event.event_id}",
                f"{self.t('table.channel')}: {event.channel}",
                f"{self.t('table.record_id')}: {event.record_id}",
                f"{self.t('table.task')}: {event.task}",
                f"{self.t('table.computer')}: {event.computer}",
                "",
                self.t("detail.message"),
                event.message or "",
                "",
                self.t("detail.complete_fields"),
            ]
            lines.extend(f"{path} = {value}" for path, value in event.fields)
            lines.extend(["", self.t("detail.raw_xml"), event.raw_xml])
            self.event_detail.setPlainText("\n".join(lines))
        self._update_analysis_input_label()

    def selected_events(self) -> list[EventRecord]:
        rows = self.event_table.selectionModel().selectedRows()
        result: list[EventRecord] = []
        seen: set[int] = set()
        for proxy_index in rows:
            source_index = self.event_proxy.mapToSource(proxy_index)
            if source_index.row() in seen:
                continue
            seen.add(source_index.row())
            if 0 <= source_index.row() < len(self.event_model.events):
                result.append(self.event_model.events[source_index.row()])
        return result

    def clear_events(self) -> None:
        self.events = []
        self.current_source = ""
        self.current_markdown = ""
        self.current_analysis = ""
        self.event_model.set_events([])
        self.event_detail.clear()
        self.analysis_output.clear()
        self._update_dashboard()
        self._update_visible_count()
        self._update_analysis_input_label()
        self._set_status(self.t("status.cleared"))

    # ------------------------------------------------------------------
    # Markdown export
    # ------------------------------------------------------------------
    def _suggest_base_name(self, suffix: str = "") -> str:
        source_name = Path(self.current_source).stem if self.current_source else "EventLog"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix_part = f"_{suffix}" if suffix else ""
        return f"{sanitize_filename(source_name)}_{timestamp}{suffix_part}"

    def save_markdown_as(self) -> None:
        if not self.events:
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_events"))
            return
        default = self.settings.output_dir() / f"{self._suggest_base_name()}.md"
        path, _ = QFileDialog.getSaveFileName(self, self.t("dialog.save_markdown"), str(default), "Markdown (*.md)")
        if path:
            self._save_markdown_to(path, auto=False)

    def _auto_save_markdown(self) -> None:
        path = self.settings.output_dir() / f"{self._suggest_base_name()}.md"
        self._save_markdown_to(str(path), auto=True)

    def _save_markdown_to(self, path: str, *, auto: bool) -> None:
        self._update_progress_bar(self.event_progress, 0, len(self.events) or 1)
        title = self.t("report.markdown_title")
        snapshot = self.snapshot_editor.toPlainText().strip()

        def saved(result: object) -> None:
            payload = dict(result or {})
            self.current_markdown_path = str(payload.get("path", path))
            self.current_markdown = str(payload.get("markdown", ""))
            self._set_status(self.t("status.markdown_saved", path=self.current_markdown_path))
            self.refresh_reports()
            if not auto:
                QMessageBox.information(self, self.t("info.title"), self.t("info.saved_to", path=self.current_markdown_path))

        self.start_worker(
            "markdown_export",
            write_markdown,
            kwargs={
                "events": list(self.events),
                "title": title,
                "source": self.current_source,
                "output_path": path,
                "system_snapshot": snapshot,
            },
            on_result=saved,
            on_progress=lambda v, t: self._update_progress_bar(self.event_progress, v, t),
            on_finished=lambda: self.event_progress.setVisible(False),
        )

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    def _provider_chart_data(self) -> list[tuple[str, int]]:
        return Counter(event.provider or self.t("state.unknown") for event in self.events).most_common(10)

    def _event_id_chart_data(self) -> list[tuple[str, int]]:
        return Counter(event.event_id or self.t("state.unknown") for event in self.events).most_common(10)

    def _update_dashboard(self) -> None:
        counts = {
            "total": len(self.events),
            "critical": sum(1 for e in self.events if level_rank(e) == 1),
            "errors": sum(1 for e in self.events if level_rank(e) == 2),
            "warnings": sum(1 for e in self.events if level_rank(e) == 3),
            "information": sum(1 for e in self.events if level_rank(e) == 4),
        }
        for key, value in counts.items():
            self.dashboard_cards[key][1].setText(f"{value:,}")
        self.chart_providers.set_data(self.t("dashboard.top_providers"), self._provider_chart_data())
        self.chart_event_ids.set_data(self.t("dashboard.top_event_ids"), self._event_id_chart_data())
        channels = Counter(event.channel or event.source or self.t("state.unknown") for event in self.events).most_common(20)
        self.table_top_channels.setRowCount(len(channels))
        for row, (name, count) in enumerate(channels):
            self.table_top_channels.setItem(row, 0, QTableWidgetItem(name))
            self.table_top_channels.setItem(row, 1, QTableWidgetItem(str(count)))
        timeline = Counter()
        for event in self.events:
            stamp = event.timestamp[:13] if event.timestamp else self.t("state.unknown")
            timeline[stamp] += 1
        timeline_items = sorted(timeline.items(), reverse=True)[:48]
        self.table_timeline.setRowCount(len(timeline_items))
        for row, (stamp, count) in enumerate(timeline_items):
            self.table_timeline.setItem(row, 0, QTableWidgetItem(stamp))
            self.table_timeline.setItem(row, 1, QTableWidgetItem(str(count)))

    # ------------------------------------------------------------------
    # Ollama model discovery and analysis
    # ------------------------------------------------------------------
    def _ollama_config(self) -> dict:
        endpoint = self.edit_ollama_endpoint.text().strip() or self.settings.value("ollama/endpoint", "http://127.0.0.1:11434", str)
        model = self.combo_analysis_model.currentText().strip() or self.combo_chat_model.currentText().strip()
        return {
            "endpoint": endpoint,
            "model": model,
            "timeout": self.spin_ollama_timeout.value(),
            "temperature": self.spin_temperature.value(),
            "num_predict": self.spin_num_predict.value(),
            "num_ctx": self.spin_num_ctx.value(),
            "context_char_budget": self.spin_context_budget.value(),
            "keep_alive": self.edit_keep_alive.text().strip() or "10m",
            "response_language": self.combo_response_language.currentData() or "en",
            "include_raw_xml": self.check_include_raw_xml.isChecked(),
            "mode": self.combo_analysis_mode.currentData() or "auto",
        }

    def refresh_models(self) -> None:
        endpoint = self.edit_ollama_endpoint.text().strip() or "http://127.0.0.1:11434"
        timeout = self.spin_ollama_timeout.value() if hasattr(self, "spin_ollama_timeout") else 900
        self.btn_refresh_models.setEnabled(False)

        def list_models_task(*, cancel_event=None, progress_callback=None, status_callback=None, chunk_callback=None):
            del cancel_event, chunk_callback
            if progress_callback:
                progress_callback(0, 0)
            if status_callback:
                status_callback(f"Querying Ollama at {endpoint}…")
            return OllamaClient(endpoint, timeout).list_models()

        self.start_worker(
            "model_refresh",
            list_models_task,
            on_result=self._models_loaded,
            on_error=self._models_error,
            on_finished=lambda: self.btn_refresh_models.setEnabled(True),
        )

    def _models_loaded(self, result: object) -> None:
        models = list(result or [])
        current = self.settings.value("ollama/model", "", str) or self.combo_analysis_model.currentText()
        for combo in (self.combo_analysis_model, self.combo_chat_model):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(models)
            if current in models:
                combo.setCurrentText(current)
            combo.blockSignals(False)
        self._set_status(self.t("status.models_loaded", count=len(models)))

    def _models_error(self, detail: str) -> None:
        LOGGER.warning("Ollama model refresh failed: %s", detail)
        message = detail.strip().splitlines()[-1] if detail.strip() else self.t("error.unknown")
        self._set_status(message)
        self.analysis_status.setText(self.t("status.ollama_unavailable", error=message))

    def _sync_model_selection(self, model: str) -> None:
        if not model:
            return
        sender = self.sender()
        target = self.combo_chat_model if sender is self.combo_analysis_model else self.combo_analysis_model
        if target.currentText() != model:
            target.blockSignals(True)
            target.setCurrentText(model)
            target.blockSignals(False)
        self.settings.set_value("ollama/model", model)

    def _update_analysis_input_label(self) -> None:
        scope = self.combo_analysis_scope.currentData() if hasattr(self, "combo_analysis_scope") else "warnings_errors"
        selected = self.selected_events() if hasattr(self, "event_table") else []
        event_count = len(select_events_for_scope(self.events, str(scope or "warnings_errors"), selected))
        self.label_analysis_input.setText(self.t("analysis.input_summary", count=event_count, selected=len(selected)))

    def _go_to_analysis_and_run(self) -> None:
        self.tabs.setCurrentWidget(self.analysis_tab)
        self.run_analysis()

    def run_analysis(self) -> None:
        if not self.events:
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_events"))
            return
        config = self._ollama_config()
        if not config["model"]:
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_model"))
            return
        scope = str(self.combo_analysis_scope.currentData() or "warnings_errors")
        selected = self.selected_events()
        analysis_events = select_events_for_scope(self.events, scope, selected)
        if scope == "selected" and not selected:
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_selected_events"))
            return
        self.settings.set_value("analysis/scope", scope)
        self.settings.set_value("analysis/mode", self.combo_analysis_mode.currentData() or "auto")
        self.settings.set_value("analysis/include_snapshot", self.check_include_snapshot.isChecked())
        self.settings.set_value("analysis/include_raw_xml", self.check_include_raw_xml.isChecked())
        self.current_analysis = ""
        self._streamed_analysis = ""
        self.analysis_output.clear()
        self.analysis_status.setText(self.t("status.analysis_started", count=len(analysis_events)))
        self._update_progress_bar(self.analysis_progress, 0, 0)
        self.btn_run_analysis.setEnabled(False)

        existing_snapshot = self.snapshot_editor.toPlainText().strip()
        include_snapshot = self.check_include_snapshot.isChecked()
        source_name = self.current_source

        def analysis_task(*, cancel_event=None, progress_callback=None, status_callback=None, chunk_callback=None):
            snapshot = existing_snapshot
            if include_snapshot and not snapshot:
                status_callback("Collecting system snapshot before analysis…")
                snapshot_result = collect_system_snapshot(
                    cancel_event=cancel_event,
                    progress_callback=lambda _v, _t: None,
                    status_callback=status_callback,
                )
                snapshot = str(snapshot_result.get("markdown", ""))
            result = analyze_events(
                events=analysis_events,
                source=source_name,
                system_snapshot=snapshot,
                config=config,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
                status_callback=status_callback,
                chunk_callback=chunk_callback,
            )
            result["snapshot"] = snapshot
            return result

        self.start_worker(
            "analysis",
            analysis_task,
            on_result=self._analysis_finished,
            on_progress=lambda v, t: self._update_progress_bar(self.analysis_progress, v, t),
            on_status=self._analysis_status_changed,
            on_chunk=self._append_analysis_chunk,
            on_finished=self._analysis_worker_finished,
        )

    def _analysis_status_changed(self, text: str) -> None:
        self.analysis_status.setText(text)
        self._set_status(text)

    def _append_analysis_chunk(self, text: str) -> None:
        self._streamed_analysis += text
        self.analysis_output.moveCursor(QTextCursor.MoveOperation.End)
        self.analysis_output.insertPlainText(text)
        self.analysis_output.ensureCursorVisible()

    def _analysis_finished(self, result: object) -> None:
        payload = dict(result or {})
        self.current_analysis = str(payload.get("analysis", self._streamed_analysis)).strip()
        snapshot = str(payload.get("snapshot", "")).strip()
        if snapshot and not self.snapshot_editor.toPlainText().strip():
            self.system_snapshot = snapshot
            self.snapshot_editor.setPlainText(snapshot)
        if self.current_analysis and self.analysis_output.toPlainText().strip() != self.current_analysis:
            self.analysis_output.setPlainText(self.current_analysis)
        self.analysis_status.setText(self.t("status.analysis_complete", mode=payload.get("mode", ""), chunks=payload.get("chunks", 1)))
        self.label_chat_context.setText(self.t("chat.context", events=len(self.events), analysis=self.t("state.yes")))
        self._auto_save_analysis()
        if self.settings.value("report/auto_save_pdf", True, bool):
            self._auto_save_pdf()
        if self.settings.value("tts/enabled", False, bool) and self.settings.value("tts/auto_analysis", False, bool):
            self.speak(self.current_analysis)

    def _analysis_worker_finished(self) -> None:
        self.analysis_progress.setVisible(False)
        self.btn_run_analysis.setEnabled(True)

    def _auto_save_analysis(self) -> None:
        if not self.current_analysis:
            return
        path = self.settings.output_dir() / f"{self._suggest_base_name('analysis')}.md"
        body = f"# {self.t('report.analysis_title')}\n\n- Source: `{self.current_source}`\n- Model: `{self.combo_analysis_model.currentText()}`\n- Generated: {datetime.now().isoformat(timespec='seconds')}\n\n{self.current_analysis}\n"
        path.write_text(body, encoding="utf-8-sig")
        self.current_analysis_path = str(path)
        self.refresh_reports()

    def save_analysis_as(self) -> None:
        if not self.current_analysis:
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_analysis"))
            return
        default = self.settings.output_dir() / f"{self._suggest_base_name('analysis')}.md"
        path, _ = QFileDialog.getSaveFileName(self, self.t("dialog.save_analysis"), str(default), "Markdown (*.md);;Text (*.txt)")
        if path:
            Path(path).write_text(self.current_analysis, encoding="utf-8-sig")
            self.current_analysis_path = path
            self.refresh_reports()
            self._set_status(self.t("status.analysis_saved", path=path))

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def send_chat_question(self) -> None:
        question = self.chat_question.toPlainText().strip()
        if not question:
            return
        config = self._ollama_config()
        if not config["model"]:
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_model"))
            return
        self._pending_chat_question = question
        self._streamed_chat_answer = ""
        self.chat_answer.clear()
        self.btn_chat_send.setEnabled(False)
        self._update_progress_bar(self.chat_progress, 0, 0)
        self.start_worker(
            "chat",
            chat_about_events,
            kwargs={
                "question": question,
                "events": list(self.events),
                "source": self.current_source,
                "analysis": self.current_analysis,
                "system_snapshot": self.snapshot_editor.toPlainText().strip(),
                "history": list(self.chat_history),
                "config": config,
            },
            on_result=self._chat_finished,
            on_progress=lambda v, t: self._update_progress_bar(self.chat_progress, v, t),
            on_status=self._set_status,
            on_chunk=self._append_chat_chunk,
            on_finished=self._chat_worker_finished,
        )

    def _append_chat_chunk(self, text: str) -> None:
        self._streamed_chat_answer += text
        self.chat_answer.moveCursor(QTextCursor.MoveOperation.End)
        self.chat_answer.insertPlainText(text)
        self.chat_answer.ensureCursorVisible()

    def _chat_finished(self, result: object) -> None:
        answer = str(result or self._streamed_chat_answer).strip()
        if self.chat_answer.toPlainText().strip() != answer:
            self.chat_answer.setPlainText(answer)
        self.chat_history.append({"role": "user", "content": self._pending_chat_question})
        self.chat_history.append({"role": "assistant", "content": answer})
        self.chat_question.clear()
        self._render_chat_history()
        self._set_status(self.t("status.chat_complete"))

    def _chat_worker_finished(self) -> None:
        self.btn_chat_send.setEnabled(True)
        self.chat_progress.setVisible(False)

    def _render_chat_history(self) -> None:
        parts = ["<html><body>"]
        for item in self.chat_history:
            role = item.get("role", "user")
            content = str(item.get("content", ""))
            rendered = markdown_lib.markdown(html.escape(content), extensions=["tables", "fenced_code", "sane_lists"])
            heading = self.t("chat.you") if role == "user" else self.t("chat.ollama")
            css_class = "user" if role == "user" else "assistant"
            parts.append(f"<div class='{css_class}'><h3>{html.escape(heading)}</h3>{rendered}</div><hr>")
        parts.append("</body></html>")
        self.chat_history_view.setHtml("".join(parts))
        self.chat_history_view.verticalScrollBar().setValue(self.chat_history_view.verticalScrollBar().maximum())

    def clear_chat(self) -> None:
        self.chat_history.clear()
        self.chat_history_view.clear()
        self.chat_question.clear()
        self.chat_answer.clear()

    def save_chat_as(self) -> None:
        if not self.chat_history and not self.chat_answer.toPlainText().strip():
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_chat"))
            return
        default = self.settings.output_dir() / f"{self._suggest_base_name('conversation')}.md"
        path, _ = QFileDialog.getSaveFileName(self, self.t("dialog.save_chat"), str(default), "Markdown (*.md)")
        if not path:
            return
        lines = [f"# {self.t('report.chat_title')}", "", f"- Source: `{self.current_source}`", ""]
        for item in self.chat_history:
            heading = self.t("chat.you") if item.get("role") == "user" else self.t("chat.ollama")
            lines.extend([f"## {heading}", "", str(item.get("content", "")), ""])
        Path(path).write_text("\n".join(lines), encoding="utf-8-sig")
        self.refresh_reports()
        self._set_status(self.t("status.chat_saved", path=path))

    # ------------------------------------------------------------------
    # System snapshot and TTS
    # ------------------------------------------------------------------
    def collect_snapshot(self) -> None:
        self.btn_collect_snapshot.setEnabled(False)
        self._update_progress_bar(self.snapshot_progress, 0, 0)
        self.start_worker(
            "snapshot",
            collect_system_snapshot,
            on_result=self._snapshot_finished,
            on_progress=lambda v, t: self._update_progress_bar(self.snapshot_progress, v, t),
            on_finished=lambda: (self.btn_collect_snapshot.setEnabled(True), self.snapshot_progress.setVisible(False)),
        )

    def _snapshot_finished(self, result: object) -> None:
        payload = dict(result or {})
        self.system_snapshot = str(payload.get("markdown", ""))
        self.snapshot_editor.setPlainText(self.system_snapshot)
        self._set_status(self.t("status.snapshot_complete"))

    def _snapshot_edited(self) -> None:
        self.system_snapshot = self.snapshot_editor.toPlainText()

    def _clear_snapshot(self) -> None:
        self.system_snapshot = ""
        self.snapshot_editor.clear()

    def save_snapshot_as(self) -> None:
        text = self.snapshot_editor.toPlainText().strip()
        if not text:
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_snapshot"))
            return
        default = self.settings.output_dir() / f"{self._suggest_base_name('system_snapshot')}.md"
        path, _ = QFileDialog.getSaveFileName(self, self.t("dialog.save_snapshot"), str(default), "Markdown (*.md)")
        if path:
            Path(path).write_text(text, encoding="utf-8-sig")
            self.refresh_reports()

    def refresh_voices(self) -> None:
        def voices_task(*, cancel_event=None, progress_callback=None, status_callback=None, chunk_callback=None):
            del cancel_event, chunk_callback
            if progress_callback:
                progress_callback(0, 0)
            if status_callback:
                status_callback("Reading Windows SAPI voices…")
            return list_voices()

        self.start_worker("voices", voices_task, on_result=self._voices_loaded, on_error=lambda _detail: None)

    def _voices_loaded(self, result: object) -> None:
        voices = list(result or [])
        current = self.settings.value("tts/voice", "", str)
        self.combo_tts_voice.clear()
        self.combo_tts_voice.addItem(self.t("tts.default_voice"), "")
        for voice_id, description in voices:
            self.combo_tts_voice.addItem(description, voice_id)
        self._set_combo_data(self.combo_tts_voice, current)

    def speak(self, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        if not self.check_tts_enabled.isChecked():
            QMessageBox.information(self, self.t("info.title"), self.t("info.tts_disabled"))
            return
        self.start_worker(
            "tts",
            speak_text,
            kwargs={
                "text": clean,
                "voice_id": self.combo_tts_voice.currentData() or "",
                "rate": self.spin_tts_rate.value(),
                "volume": self.spin_tts_volume.value(),
            },
            on_progress=lambda _v, _t: self._set_status(self.t("status.speaking")),
        )

    # ------------------------------------------------------------------
    # PDF reports and report browser
    # ------------------------------------------------------------------
    def export_pdf_as(self) -> None:
        if not self.events:
            QMessageBox.information(self, self.t("info.title"), self.t("info.no_events"))
            return
        default = self.settings.output_dir() / f"{self._suggest_base_name('report')}.pdf"
        path, _ = QFileDialog.getSaveFileName(self, self.t("dialog.export_pdf"), str(default), "PDF (*.pdf)")
        if path:
            self._generate_pdf(path, auto=False)

    def _auto_save_pdf(self) -> None:
        path = self.settings.output_dir() / f"{self._suggest_base_name('report')}.pdf"
        self._generate_pdf(str(path), auto=True)

    def _generate_pdf(self, path: str, *, auto: bool) -> None:
        try:
            generated = generate_pdf_report(
                events=list(self.events),
                source=self.current_source,
                analysis_markdown=self.current_analysis,
                system_snapshot_markdown=self.snapshot_editor.toPlainText().strip(),
                output_path=path,
                title=self.t("report.pdf_title"),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("PDF generation failed")
            QMessageBox.critical(self, self.t("error.title"), str(exc))
            return
        self.refresh_reports()
        self._set_status(self.t("status.pdf_saved", path=generated))
        if not auto:
            QMessageBox.information(self, self.t("info.title"), self.t("info.saved_to", path=generated))

    def refresh_reports(self) -> None:
        output_dir = self.settings.output_dir()
        self.label_output_path.setText(str(output_dir))
        files = [path for path in output_dir.iterdir() if path.is_file() and path.suffix.casefold() in {".md", ".pdf", ".txt"}]
        files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        self.reports_table.setRowCount(len(files))
        for row, path in enumerate(files):
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            values = [modified, path.suffix.upper().lstrip("."), path.name, self._format_size(stat.st_size)]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                self.reports_table.setItem(row, column, item)
        self.reports_table.resizeColumnsToContents()
        self.reports_table.horizontalHeader().setStretchLastSection(True)

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if value < 1024 or unit == "GiB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GiB"

    def open_selected_report(self) -> None:
        row = self.reports_table.currentRow()
        if row < 0:
            return
        item = self.reports_table.item(row, 0)
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_output_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.output_dir())))

    # ------------------------------------------------------------------
    # About, close
    # ------------------------------------------------------------------
    def show_about(self) -> None:
        QMessageBox.about(
            self,
            self.t("action.about"),
            self.t(
                "about.text",
                version=VERSION,
                date=RELEASE_DATE,
                source=SOURCE_URL,
            ),
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        for worker in list(self.workers.values()):
            worker.cancel()
        self.save_settings()
        event.accept()
