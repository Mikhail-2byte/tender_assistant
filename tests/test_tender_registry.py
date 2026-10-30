import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.models import Tender
from tender_app.settings import Settings
from tender_app.tender_registry import NumberDelegate, TenderRegistry, latest_comment


def test_copy_number_shortcut_and_icon(app, monkeypatch):
    page = TenderRegistry()
    page.set_tenders([Tender(id=1, number='0000123/ТЕСТ'), Tender(id=2, number='0000456')])
    page.resize(1100, 450)
    page.show()
    edits = []
    opened = []
    page.edit_requested.connect(lambda *args: edits.append(args))
    page.tender_requested.connect(opened.append)
    class Clipboard:
        value = ''
        def setText(self, text):self.value = text
        def text(self):return self.value
    clipboard = Clipboard()
    monkeypatch.setattr(QApplication, 'clipboard', lambda: clipboard)
    try:
        page.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        page.table.setCurrentCell(1, 2)
        page.table.setFocus()
        app.processEvents()
        QTest.keyClick(page.table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert clipboard.text() == '0000123/ТЕСТ'
        item = page.table.item(0, 0)
        point = NumberDelegate.copy_rect(page.table.visualItemRect(item)).center()
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert clipboard.text() == '0000456' and not edits and not opened
        page.edit_cell_at(page.table.visualItemRect(item).center())
        assert not edits
        page.search.setText('0000123')
        item = page.table.item(0, 0)
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=NumberDelegate.copy_rect(page.table.visualItemRect(item)).center())
        assert clipboard.text() == '0000123/ТЕСТ' and not opened
        page.search.setText('нет совпадений')
        page.copy_number()
        assert clipboard.text() == '0000123/ТЕСТ'
        assert page.copy_number_shortcut.parent() is page.table
        assert page.copy_number_shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
        assert not page.table.isAncestorOf(page.search)
    finally:
        page.close()


def test_latest_comment_preserves_legacy_and_multiline():
    assert latest_comment("Прежние заметки\n\nЕщё абзац") == "Прежние заметки\n\nЕщё абзац"
    assert latest_comment("Старое\n\n[27.10.2026 12:00 МСК]\nПервый\n\n"
                          "[27.10.2026 13:00 МСК]\nПоследний\nПродолжение") == "Последний\nПродолжение"
    assert latest_comment("") == ""


def test_registry_comment_column_after_action_removed(app):
    page = TenderRegistry()
    try:
        page.set_tenders([Tender(id=1, number="ТЕСТ", action="Старое действие", comment="Заметка")])
        assert page.table.columnCount() == 7
        assert page.table.horizontalHeaderItem(6).text() == "Последний комментарий"
        assert page.table.item(0, 6).text() == "Заметка"
        edits = []
        page.edit_requested.connect(lambda *args: edits.append(args))
        page.edit_cell_at(page.table.visualItemRect(page.table.item(0, 6)).center())
        assert edits == [(1, "comment")]
    finally:
        page.close()


def test_selected_row_color_differs_from_status_with_and_without_focus(app):
    from tender_app.window import STATUS_COLORS
    page = TenderRegistry()
    page.resize(1200, 400)
    page.set_tenders([Tender(id=1, number="ТЕСТ", status="В работе")])
    page.show()
    try:
        page.table.setCurrentCell(0, 0)
        for focus in (page.table, page.search):
            focus.setFocus()
            app.processEvents()
            snapshot = page.table.viewport().grab().toImage()
            colors = []
            for column in (1, 4):
                rect = page.table.visualItemRect(page.table.item(0, column))
                colors.append(snapshot.pixelColor(rect.left() + 8, rect.top() + 3).name())
            assert colors == ["#654585", "#654585"]
            assert colors[0] != STATUS_COLORS["В работе"][1]
    finally:
        page.close()


def test_card_added_date_survives_edit_and_handles_missing_date(registry_window):
    from dataclasses import replace
    window = registry_window
    tender = window.repository.save(Tender(number="ДАТА", action="Добавлен из 1С"))
    window.open_registry_tender(tender.id)
    expected = window.added_caption.text()
    assert expected.startswith("Добавлен: ") and expected.endswith(" МСК")
    window.repository.save(replace(tender, customer="Новый заказчик"))
    window.open_registry_tender(tender.id)
    assert window.added_caption.text() == expected
    window.load_tender(replace(tender, created_at="2026-10-28T12:34:00+00:00"))
    assert window.added_caption.text() == "Добавлен: 28.10.2026 15:34 МСК"
    window.load_tender(replace(tender, created_at=""))
    assert window.added_caption.text() == "Добавлен: дата не указана"
    window.load_tender(Tender())
    assert window.added_caption.isHidden()


