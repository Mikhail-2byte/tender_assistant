from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tender_app.execution_models import Position, Specification
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.global_search import search_records
from tender_app.models import Tender
from tender_app.settings import Settings


@pytest.fixture
def records():
    tender = Tender(id=1, number='001-АБ', customer='Завод', title='Валы',
        contacts='Сабина, снабжение', comment='Отправил чертёж поставщику\nЖдём ответ во вторник')
    spec = Specification(id=2, tender_id=1, number='СП-12', contract='Договор 77/26', archived=True,
        comments=[(None, 'Перенесли доставку на пятницу')], history=[('', 'Согласован новый срок')],
        positions=[Position(id=3, name='Вал ЧЕРТ-507', supplier='Механический завод',
            supplier_document='Приложение 74', comments=[(None, 'Уточнить упаковку у Рамазана')])])
    return [tender], [spec]


@pytest.mark.parametrize(('query', 'kind', 'field'), [
    ('саБИНА', 'Тендер', 'Контакты'), ('чертеж поставщику', 'Тендер', 'История работы'),
    ('77/26', 'Спецификация', 'Договор'), ('доставку пятницу', 'Спецификация', 'Комментарии спецификации'),
    ('согласован срок', 'Спецификация', 'История изменений'),
    ('ЧЕРТ-507', 'Позиция', 'Позиция / чертёж'), ('Механический', 'Позиция', 'Изготовитель'),
    ('Приложение 74', 'Позиция', 'Приложение / документ'), ('Рамазана', 'Позиция', 'Комментарии позиции'),
])
def test_searches_saved_work_and_explains_match(records, query, kind, field):
    result = search_records(*records, query)[0]
    assert result.kind == kind and result.field == field
    assert result.snippet
    assert result.tender_id == 1
    if kind != 'Тендер':
        assert result.spec_id == 2 and 'Архив' in result.context


def test_empty_literal_query_and_exact_number_rank(records):
    tenders, specs = records
    assert not search_records(tenders, specs, '  ')
    assert not search_records(tenders, specs, '%')
    assert not search_records(tenders, specs, 'несуществующий')
    tenders.append(Tender(id=4, number='СП-12'))
    assert search_records(tenders, specs, '№ СП-12')[0].tender_id == 4


@pytest.fixture
def win(tmp_path, app):
    path = tmp_path / 'test.db'
    create_demo_database(path)
    repo = ExecutionRepository(path)
    tender = repo.all()[0]
    repo.save(replace(tender, contacts='Сабина, отдел снабжения', comment='Уточнить цену у Рамазана'))
    spec = next(s for s in repo.specifications() if len(s.positions) > 1)
    repo.add_specification_comment(spec.id, 'Проверка архивного комментария')
    repo.set_position_comment(spec.id, spec.positions[-1].id, 'Особая упаковка позиции')
    repo.set_archived(spec.id, True)
    window = ExecutionWindow(repo, Settings(tmp_path / 'settings.json'), tmp_path)
    window.resize(1152, 768)
    window.show()
    app.processEvents()
    yield window
    window.dirty = False
    window.close()
    repo.close()
    app.processEvents()


def search(win, query):
    win.global_search.setFocus()
    win.global_search.setText(query)
    QTest.qWait(210)
    return win.search_results


def test_common_results_ignore_section_and_filters_and_do_not_write(win, app):
    before = win.repository.all(), win.repository.specifications()
    win.pages.setCurrentIndex(2)
    win.status_filter.setCurrentText('Не участвуем')
    panel = search(win, 'Сабина')
    assert panel.isVisible() and panel.list.count() == 1
    assert 'Контакты: Сабина' in panel.list.item(0).text()
    assert win.pages.currentIndex() == 2
    assert (win.repository.all(), win.repository.specifications()) == before
    QTest.keyClick(win.global_search, Qt.Key.Key_Return)
    assert win.pages.currentIndex() == 0
    assert win.current.contacts.startswith('Сабина')
    assert not panel.isVisible()


def test_opens_archived_specification_and_matching_position(win, app):
    panel = search(win, 'архивного комментария')
    result = panel.list.item(0).data(Qt.ItemDataRole.UserRole)
    panel.activate(panel.list.item(0))
    assert win.pages.currentIndex() == 1
    assert win.execution.current_specification_id == result.spec_id
    assert win.execution.state.currentText() == 'Архив'
    panel = search(win, 'Особая упаковка')
    result = panel.list.item(0).data(Qt.ItemDataRole.UserRole)
    panel.activate(panel.list.item(0))
    app.processEvents()
    table = win.execution.position_tables[0]
    assert table.currentRow() == len(table.positions) - 1
    assert table.positions[table.currentRow()]['id'] == result.position_id


