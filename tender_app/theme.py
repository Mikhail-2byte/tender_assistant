"""Shared dark design tokens. Presentation only: no storage or model dependencies."""

from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QApplication

ENTITY_COLORS = {"company": "#c5b4ed", "tender": "#8ba9ff", "spec": "#6bc7b2"}


def style_record_link(button, kind):
    color = ENTITY_COLORS["tender" if kind == "tender" else "spec"]
    button.setStyleSheet(f"QPushButton {{ color: {color}; text-align: left; }}")

COLORS = {
    "background": "#16191e",
    "sidebar": "#111419",
    "surface": "#1c2027",
    "input": "#20262f",
    "hover": "#29313e",
    "border": "#343d4b",
    "text": "#ecf0f6",
    "muted": "#a3adbd",
    "accent": "#8ba9ff",
    "primary": "#4e6fcb",
    "selection": "#25314a",
    "green": "#8bdbb3",
    "warning": "#edc184",
    "danger": "#f19b9f",
}


def dark_palette():
    p = QPalette()
    for role, color in {
        "Window": "#16191e",
        "WindowText": "#ecf0f6",
        "Base": "#20262f",
        "AlternateBase": "#232a34",
        "Text": "#ecf0f6",
        "Button": "#262e3a",
        "ButtonText": "#ecf0f6",
        "Highlight": "#3a5180",
        "HighlightedText": "#ffffff",
        "ToolTipBase": "#293344",
        "ToolTipText": "#ecf0f6",
        "PlaceholderText": "#a3adbd",
        "Link": "#aec6ff",
        "Light": "#44516a",
        "Midlight": "#354154",
        "Mid": "#2a3340",
        "Dark": "#111419",
        "Shadow": "#090d12",
    }.items():
        p.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#8993a3"))
    return p


def style_popup(widget):
    """Popup palettes must not depend on the OS light/dark setting."""
    widget.setPalette(dark_palette())


def apply_theme(window):
    app = QApplication.instance()
    if app:
        app.setPalette(dark_palette())
        font = QFont("Segoe UI", 10)
        if not QFont("Segoe UI").exactMatch():
            font = QFont("Arial", 10)
        app.setFont(font)
    window.setPalette(dark_palette())
    window.setStyleSheet(STYLESHEET)


