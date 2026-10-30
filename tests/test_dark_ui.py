"""Regression tests for the native dark shell, without changing storage semantics."""

from pathlib import Path
import hashlib
from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.settings import Settings
from tender_app.theme import COLORS
from tender_app.ui_icons import icon


def test_shell_is_native_and_has_search_shortcut(window, app):
    assert window.app_sidebar.isVisible()
    assert window.menuBar().isHidden()
    assert window.navigation_buttons[0].isChecked()
    window.search_shortcut.activated.emit()
    app.processEvents()
    assert window.global_search.hasFocus()
    assert not icon("folder").isNull()


def test_dark_palette_and_unsaved_feedback(window, app):
    assert window.palette().color(QPalette.ColorRole.Window).lightness() < 60
    assert window.palette().color(QPalette.ColorRole.WindowText).lightness() > 200
    before = window.repository.all()
    window.edits["platform"].setText("UI test")
    assert window.dirty
    assert "несохранённые" in window.save_indicator.text()
    assert window.repository.all() == before
    window.dirty = False


def test_resource_buttons_have_text_beside_icons(window):
    assert (
        window.folder_button.toolButtonStyle()
        == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    )
    assert (
        window.etp_button.toolButtonStyle()
        == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    )
    assert not window.folder_button.icon().isNull()
    assert not window.etp_button.icon().isNull()


def test_native_navigation_assistant_and_data_safety(tmp_path, app):
    db = tmp_path / "fictional.db"
    create_demo_database(db)
    repo = ExecutionRepository(db)
    win = ExecutionWindow(repo, Settings(tmp_path / "settings.json"), tmp_path)
    win.show()
    app.processEvents()
    before = repo.all(), repo.specifications()
    try:
        assert win.pages.tabBar().isHidden()
        win.navigation_buttons[1].click()
        QTest.qWait(20)
        assert win.pages.currentIndex() == 1
        assert win.navigation_buttons[1].isChecked()
        assert "Исполнение" in win.breadcrumb.text()
        win.shell_assistant.click()
        app.processEvents()
        assert win.execution_assistant.isVisible()
        assistant = win.execution_assistant
        old_count = assistant.messages.count()
        assistant.run_command("Сводка")
        assert assistant.messages.count() > old_count
        assistant.input.setPlainText("Мой черновик")
        assistant.run_command("Остатки")
        assert assistant.input.toPlainText() == "Мой черновик"
        win.navigation_buttons[0].click()
        QTest.qWait(20)
        assert win.pages.currentIndex() == 0
        assert win.navigation_buttons[0].isChecked()
        assert not win.execution_assistant.isVisible()
        assert (repo.all(), repo.specifications()) == before
    finally:
        win.dirty = False
        win.close()
        repo.close()


def test_primary_action_has_readable_white_text():
    def luminance(h):
        rgb = [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        rgb = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return sum(a * b for a, b in zip(rgb, (0.2126, 0.7152, 0.0722)))

    assert 1.05 / (luminance(COLORS["primary"]) + 0.05) >= 4.5


def test_narrow_execution_assistant_does_not_overlap_card(tmp_path, app):
    db = tmp_path / "narrow.db"
    create_demo_database(db)
    repo = ExecutionRepository(db)
    win = ExecutionWindow(repo, Settings(tmp_path / "narrow.json"), tmp_path)
    win.show()
    win.pages.setCurrentIndex(1)
    win.resize(1020, 740)
    win.execution_assistant_toggle.setChecked(True)
    QTest.qWait(40)
    try:
        assert win.execution.splitter.widget(0).isHidden()
        card = win.execution.scroll
        assistant = win.execution_assistant
        assert card.geometry().right() < assistant.geometry().left()
        assert assistant.geometry().right() <= win.execution.splitter.width()
        win.shell_list_button.click()
        QTest.qWait(30)
        assert not win.execution.splitter.widget(0).isHidden()
        assert not win.execution_assistant_toggle.isChecked()
    finally:
        win.dirty = False
        win.close()
        repo.close()