def test_cancelled_navigation_preserves_draft(win, app, monkeypatch):
    win.edits['contacts'].setPlainText('Мой черновик')
    win.note.setPlainText('Не потерять запись')
    identifier = win.current.id
    panel = search(win, 'архивного комментария')
    monkeypatch.setattr(win, 'guard_changes', lambda: False)
    panel.activate(panel.list.item(0))
    assert win.pages.currentIndex() == 0 and win.current.id == identifier
    assert win.edits['contacts'].toPlainText() == 'Мой черновик'
    assert win.note.toPlainText() == 'Не потерять запись'


def test_empty_results_escape_and_keyboard_selection(win, app):
    panel = search(win, 'неттакойстроки')
    assert panel.isVisible() and panel.list.count() == 0
    assert 'Ничего не найдено' in panel.count.text()
    QTest.keyClick(win.global_search, Qt.Key.Key_Escape)
    QTest.qWait(220)
    assert not panel.isVisible()
    search(win, 'Рамазана')
    QTest.keyClick(win.global_search, Qt.Key.Key_Down)
    assert panel.list.hasFocus()
    QTest.keyClick(panel.list, Qt.Key.Key_Return)
    QTest.qWait(220)
    assert not panel.isVisible()
    win.global_search.clear()
    assert panel.list.count() == 0


def test_explicit_discard_clears_draft_and_opens_result(win, app, monkeypatch):
    original = win.current
    win.edits['contacts'].setPlainText('Черновик, который отменяю')
    win.note.setPlainText('Не сохранять эту запись')
    panel = search(win, 'архивного комментария')
    monkeypatch.setattr(win, 'guard_changes', lambda: True)
    panel.activate(panel.list.item(0))
    assert win.pages.currentIndex() == 1
    assert win.edits['contacts'].toPlainText() == original.contacts
    assert not win.note.toPlainText() and not win.dirty


def test_saved_changes_are_visible_to_next_search(win, app):
    win.edits['contacts'].setPlainText('Новый контакт Ковалёв')
    assert win.save_tender()
    panel = search(win, 'Ковалев')
    assert panel.list.count() == 1
    assert panel.list.item(0).data(Qt.ItemDataRole.UserRole).tender_id == win.current.id


def test_application_links_reveal_position_from_execution_registry(win, app):
    from tender_app.agent_chat import AgentHistory
    from tender_app.theme import ENTITY_COLORS
    from tender_app.search_presentation import HTML_ROLE

    spec = next(s for s in win.repository.specifications() if len(s.positions) > 1)
    position = spec.positions[-1]
    win.repository.update_position_field(spec.id, position.id, 'supplier_document', 'ДЕМО прил.234 от 14/05/2026')
    win.pages.setCurrentIndex(3)
    panel = search(win, 'приложение 234')
    assert panel.list.count() == 1
    html = panel.list.item(0).data(HTML_ROLE)
    assert all(color in html for color in ENTITY_COLORS.values())
    panel.activate(panel.list.item(0))
    app.processEvents()
    assert win.pages.currentIndex() == 1
    assert win.execution.current_specification_id == spec.id
    table = win.execution.position_tables[0]
    assert table.positions[table.currentRow()]['id'] == position.id

    win.agent_page.chat.input.setPlainText('Найди приложение 234')
    win.agent_page.send_message()
    message = win.agent_page.history.active['messages'][-1]
    assert message['links'][0]['position_id'] == position.id
    assert AgentHistory(win.agent_page.history.path).active['messages'][-1] == message
    win.pages.setCurrentIndex(3)
    win.agent_page.open_record(message['links'][0])
    app.processEvents()
    assert win.pages.currentIndex() == 1
    assert table.positions[table.currentRow()]['id'] == position.id


def test_cancelled_application_navigation_preserves_draft(win, app, monkeypatch):
    spec = win.repository.specifications()[0]
    position = spec.positions[0]
    win.note.setPlainText('Сохранить черновик')
    monkeypatch.setattr(win, 'guard_changes', lambda: False)
    win.open_search_position(spec.id, position.id)
    app.processEvents()
    assert win.pages.currentIndex() == 0
    assert win.note.toPlainText() == 'Сохранить черновик'
