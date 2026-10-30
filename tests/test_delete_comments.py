import sqlite3
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton

from tender_app.execution_comments import PositionCommentsDialog
from tests.test_save_rules import execution_window
from tests.test_execution_window import execution_window as base_execution_window


@pytest.fixture(autouse=True)
def confirm(monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)


@pytest.mark.parametrize('index', [0, 1, 2])
def test_delete_tender_entry_keeps_other_entries_and_drafts(window, index):
    history = 'Прежняя заметка\n\n[24.10.2026 10:00 МСК]\nПервый\n\n[24.10.2026 10:01 МСК]\nВторой'
    window.load_tender(window.repository.save(replace(window.current, comment=history)))
    window.edits['contacts'].setPlainText('Несохранённый контакт')
    window.note.setPlainText('Несохранённый комментарий')
    buttons = [window.history_entries_layout.itemAt(i).widget().findChild(QPushButton, 'deleteComment')
               for i in range(window.history_entries_layout.count())]
    buttons[index].click()
    saved = next(t for t in window.repository.all() if t.id == window.current.id)
    removed = ['Второй', 'Первый', 'Прежняя заметка'][index]
    assert removed not in saved.comment
    for text in {'Второй', 'Первый', 'Прежняя заметка'} - {removed}:
        assert text in saved.comment
    assert saved.contacts != 'Несохранённый контакт'
    assert window.note.toPlainText() == 'Несохранённый комментарий'
    assert window.edits['contacts'].toPlainText() == 'Несохранённый контакт'
    assert window.save_tender()
    assert removed not in window.current.comment


def test_tender_cancel_error_and_stale_history_preserve_comment(window, monkeypatch):
    window.load_tender(window.repository.save(replace(window.current, comment='Оставить')))
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.No)
    window.delete_note('Оставить', 0, 8)
    assert window.current.comment == 'Оставить'
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    errors = []
    monkeypatch.setattr(window, 'error', errors.append)
    window.delete_note('Другая история', 0, 5)
    assert errors and window.current.comment == 'Оставить'
    monkeypatch.setattr(window.repository, 'delete_tender_comment', lambda *_: (_ for _ in ()).throw(sqlite3.OperationalError('disk full')))
    window.delete_note('Оставить', 0, 8)
    assert len(errors) == 2 and window.current.comment == 'Оставить'


def test_delete_tender_comment_with_nonbreaking_spaces(window):
    text = '[24.10.2026 10:00 МСК]\nНомер\u00a0123'
    window.load_tender(window.repository.save(replace(window.current, comment=text)))
    window.history_entries_layout.itemAt(0).widget().findChild(QPushButton, 'deleteComment').click()
    assert window.current.comment == ''


def test_specification_delete_uses_id_and_keeps_draft(execution_window):
    win = execution_window
    spec = win.repository.specifications()[0]
    for _ in range(2):
        win.repository.add_specification_comment(spec.id, 'Одинаковый текст')
    win.refresh_execution()
    win.open_specification(spec.id)
    rows = win.repository.comment_entries(spec.id)
    win.execution.spec_comment_feed.edit.setPlainText('Черновик')
    button = win.execution.spec_comment_feed.entries.itemAt(1).widget().findChild(QPushButton, 'deleteComment')
    button.click()
    remaining = [r[0] for r in win.repository.comment_entries(spec.id)]
    assert rows[0][0] in remaining and rows[1][0] not in remaining
    assert win.execution.spec_comment_feed.edit.toPlainText() == 'Черновик'
    assert win.execution.spec_comment_feed.feedback.text() == 'Комментарий удалён'
    with pytest.raises(ValueError):
        win.repository.delete_execution_comment(spec.id, None, rows[1][0])


def test_position_delete_refreshes_latest_and_keeps_draft(execution_window):
    win = execution_window
    spec = win.repository.specifications()[0]
    position = spec.positions[0]
    win.repository.set_position_comment(spec.id, position.id, 'Первый')
    win.repository.set_position_comment(spec.id, position.id, 'Второй')
    dialog = PositionCommentsDialog(win.repository, spec.id, position.id, win)
    dialog.feed.edit.setPlainText('Черновик')
    dialog.feed.entries.itemAt(0).widget().findChild(QPushButton, 'deleteComment').click()
    assert dialog.position().comment == 'Первый'
    assert dialog.feed.edit.toPlainText() == 'Черновик'
    for row in win.repository.comment_entries(spec.id, position.id):
        win.repository.delete_execution_comment(spec.id, position.id, row[0])
    assert dialog.position().comment == ''
    assert not dialog.position().comments
    dialog.feed.edit.clear()
    dialog.reject()


def test_wrong_scope_cannot_delete_position_comment(execution_window):
    repo = execution_window.repository
    spec = repo.specifications()[0]
    pos = spec.positions[0]
    repo.set_position_comment(spec.id, pos.id, 'Сохранить')
    key = repo.comment_entries(spec.id, pos.id)[0][0]
    with pytest.raises(ValueError):
        repo.delete_execution_comment(spec.id, None, key)
    assert repo.comment_entries(spec.id, pos.id)[0][0] == key


def test_execution_cancel_and_failure_keep_comment_and_draft(execution_window, monkeypatch):
    win = execution_window
    spec = win.repository.specifications()[0]
    win.repository.add_specification_comment(spec.id, 'Оставить')
    win.refresh_execution()
    win.open_specification(spec.id)
    key = win.repository.comment_entries(spec.id)[0][0]
    win.execution.spec_comment_feed.edit.setPlainText('Черновик')
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.No)
    win.delete_specification_comment(spec.id, key)
    assert win.repository.comment_entries(spec.id)[0][0] == key
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    errors = []
    monkeypatch.setattr(win, 'error', errors.append)
    monkeypatch.setattr(win.repository, 'delete_execution_comment', lambda *_: (_ for _ in ()).throw(sqlite3.OperationalError('disk full')))
    win.delete_specification_comment(spec.id, key)
    assert errors and win.repository.comment_entries(spec.id)[0][0] == key
    assert win.execution.spec_comment_feed.edit.toPlainText() == 'Черновик'


def test_position_delete_rolls_back_on_failure_and_makes_backup(execution_window):
    repo = execution_window.repository
    spec = repo.specifications()[0]
    pos = spec.positions[0]
    repo.set_position_comment(spec.id, pos.id, 'Оставить')
    key = repo.comment_entries(spec.id, pos.id)[0][0]
    repo.connection.execute("CREATE TEMP TRIGGER fail_comment_update BEFORE UPDATE OF comment ON execution_positions BEGIN SELECT RAISE(ABORT,'disk full'); END")
    with pytest.raises(sqlite3.Error):
        repo.delete_execution_comment(spec.id, pos.id, key)
    assert repo.comment_entries(spec.id, pos.id)[0][0] == key
    backup = max(repo.backup_dir.glob('manual-*.db'), key=lambda p: p.stat().st_mtime_ns)
    with sqlite3.connect(backup) as copy:
        assert copy.execute('SELECT message FROM execution_comments WHERE id=?', (key,)).fetchone()[0] == 'Оставить'