STYLESHEET = """
QWidget { color: #ecf0f6; font-size: 10pt; }
QMainWindow, QDialog { background: #16191e; }
QWidget#registryPanel { background: #191d24; border-right: 1px solid #343d4b; }
QWidget#cardPanel { background: #16191e; }
QFrame#section { background: #1c2027; border: 1px solid #343d4b; border-radius: 9px; }
QFrame#section QWidget { background: transparent; }
QLabel { background: transparent; }
QLabel#tenderNumber { font-size: 15pt; font-weight: 600; color: #8ba9ff; }
QLabel#heading { font-size: 13pt; font-weight: 600; }
QLabel#customerName { font-size: 18pt; font-weight: 600; color: #c5b4ed; }
QLabel#companyName { color: #c5b4ed; font-weight: 600; }
QLabel#specNumber { color: #6bc7b2; font-weight: 600; }
QLabel#subjectHeading { font-size: 15pt; font-weight: 600; color: #ecf0f6; }
QLabel#sectionHeading { font-size: 11pt; font-weight: 600; padding-bottom: 4px; }
QLabel#fieldLabel, QLabel#muted { color: #a3adbd; font-size: 9pt; }
QLabel#warning { color: #edc184; }
QLabel#deadlineSummary { font-size: 11pt; font-weight: 500; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QDateEdit, QTimeEdit, QSpinBox, QDoubleSpinBox {
 background: #20262f; color: #ecf0f6; border: 1px solid #455166; border-radius: 6px;
 padding: 6px 8px; selection-background-color: #3a5180; selection-color: white;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #8ba9ff; }
QLineEdit:disabled, QComboBox:disabled { color: #8993a3; background: #1c2129; border-color: #343d4b; }
QComboBox { padding-right: 26px; min-height: 20px; }
QComboBox::drop-down { width: 24px; border: none; }
QComboBox QAbstractItemView { background: #20262f; color: #ecf0f6; selection-background-color: #3a5180; selection-color: white; }
QPushButton { background: #262e3a; color: #e6ecf5; border: 1px solid #455166; border-radius: 6px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover { background: #303c50; border-color: #64789b; }
QPushButton:pressed { background: #3a4c6a; }
QPushButton:focus, QToolButton:focus { border: 1px solid #8ba9ff; }
QPushButton:disabled { background: #1f252e; color: #8993a3; border-color: #343d4b; }
QPushButton#primary { background: #4e6fcb; color: white; border-color: #4e6fcb; font-weight: 600; }
QPushButton#primary:hover { background: #7598ff; }
QPushButton#primary:disabled { background: #2b3750; color: #abb8d4; border-color: #3e4e6e; }
QPushButton[secondary="true"] { background: transparent; border-color: transparent; color: #b6c5df; }
QPushButton[secondary="true"]:hover { background: #29313e; border-color: #455166; }
QToolButton { color: #b8c9e5; background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 5px 7px; }
QToolButton:hover { background: #29313e; }
QToolButton:checked, QPushButton#assistantToggle:checked { background: #283650; color: #c5d6ff; border-color: #506991; }
QToolButton#quickAction { border: 1px solid #455166; background: #262e3a; padding: 8px 28px 8px 12px; }
QToolButton#quickAction::menu-button { width: 20px; border-left: 1px solid #455166; }
QToolButton#assistantToggle, QPushButton#assistantToggle { border: 1px solid #455166; border-radius: 6px; padding: 6px 10px; }
QListWidget, QTreeWidget, QTableWidget { background: #191d24; color: #ecf0f6; border: none; outline: none; alternate-background-color: #232a34; selection-background-color: #25314a; selection-color: #ecf0f6; }
QListWidget::item { border: none; }
QTreeWidget::item:selected, QTableWidget::item:selected { background: #25314a; color: #ecf0f6; }
QTableWidget { gridline-color: #343d4b; }
QHeaderView::section { background: #232a34; color: #b7c2d3; padding: 8px; border: none; border-bottom: 1px solid #455166; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QSplitter::handle, QMainWindow::separator { background: #343d4b; width: 2px; height: 2px; }
QSplitter::handle:hover, QMainWindow::separator:hover { background: #8ba9ff; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 0; }
QScrollBar::handle:vertical { background: #485368; border-radius: 4px; min-height: 30px; margin: 2px; }
QScrollBar::handle:vertical:hover { background: #687f9f; }
QScrollBar:horizontal { height: 10px; background: transparent; }
QScrollBar::handle:horizontal { background: #485368; border-radius: 4px; min-width: 30px; margin: 2px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #a3adbd; padding: 10px 14px; margin-right: 6px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #d9e4ff; border-bottom-color: #8ba9ff; }
QTabBar::tab:hover:!selected { background: #252c37; }
QMenuBar, QMenu { background: #20262f; color: #ecf0f6; }
QMenu { border: 1px solid #455166; padding: 5px; }
QMenu::item { padding: 8px 22px 8px 12px; }
QMenu::item:selected { background: #334767; }
QMenu::item:disabled { color: #8993a3; }
QMenu::separator { height: 1px; background: #455166; margin: 4px; }
QToolTip { background: #293344; color: #ecf0f6; border: 1px solid #566885; padding: 7px; }
QStatusBar { background: #15191f; color: #a3adbd; border-top: 1px solid #343d4b; min-height: 26px; }
QStatusBar::item { border: none; }
QCheckBox { spacing: 7px; color: #c5cedc; }
QCheckBox::indicator { width: 16px; height: 16px; }
QGroupBox { border: 1px solid #455166; border-radius: 7px; margin-top: 12px; padding-top: 12px; }
QDockWidget { color: #d6e1f4; background: #191e25; border-left: 1px solid #343d4b; }
QDockWidget::title { background: #191e25; padding: 12px; font-weight: 600; }
QWidget#appSidebar { background: #111419; border-right: 1px solid #343d4b; }
QLabel#brand { font-size: 15pt; font-weight: 600; padding: 10px 8px 22px; }
QLabel#navCaption { color: #a3adbd; font-size: 8pt; padding: 0 10px 10px; }
QPushButton#navItem { text-align: left; background: transparent; border: 1px solid transparent; color: #aebbd0; padding: 11px 10px; }
QPushButton#navItem:checked { background: #25314a; color: #ecf0f6; border-color: #354868; }
QPushButton#navItem:hover { background: #29313e; }
QToolBar#appToolbar { background: #181c22; border: none; border-bottom: 1px solid #343d4b; padding: 4px 8px; spacing: 4px; }
QLabel#breadcrumb { color: #b6c2d5; padding: 0 8px; }
QLabel#saveIndicator { color: #a3adbd; padding: 2px 8px; font-size: 9pt; }
QFrame#historyComposer { background: #202833; border: 1px solid #3f4d63; border-radius: 7px; }
QFrame#historyEntry { background: #1c2027; border: 1px solid #343d4b; border-left: 2px solid #607da6; border-radius: 6px; }
QWidget#appSidebar[collapsed="true"] QPushButton#navItem { padding: 9px 0; text-align: center; }
QPushButton#railToggle { background: transparent; border: 1px solid transparent; padding: 6px; color: #b7c9e8; }
QPushButton#railToggle:hover { background: #29313e; }
QPushButton#railToggle:checked { background: #25314a; border-color: #455166; }
QLineEdit#globalSearch { background: #20262f; border: 1px solid #455166; border-radius: 7px; padding: 7px 10px; }
QLineEdit#globalSearch:focus { border-color: #8ba9ff; }
QFrame#positionCard { background: #20262f; border: 1px solid #3c4658; border-radius: 8px; }
QFrame#positionCard QLabel { background: transparent; }
QWidget#quantityBox { background: #283244; border-radius: 6px; }
QTabBar#navigationModes::tab { padding: 5px 7px; margin-right: 2px; font-size: 9pt; }
QPushButton:focus, QToolButton:focus { border: 1px solid #8ba9ff; }
QLabel#historyDate { color: #b3c7e8; font-size: 9pt; font-weight: 600; }
"""
