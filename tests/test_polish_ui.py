"""V3 UX regressions: no production records, all data generated locally."""
import json
from dataclasses import replace
from datetime import date
import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QCalendarWidget, QDialogButtonBox, QLineEdit
from tender_app.execution_seed import create_demo_database
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_window import ExecutionWindow
from tender_app.execution_panel import ROLE
from tender_app.execution_editor import SpecificationDialog, PositionDialog
from tender_app.settings import Settings
from tender_app.widgets import OptionalDateEdit

@pytest.fixture
def polished(tmp_path, app):
    create_demo_database(tmp_path/'fictional.db')
    repo = ExecutionRepository(tmp_path/'fictional.db')
    win = ExecutionWindow(repo, Settings(tmp_path/'settings.json'), tmp_path)
    win.resize(1366, 768)
    win.show()
    QTest.qWait(30)
    yield win
    win.dirty = False
    win.execution.comment_drafts.clear()
    win.close()
    repo.close()
    app.processEvents()

def test_compact_spec_header_shows_all_details_and_resource_actions(polished, app):
    win = polished
    spec = win.repository.specifications()[0]
    win.open_specification(spec.id)
    QTest.qWait(40)
    panel = win.execution
    assert not hasattr(panel, "spec_details_toggle")
    assert panel.spec_header.height() < 300
    assert panel.position_tables[0].mapTo(panel.card, panel.position_tables[0].rect().topLeft()).y() < 430
    actions = {button.text(): button for button in panel.spec_header.findChildren(QPushButton)}
    assert "Подробности и документы" not in actions
    for name, resource in (("Открыть папку", "folder"), ("Открыть ЭТП", "url")):
        assert actions[name].isVisible()
        assert actions[name].mapTo(panel.spec_header, actions[name].rect().topLeft()).y() < 200
    captions = {label.text() for label in panel.spec_header.findChildren(QLabel)}
    assert "Контакты заказчика" in captions
    assert not {"Предмет спецификации", "Этап исполнения"} & captions
    tender = next(t for t in win.repository.all() if t.id == spec.tender_id)
    assert panel.customer_contacts.isVisible()
    assert panel.customer_contacts.toPlainText() == tender.contacts
    assert panel.scroll.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize("width", [1366, 1920])
def test_long_contacts_and_description_do_not_push_positions_out_of_view(polished, app, width):
    from PySide6.QtWidgets import QPlainTextEdit
    win = polished
    win.resize(width, 768)
    spec = win.repository.specifications()[0]
    win.open_specification(spec.id)
    QTest.qWait(30)
    assert win.execution.spec_header.height() < 300
    tender = next(t for t in win.repository.all() if t.id == spec.tender_id)
    tender.contacts = "\n\n".join(f"Учебный контакт {n}\ncontact-{n}@example.invalid" for n in range(50))
    spec.title = "Учебное пояснение о согласовании поставки. " * 50
    win.repository.save(tender)
    win.repository.save_specification(spec)
    win.open_specification(spec.id)
    QTest.qWait(60)
    panel = win.execution
    assert panel.spec_header.height() < 300
    contacts = panel.customer_contacts
    assert contacts.toPlainText() == tender.contacts
    assert contacts.height() == 96
    assert contacts.isReadOnly() and contacts.verticalScrollBar().maximum() > 0
    contacts.verticalScrollBar().setValue(contacts.verticalScrollBar().maximum())
    assert contacts.textCursor().document().toPlainText().endswith("contact-49@example.invalid")
    assert not any(e.accessibleName() == "Предмет спецификации" for e in panel.spec_header.findChildren(QPlainTextEdit))
    saved = next(s for s in win.repository.specifications() if s.id == spec.id)
    assert saved.title == spec.title and saved.stage == spec.stage
    table = panel.position_tables[0]
    first_row = table.viewport().mapTo(panel.scroll.viewport(), table.visualItemRect(table.item(0, 0)).topLeft())
    assert first_row.y() < panel.scroll.viewport().height() - table.rowHeight(0)
    assert panel.scroll.horizontalScrollBar().maximum() == 0