def test_filters_search_and_date_sort(app):
    page = TenderRegistry()
    try:
        page.set_tenders([
            Tender(id=1, number="Без срока", action="Позвонить", comment="Архивный текст"),
            Tender(id=2, number="Поздний", deadline_date="2027-01-01", status="Податься"),
            Tender(id=3, number="Ранний", deadline_date="2026-12-31", status="Переторжка"),
            Tender(id=4, number="Закрытый", status="Победа"),
        ])
        assert page.table.rowCount() == 3
        assert [page.table.item(r, 0).text() for r in range(3)] == ["Ранний", "Поздний", "Без срока"]
        page.search.setText("архивный")
        assert page.table.rowCount() == 1
        page.search.setText("Позвонить")
        assert page.table.rowCount() == 1
        page.search.clear()
        page.status_filter.setCurrentIndex(1)
        assert page.table.rowCount() == 4
        page.status_filter.setCurrentIndex(page.status_filter.findData("Победа"))
        assert page.table.rowCount() == 1
        page.search.setText("Нет совпадений")
        assert page.table.rowCount() == 0
        assert page.count.text() == "Найдено: 0"
    finally:
        page.close()


@pytest.fixture
def registry_window(tmp_path, app):
    path = tmp_path / "fictional.db"
    create_demo_database(path)
    repo = ExecutionRepository(path)
    window = ExecutionWindow(repo, Settings(tmp_path / "settings.json"), tmp_path)
    window.show()
    window.pages.setCurrentIndex(0)
    app.processEvents()
    yield window
    window.dirty = False
    window.close()
    repo.close()
    app.processEvents()


def test_registry_navigation_and_enter(registry_window, app):
    window = registry_window
    assert not window.save_action.isEnabled()
    page = window.registry
    page.status_filter.setCurrentIndex(1)
    page.table.setCurrentCell(0, 0)
    tender_id = page.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    page.table.setFocus()
    QTest.keyClick(page.table, Qt.Key.Key_Return)
    app.processEvents()
    assert window.pages.currentIndex() == 0
    assert window.registry_pages.currentWidget() == window.registry_card_page
    assert window.card.parentWidget() == window.registry_card_page
    assert not window.registry_panel.isVisible()
    assert not window.shell_list_button.isVisible()
    assert window.save_action.isEnabled()
    assert window.current.id == tender_id
    assert window.registry_back.isVisible()
    window.registry_back.click()
    app.processEvents()
    assert window.pages.currentIndex() == 0
    assert page.table.item(page.table.currentRow(), 0).data(Qt.ItemDataRole.UserRole) == tender_id
    assert not window.shell_assistant.isVisible()
    assert not window.shell_list_button.isVisible()
    assert not window.save_action.isEnabled()
    assert [window.pages.tabText(i) for i in range(window.pages.count())] == ["Реестр", "Исполнение", "Агент", "Реестр исполнения"]


def test_cancel_and_refresh(registry_window, monkeypatch, app):
    window = registry_window
    tender = window.repository.all()[0]
    monkeypatch.setattr(window, "guard_changes", lambda: False)
    window.open_registry_tender(tender.id)
    assert window.pages.currentIndex() == 0
    monkeypatch.setattr(window, "guard_changes", lambda: True)
    window.registry.status_filter.setCurrentIndex(1)
    window.open_registry_tender(tender.id)
    window.status.setCurrentText("Победа")
    assert window.save_tender()
    window.registry_back.click()
    app.processEvents()
    rows = window.registry.table
    row = next(r for r in range(rows.rowCount())
               if rows.item(r, 0).data(Qt.ItemDataRole.UserRole) == tender.id)
    assert rows.item(row, 4).text() == "Победа"


