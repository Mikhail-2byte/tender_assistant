import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests.test_tender_registry import registry_window


def toggle(window, app):
    QTest.keyClick(window.focusWidget() or window, Qt.Key.Key_QuoteLeft,
                   Qt.KeyboardModifier.ControlModifier)
    app.processEvents()


def test_shortcut_toggles_shared_chat_and_restores_focus(registry_window, app):
    window = registry_window
    chat = window.agent_page.chat
    selector = chat.model_selector
    window.registry.search.setFocus()
    toggle(window, app)
    assert window.agent_dock.isVisible()
    assert window.pages.currentIndex() == 0
    assert chat.input.hasFocus()
    assert window.agent_page.parentWidget() == window.agent_dock
    chat.input.setPlainText("Черновик агента")
    toggle(window, app)
    assert not window.agent_dock.isVisible()
    assert window.agent_page.parentWidget() == window.agent_home
    assert window.registry.search.hasFocus()
    toggle(window, app)
    assert chat.input.toPlainText() == "Черновик агента"
    assert chat.model_selector is selector
    assert chat in window.ai_service.sources
    assert len(window.ai_service.sources) == 3
    assert [s.key().toString() for s in window.agent_dock_shortcuts] == ["Ctrl+`", "Ctrl+Ё"]


def test_fullpage_and_bottom_share_chat(registry_window, app):
    window = registry_window
    toggle(window, app)
    window.agent_page.chat.input.setPlainText("Общий черновик")
    window.pages.setCurrentIndex(2)
    app.processEvents()
    assert not window.agent_dock.isVisible()
    assert window.agent_page.isVisible()
    assert window.agent_page.parentWidget() == window.agent_home
    assert window.agent_page.chat.input.toPlainText() == "Общий черновик"
    assert window.agent_page.chat.input.height() == 92
    toggle(window, app)
    assert window.pages.currentIndex() == 0
    assert window.agent_dock.isVisible()
    assert window.agent_page.chat.input.toPlainText() == "Общий черновик"


def test_toggle_preserves_unsaved_card_without_guard(registry_window, app, monkeypatch):
    window = registry_window
    window.open_registry_tender(window.repository.all()[0].id)
    window.note.setPlainText("Черновик карточки")
    monkeypatch.setattr(window, "guard_changes", lambda: pytest.fail("Dock toggle must not navigate or guard the card"))
    toggle(window, app)
    toggle(window, app)
    assert window.dirty
    assert window.note.toPlainText() == "Черновик карточки"
    assert window.registry_pages.currentWidget() == window.registry_card_page


def test_close_button_remembers_height_and_keeps_history(registry_window, app):
    window = registry_window
    toggle(window, app)
    window.agent_page.chat.input.setPlainText("Неотправленный текст")
    height = window.agent_dock.height()
    window.agent_dock.close()
    app.processEvents()
    assert not window.agent_dock.isVisible()
    assert window.settings.get("agent_dock_height") == height
    assert window.agent_page.chat.input.toPlainText() == "Неотправленный текст"
    assert window.agent_page.flush()
    assert window.agent_page.history.active["draft"] == "Неотправленный текст"


@pytest.mark.parametrize('hide', ['minimize', 'hide'])
def test_restoring_window_keeps_agent_in_dock(registry_window, app, hide):
    window = registry_window
    toggle(window, app)
    page = window.agent_page
    page.chat.input.setPlainText('Несохранённый вопрос')
    page.run_quick('Сводка исполнения')
    messages = list(page.history.active['messages'])
    selector = page.chat.model_selector
    if hide == 'minimize':
        window.showMinimized()
    else:
        window.hide()
    app.processEvents()
    window.showNormal()
    for _ in range(5):
        app.processEvents()
    assert window.agent_dock.isVisible()
    assert page.parentWidget() == window.agent_dock
    assert page.chat.input.isVisible()
    assert page.chat.input.toPlainText() == 'Несохранённый вопрос'
    assert page.history.active['messages'] == messages
    assert page.chat.model_selector is selector


@pytest.mark.parametrize("size", [(1366, 768), (1920, 1080)])
def test_bottom_panel_leaves_work_area_visible(registry_window, app, size):
    window = registry_window
    window.resize(*size)
    before = window.size()
    toggle(window, app)
    for _ in range(5):
        app.processEvents()
    assert window.size() == before
    assert window.registry.height() >= 200
    assert window.agent_dock.height() <= size[1] * .6
    assert window.agent_page.chat.input.isVisible()
    assert window.agent_page.chat.scroll.height() > 30
