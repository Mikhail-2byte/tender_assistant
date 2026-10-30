import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tender_app.execution_comments import PositionCommentsDialog
from tests.test_execution_window import execution_window as base_execution_window


@pytest.fixture
def execution_window(base_execution_window):
    yield base_execution_window
    base_execution_window.execution.comment_drafts.clear()


@pytest.mark.parametrize('action', ['top', 'comment', 'ctrl_s', 'ctrl_enter'])
def test_tender_save_actions_commit_same_fields_and_comment(window, app, action):
    identifier = window.current.id
    window.edits['contacts'].setPlainText('Новый контакт')
    window.note.setPlainText('Договорились о сроке')
    assert window.save_button.text() == window.send_button.text() == 'Сохранить'
    assert window.save_button.isEnabled() and window.send_button.isEnabled()
    window.note.setFocus()
    app.processEvents()
    if action == 'top':
        window.save_button.click()
    elif action == 'comment':
        window.send_button.click()
    else:
        QTest.keyClick(window.note, Qt.Key.Key_S if action == 'ctrl_s' else Qt.Key.Key_Return,
                       Qt.KeyboardModifier.ControlModifier)
    saved = next(t for t in window.repository.all() if t.id == identifier)
    assert saved.contacts == 'Новый контакт'
    assert saved.comment.count('Договорились о сроке') == 1
    assert window.note_feedback.text() == 'Комментарий добавлен, изменения карточки сохранены'
    assert window.statusBar().currentMessage() == window.note_feedback.text()
    assert not window.dirty and not window.note.toPlainText()
    assert not window.save_button.isEnabled() and not window.send_button.isEnabled()


def test_comment_button_can_save_fields_without_empty_history_entry(window):
    comment = window.current.comment
    window.edits['contacts'].setPlainText('Только контакт')
    assert window.send_button.isEnabled()
    window.send_button.click()
    assert window.current.comment == comment
    assert window.note_feedback.text() == 'Изменения карточки сохранены'


def test_tender_error_keeps_all_drafts_and_retry_does_not_duplicate(window, monkeypatch):
    original = window.current
    save = window.repository.save
    window.note.setPlainText('Комментарий после повтора')
    window.edits['contacts'].setPlainText('Изменение контакта')
    def fail(*_):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(window.repository, 'save', fail)
    monkeypatch.setattr(window, 'error', lambda _: None)
    window.send_button.click()
    assert window.note_feedback.text().startswith('Не сохранено')
    assert window.note.toPlainText() == 'Комментарий после повтора'
    assert window.edits['contacts'].toPlainText() == 'Изменение контакта'
    assert next(t for t in window.repository.all() if t.id == original.id) == original
    monkeypatch.setattr(window.repository, 'save', save)
    window.send_button.click()
    assert window.current.comment.count('Комментарий после повтора') == 1
    assert window.note_feedback.text().startswith('Комментарий добавлен')


@pytest.mark.parametrize('key', [Qt.Key.Key_S, Qt.Key.Key_Return])
def test_execution_shortcuts_save_current_comment_not_hidden_tender(execution_window, app, key):
    win = execution_window
    spec = win.repository.specifications()[0]
    win.open_specification(spec.id)
    before = win.repository.all()
    win.edits['contacts'].setPlainText('Черновик скрытого тендера')
    feed = win.execution.spec_comment_feed
    feed.edit.setPlainText('Комментарий открытой спецификации')
    feed.edit.setFocus()
    app.processEvents()
    win.mark_dirty()  # A background resource update must respect the visible section.
    assert 'несохранённый комментарий' in win.save_indicator.text()
    QTest.keyClick(feed.edit, key, Qt.KeyboardModifier.ControlModifier)
    saved = next(s for s in win.repository.specifications() if s.id == spec.id)
    assert saved.comments[0][1] == 'Комментарий открытой спецификации'
    assert win.repository.all() == before
    assert win.edits['contacts'].toPlainText() == 'Черновик скрытого тендера'
    assert win.execution.spec_comment_feed.feedback.text() == 'Комментарий добавлен'
    assert win.execution.spec_comment_feed.edit.toPlainText() == ''
    assert 'Сохранено' in win.save_indicator.text()


def test_specification_error_is_visible_and_retry_keeps_one_comment(execution_window, app, monkeypatch):
    win = execution_window
    spec = win.repository.specifications()[0]
    win.open_specification(spec.id)
    win.execution.spec_comment_feed.edit.setPlainText('Сохранить после ошибки')
    save = win.repository.add_specification_comment
    def fail(*_):
        raise sqlite3.OperationalError('disk full')
    monkeypatch.setattr(win.repository, 'add_specification_comment', fail)
    monkeypatch.setattr(win, 'error', lambda _: None)
    assert not win.save_current()
    app.processEvents()
    assert win.execution.spec_comment_feed.feedback.isVisible()
    assert win.execution.spec_comment_feed.feedback.text().startswith('Не сохранено')
    assert win.execution.comment_drafts[spec.id] == 'Сохранить после ошибки'
    monkeypatch.setattr(win.repository, 'add_specification_comment', save)
    assert win.save_current()
    assert win.save_current()  # Repeating Save with an empty composer is a no-op.
    saved = next(s for s in win.repository.specifications() if s.id == spec.id)
    assert [text for _, text in saved.comments].count('Сохранить после ошибки') == 1


def test_position_dialog_uses_same_labels_shortcuts_and_feedback(execution_window, app):
    win = execution_window
    spec = win.repository.specifications()[0]
    dialog = PositionCommentsDialog(win.repository, spec.id, spec.positions[0].id, win)
    dialog.show()
    app.processEvents()
    dialog.feed.edit.setPlainText('Комментарий позиции')
    dialog.feed.edit.setFocus()
    app.processEvents()
    assert dialog.feed.add_button.text() == 'Сохранить'
    QTest.keyClick(dialog.feed.edit, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    saved = next(s for s in win.repository.specifications() if s.id == spec.id)
    assert saved.positions[0].comments[0][1] == 'Комментарий позиции'
    assert dialog.feed.feedback.text() == 'Комментарий добавлен'
    assert dialog.feed.edit.toPlainText() == ''
    dialog.reject()