def test_double_click_and_preserved_table_state(registry_window, app):
    window = registry_window
    for index in range(45):
        window.repository.save(Tender(number=f"УЧЕБНЫЙ-{index:03}", comment="Проверка"))
    window.registry.set_tenders(window.repository.all())
    page = window.registry
    page.search.setText("УЧЕБНЫЙ")
    page.table.sortItems(0, Qt.SortOrder.DescendingOrder)
    page.table.setColumnWidth(1, 260)
    page.table.setCurrentCell(30, 0)
    page.table.scrollToItem(page.table.item(30, 0))
    app.processEvents()
    scroll = page.table.verticalScrollBar().value()
    target = page.table.item(30, 0)
    tender_id = target.data(Qt.ItemDataRole.UserRole)
    QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=page.table.visualItemRect(target).center())
    assert window.pages.currentIndex() == 0
    QTest.mouseDClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                     pos=page.table.visualItemRect(target).center())
    app.processEvents()
    assert window.pages.currentIndex() == 0
    assert window.registry_pages.currentWidget() == window.registry_card_page
    assert window.current.id == tender_id
    window.registry_back.click()
    app.processEvents()
    assert page.table.verticalScrollBar().value() == scroll
    assert page.table.columnWidth(1) == 260
    assert page.search.text() == "УЧЕБНЫЙ"
    assert page.table.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder


def test_registry_view_does_not_mark_new_tenders_seen(registry_window, app):
    window = registry_window
    tender = window.repository.all()[0]
    window.repository.connection.execute("UPDATE tenders SET is_unseen=1 WHERE id=?", (tender.id,))
    window.repository.connection.commit()
    page = window.registry
    page.status_filter.setCurrentIndex(1)
    page.set_tenders(window.repository.all())
    assert tender.id in window.repository.unseen_tender_ids()
    window.open_registry_tender(tender.id)
    assert tender.id not in window.repository.unseen_tender_ids()


def test_discard_on_return_does_not_prompt_again(registry_window, monkeypatch, app):
    window = registry_window
    tender = window.repository.all()[0]
    window.open_registry_tender(tender.id)
    window.note.setPlainText("Черновик для отмены")
    assert window.dirty
    calls = []
    monkeypatch.setattr(window, "guard_changes", lambda: calls.append(True) or True)
    window.registry_back.click()
    app.processEvents()
    assert window.pages.currentIndex() == 0
    assert not window.dirty
    assert len(calls) == 1


def test_status_change_removes_row_from_active_filter(registry_window, app):
    window = registry_window
    page = window.registry
    window.repository.save(Tender(number="УЧЕБНЫЙ-АКТИВНЫЙ"))
    page.set_tenders(window.repository.all())
    page.table.setCurrentCell(0, 0)
    tender_id = page.table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    before = page.table.rowCount()
    window.open_registry_tender(tender_id)
    window.status.setCurrentText("Победа")
    assert window.save_tender()
    window.registry_back.click()
    app.processEvents()
    assert page.table.rowCount() == before - 1
    assert all(page.table.item(r, 0).data(Qt.ItemDataRole.UserRole) != tender_id
               for r in range(page.table.rowCount()))


def test_cancel_return_keeps_card_and_navigation(registry_window, monkeypatch, app):
    window = registry_window
    tender = window.repository.all()[0]
    window.open_registry_tender(tender.id)
    monkeypatch.setattr(window, "guard_changes", lambda: False)
    window.registry_back.click()
    app.processEvents()
    assert window.pages.currentIndex() == 0
    assert window.registry_pages.currentWidget() == window.registry_card_page
    assert window.current.id == tender.id
    assert window.registry_back.isVisible()
    assert window.navigation_buttons[0].isChecked()


def test_single_registry_card_stays_in_its_home(registry_window, app):
    window = registry_window
    tender = window.repository.all()[0]
    window.open_registry_tender(tender.id)
    app.processEvents()
    assert window.card.parentWidget() == window.registry_card_page
    assert window.card.width() > window.registry_pages.width() - 20
    assert not window.registry_panel.isVisible()
    window.note.setPlainText("Из отдельной карточки")
    assert window.save_current()
    assert "Из отдельной карточки" in next(t for t in window.repository.all() if t.id == tender.id).comment
    window.pages.setCurrentIndex(1)
    window.pages.setCurrentIndex(0)
    app.processEvents()
    assert window.card.parentWidget() == window.registry_card_page
    assert window.card.isVisible()
    window.return_to_registry()
    assert window.registry_pages.currentWidget() == window.registry