def test_navigation_modes_and_recent_are_local_read_only(polished, app):
    win = polished
    panel = win.execution
    before = win.repository.all(), win.repository.specifications()
    target = next(s for s in win.repository.specifications() if s.risk)
    win.open_specification(target.id)
    assert ('spec', target.id) in panel.recent_keys
    panel.navigation_modes.setCurrentIndex(2)
    QTest.qWait(30)
    assert target.id in [s['id'] for s in panel.visible_specifications]
    panel.navigation_modes.setCurrentIndex(1)
    assert panel.visible_specifications
    assert all(panel.needs_attention(s) for s in panel.visible_specifications)
    panel.navigation_modes.setCurrentIndex(0)
    for i in range(panel.tree.topLevelItemCount()):
        parent = panel.tree.topLevelItem(i)
        compact = parent.data(0, ROLE+3)
        assert compact.splitlines()[0] == panel.groups[parent.data(0, ROLE)]['customer']
        for j in range(parent.childCount()):
            child = parent.child(j)
            spec = next(s for s in panel.specifications if s['id'] == child.data(0, ROLE)[1])
            assert spec['title'] in child.data(0, ROLE+3)
            assert 'Тендер №' in child.toolTip(0)
    assert (win.repository.all(), win.repository.specifications()) == before

def test_navigation_persists_expansion_selection_recent_scroll_and_width(polished, app):
    win = polished
    panel = win.execution
    win.pages.setCurrentIndex(1)
    for i in range(panel.tree.topLevelItemCount()):
        panel.tree.topLevelItem(i).setExpanded(True)
    spec = next(s for s in win.repository.specifications() if s.risk)
    panel.open_specification(spec.id)
    for i in range(panel.tree.topLevelItemCount()):
        panel.tree.topLevelItem(i).setExpanded(True)
    QTest.qWait(40)
    panel.tree.verticalScrollBar().setValue(40)
    panel.splitter.moveSplitter(340, 1)
    QTest.qWait(30)
    expected = panel.navigation_preferences()
    width = win.execution_registry_width
    win.close()
    settings = Settings(win.settings.path)
    assert settings.get('execution_navigation')['selected'] == ['spec', spec.id]
    other = ExecutionWindow(win.repository, settings, win.documents_root)
    other.show()
    other.pages.setCurrentIndex(1)
    QTest.qWait(60)
    try:
        actual = other.execution.navigation_preferences()
        assert actual['expanded'] == expected['expanded']
        assert actual['recent'] == expected['recent']
        assert other.execution.current_specification_id == spec.id
        assert other.execution_registry_width == width
        assert actual["scroll"] == expected["scroll"]
        # A plain refresh must not jump back to the top.
        other.execution.tree.verticalScrollBar().setValue(30)
        before = other.execution.tree.verticalScrollBar().value()
        other.execution.refresh()
        QTest.qWait(40)
        assert other.execution.tree.verticalScrollBar().value() == before
    finally:
        other.dirty = False
        other.close()

def test_save_only_for_actual_changes_and_noop_does_not_write(polished, monkeypatch):
    win = polished
    original = win.current
    assert not win.save_button.isEnabled()
    calls = []
    save = win.repository.save
    monkeypatch.setattr(win.repository, 'save', lambda tender: (calls.append(tender), save(tender))[1])
    assert win.save_tender() and not calls
    win.edits['title'].setPlainText(original.title+' уточнение')
    assert win.dirty and win.save_button.isEnabled()
    win.edits['title'].setPlainText(original.title)
    assert not win.dirty and not win.save_button.isEnabled()
    win.edits['title'].setPlainText(original.title+' уточнение')
    assert win.save_tender()
    assert len(calls) == 1
    assert not win.dirty and not win.save_button.isEnabled()
    assert 'Сохранено' in win.save_indicator.text()