def test_registry_keyboard_commands(registry_window, app):
    window = registry_window
    window.registry.search.setFocus()
    QTest.keyClick(window.registry.search, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.registry.table.hasFocus()
    QTest.keyClick(window.registry.table, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.global_search.hasFocus()
    QTest.keyClick(window.global_search, Qt.Key.Key_Escape)
    app.processEvents()
    assert window.registry.table.hasFocus()


def test_close_registry_card_preserves_data(registry_window, app):
    window = registry_window
    before = window.repository.all()
    window.open_registry_tender(before[0].id)
    window.close()
    assert window.repository.all() == before


def registry_item(window, tender_id):
    return next(window.registry.table.item(row, 0)
                for row in range(window.registry.table.rowCount())
                if window.registry.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == tender_id)


def import_new(window):
    from tests.test_import_1c import line
    plan = window.repository.preview_1c(line("0000123/НОВЫЙ") + line("0000456/НОВЫЙ"))
    ids, _ = window.repository.import_1c(plan, [row.ordinal for row in plan.rows if row.category == 'new'])
    window.refresh_execution()
    return ids


def test_unseen_table_selection_refresh_copy_and_reopen(registry_window, app, monkeypatch):
    window = registry_window
    ids = import_new(window)
    item = registry_item(window, ids[0])
    assert item.data(Qt.ItemDataRole.UserRole + 3)
    assert item.background().color().name() == '#23394d'
    assert 'Новый' in item.toolTip()
    assert item.text() == '0000123/НОВЫЙ'
    class Clipboard:
        def setText(self, value): self.value = value
    clipboard = Clipboard()
    monkeypatch.setattr(QApplication, 'clipboard', lambda: clipboard)
    window.registry.table.setCurrentItem(item)
    window.registry.copy_number()
    assert clipboard.value == '0000123/НОВЫЙ'
    window.registry.search.setText('НОВЫЙ')
    window.registry.table.sortItems(0, Qt.SortOrder.DescendingOrder)
    window.pages.setCurrentIndex(1)
    window.pages.setCurrentIndex(0)
    window.refresh_execution()
    assert window.repository.unseen_tender_ids() == set(ids)
    reopened = ExecutionWindow(window.repository, Settings(window.settings.path.parent / 'reopened.json'), window.documents_root)
    try:
        assert reopened.pages.currentIndex() == 0
        assert registry_item(reopened, ids[0]).data(Qt.ItemDataRole.UserRole + 3)
        assert window.repository.unseen_tender_ids() == set(ids)
    finally:
        reopened.close()


@pytest.mark.parametrize('route', ['open_registry_tender', 'open_execution_tender', 'open_assistant_tender'])
def test_unseen_explicit_routes_show_registry_card(registry_window, route):
    window = registry_window
    ids = import_new(window)
    window.pages.setCurrentIndex(1)
    getattr(window, route)(ids[0])
    assert window.pages.currentIndex() == 0
    assert window.registry_pages.currentWidget() == window.registry_card_page
    assert window.repository.unseen_tender_ids() == {ids[1]}
    window.return_to_registry()
    assert not registry_item(window, ids[0]).data(Qt.ItemDataRole.UserRole + 3)
    assert registry_item(window, ids[1]).data(Qt.ItemDataRole.UserRole + 3)


def test_unseen_cancel_and_storage_failure(registry_window, monkeypatch):
    import sqlite3
    window = registry_window
    ids = import_new(window)
    with monkeypatch.context() as patch:
        patch.setattr(window, 'guard_changes', lambda: False)
        assert not window.open_registry_tender(ids[0])
    assert window.repository.unseen_tender_ids() == set(ids)
    def fail(_):
        raise sqlite3.OperationalError('locked')
    monkeypatch.setattr(window.repository, 'mark_tender_seen', fail)
    assert window.open_registry_tender(ids[0])
    assert window.registry_pages.currentWidget() == window.registry_card_page
    assert 'не снята' in window.statusBar().currentMessage()
    window.return_to_registry()
    assert registry_item(window, ids[0]).data(Qt.ItemDataRole.UserRole + 3)


def test_unseen_colors_preserve_status_and_overdue(registry_window):
    window = registry_window
    ids = import_new(window)
    from dataclasses import replace
    tender = next(t for t in window.repository.all() if t.id == ids[0])
    window.repository.save(replace(tender, deadline_date='2020-01-01'))
    window.refresh_registry()
    row = registry_item(window, ids[0]).row()
    assert window.registry.table.item(row, 3).foreground().color().name() == '#f19b9f'
    assert window.registry.table.item(row, 4).background().color().name() != '#23394d'