def test_escape_closes_assistant_and_ctrl_l_focuses_registry(polished, app):
    win = polished
    win.open_specification(win.repository.specifications()[0].id)
    win.execution_assistant_toggle.setChecked(True)
    QTest.qWait(40)
    win.activateWindow()
    win.execution_assistant.input.setFocus()
    QTest.keyClick(win.execution_assistant.input, Qt.Key.Key_Escape)
    QTest.qWait(20)
    assert not win.execution_assistant_toggle.isChecked()
    win.global_search.setFocus()
    QTest.keyClick(win.global_search, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    assert win.execution.tree.hasFocus()

def test_nullable_calendar_select_cancel_clear_and_editors(polished, app):
    edit = OptionalDateEdit('2026-10-27')
    edit.show()
    try:
        edit.open_calendar()
        popup = edit.calendar_popup
        calendar = popup.findChild(QCalendarWidget)
        assert calendar.selectedDate() == QDate(2026, 10, 27)
        calendar.setSelectedDate(QDate(2026, 11, 5))
        popup.reject()
        assert edit.text() == '2026-10-27'
        edit.open_calendar()
        popup = edit.calendar_popup
        popup.findChild(QCalendarWidget).setSelectedDate(QDate(2026, 11, 5))
        next(b for b in popup.findChild(QDialogButtonBox).buttons() if b.text() == 'Выбрать').click()
        assert edit.text() == '2026-11-05'
        edit.open_calendar()
        popup = edit.calendar_popup
        next(b for b in popup.findChild(QDialogButtonBox).buttons() if b.text() == 'Не указан').click()
        assert edit.text() == ''
        spec = polished.repository.specifications()[0]
        dialog = SpecificationDialog(polished.repository, spec, polished)
        assert isinstance(dialog.edits['due'], OptionalDateEdit)
        assert not dialog.save_button.isEnabled()
        dialog.edits['title'].setPlainText('Уточнённый предмет')
        assert dialog.save_button.isEnabled()
        dialog.modified = False
        dialog.reject()
        dialog = PositionDialog(spec.positions[0], polished)
        assert isinstance(dialog.edits['eta'], OptionalDateEdit)
        dialog.reject()
    finally:
        edit.close()

def test_enter_and_arrows_navigate_contract_tree(polished, app):
    win = polished
    win.pages.setCurrentIndex(1)
    tree = win.execution.tree
    parent = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).childCount())
    tree.setCurrentItem(parent)
    parent.setExpanded(False)
    win.activateWindow()
    tree.setFocus()
    QTest.keyClick(tree, Qt.Key.Key_Return)
    assert parent.isExpanded()
    QTest.keyClick(tree, Qt.Key.Key_Down)
    assert tree.currentItem().parent() == parent
    QTest.keyClick(tree, Qt.Key.Key_Return)
    assert win.execution.scroll.hasFocus()


def test_calendar_escape_does_not_change_date_or_trigger_main_shortcut(polished, app):
    win = polished
    dialog = SpecificationDialog(win.repository, win.repository.specifications()[0], win)
    dialog.show()
    edit = dialog.edits['due']
    original = edit.text()
    edit.open_calendar()
    popup = edit.calendar_popup
    popup.activateWindow()
    QTest.qWait(20)
    popup.findChild(QCalendarWidget).setSelectedDate(QDate(2027, 2, 1))
    QTest.keyClick(popup, Qt.Key.Key_Escape)
    assert not popup.isVisible()
    assert edit.text() == original and not dialog.modified
    dialog.reject()


def test_archived_navigation_selection_survives_restart(polished, app):
    win = polished
    archived = next(s for s in win.repository.specifications() if s.archived)
    win.open_specification(archived.id)
    win.close()
    other = ExecutionWindow(win.repository, Settings(win.settings.path), win.documents_root)
    other.show()
    other.pages.setCurrentIndex(1)
    QTest.qWait(40)
    try:
        assert other.execution.state.currentText() == "Архив"
        assert other.execution.current_specification_id == archived.id
    finally:
        other.dirty = False
        other.close()
