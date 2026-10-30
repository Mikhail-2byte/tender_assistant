"""First-view metadata uses fictional temporary data only."""
import sqlite3

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.import_dialog import Import1CDialog
from tender_app.import_1c import StaleImportPlan
from tender_app.models import Tender
from tender_app.settings import Settings
from tests.test_import_1c import line
from tests.test_import_1c_ui import owner
from tests.test_global_search import search


def add(repo, count=2):
    plan = repo.preview_1c(''.join(line(f'НОВЫЙ-{i}') for i in range(count)))
    ids, _ = repo.import_1c(plan, [r.ordinal for r in plan.rows if r.category == 'new'])
    return ids


def item_for(win, tender_id):
    return next(win.list.item(i) for i in range(win.list.count())
                if win.list.item(i).data(Qt.ItemDataRole.UserRole) == tender_id)


def test_import_flags_survive_reopen_and_card_save(owner):
    repo = owner.repository
    before = repo.all()
    ids = add(repo)
    assert repo.unseen_tender_ids() == set(ids)
    assert repo.all()[:len(before)] == before
    reopened = ExecutionRepository(repo.path)
    try:
        assert reopened.unseen_tender_ids() == set(ids)
        card = next(t for t in reopened.all() if t.id == ids[0])
        reopened.save(card)
        assert reopened.unseen_tender_ids() == set(ids)
        saved = next(t for t in reopened.all() if t.id == ids[0])
        reopened.mark_tender_seen(ids[0])
        assert next(t for t in reopened.all() if t.id == ids[0]) == saved
        assert reopened.unseen_tender_ids() == {ids[1]}
        repeated = reopened.preview_1c(line('НОВЫЙ-0'))
        assert repeated.rows[0].category == 'existing'
        assert reopened.unseen_tender_ids() == {ids[1]}
        assert reopened.save(Tender(number='РУЧНОЙ')).id not in reopened.unseen_tender_ids()
    finally:
        reopened.close()


def test_import_rollback_and_trigger_cannot_remove_flags(owner):
    repo = owner.repository
    before = repo.all()
    repo.connection.execute("CREATE TRIGGER fail_import BEFORE INSERT ON tenders WHEN NEW.number='НОВЫЙ-1' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        add(repo)
    assert repo.all() == before
    assert repo.unseen_tender_ids() == set()
    repo.connection.execute('DROP TRIGGER fail_import')
    repo.connection.execute('CREATE TRIGGER remove_flag AFTER INSERT ON tenders BEGIN UPDATE tenders SET is_unseen=0 WHERE id=NEW.id; END')
    with pytest.raises(StaleImportPlan):
        add(repo)
    assert repo.all() == before
    assert repo.unseen_tender_ids() == set()


def test_refresh_and_cancel_preserve_flags_manual_open_clears_one(owner, app, monkeypatch):
    ids = add(owner.repository)
    owner.tenders = owner.repository.all()
    owner.refresh_list()
    assert item_for(owner, ids[0]).text().startswith('Новый · ')
    assert item_for(owner, ids[0]).data(Qt.ItemDataRole.UserRole + 3)
    owner.refresh_list()
    with monkeypatch.context() as patch:
        patch.setattr(owner, 'guard_changes', lambda: False)
        owner.list.setCurrentItem(item_for(owner, ids[0]))
    assert owner.repository.unseen_tender_ids() == set(ids)
    owner.list.setCurrentItem(item_for(owner, ids[0]))
    assert owner.current.id == ids[0]
    assert owner.repository.unseen_tender_ids() == {ids[1]}
    assert not item_for(owner, ids[0]).text().startswith('Новый · ')
    assert not owner.dirty


@pytest.mark.parametrize('restore_selection', [True, False])
def test_startup_selected_card_stays_unseen_until_clicked(owner, app, tmp_path, restore_selection):
    ids = add(owner.repository)
    settings = Settings(tmp_path / 'second-settings.json')
    if restore_selection:
        settings.save(selected_id=ids[0])
    win = ExecutionWindow(owner.repository, settings, tmp_path)
    win.show()
    win.pages.setCurrentIndex(0)
    app.processEvents()
    try:
        assert win.current.id == ids[0]
        assert owner.repository.unseen_tender_ids() == set(ids)
        table = win.registry.table
        item = next(table.item(row, 0) for row in range(table.rowCount())
                    if table.item(row, 0).data(Qt.ItemDataRole.UserRole) == ids[0])
        table.scrollToItem(item)
        app.processEvents()
        point = table.visualItemRect(item).center()
        QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert owner.repository.unseen_tender_ids() == set(ids)
        QTest.mouseDClick(table.viewport(), Qt.MouseButton.LeftButton, pos=point)
        assert owner.repository.unseen_tender_ids() == {ids[1]}
    finally:
        win.dirty = False
        win.close()


@pytest.mark.parametrize('route', ['list', 'search'])
def test_write_failure_allows_view_and_preserves_draft(owner, app, monkeypatch, route):
    ids = add(owner.repository)
    owner.tenders = owner.repository.all()
    owner.refresh_list()
    def fail(_):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(owner.repository, 'mark_tender_seen', fail)
    if route == 'search':
        panel = search(owner, 'НОВЫЙ-0')
        assert panel.isVisible()
        QTest.keyClick(owner.global_search, Qt.Key.Key_Return)
    else:
        owner.list.setCurrentItem(item_for(owner, ids[0]))
    assert owner.current.id == ids[0]
    assert owner.repository.unseen_tender_ids() == set(ids)
    assert item_for(owner, ids[0]).data(Qt.ItemDataRole.UserRole + 3)
    assert 'не снята' in owner.statusBar().currentMessage()
    owner.mark_dirty()
    owner.edits['customer'].setPlainText('Новая правка')
    assert owner.dirty
    owner.mark_opened_tender_seen(ids[0])
    assert owner.dirty
    assert owner.edits['customer'].toPlainText() == 'Новая правка'


@pytest.mark.parametrize('route', ['open_assistant_tender', 'open_execution_tender'])
def test_explicit_card_routes_clear_flag(owner, route):
    ids = add(owner.repository)
    owner.tenders = owner.repository.all()
    getattr(owner, route)(ids[0])
    assert owner.current.id == ids[0]
    assert owner.repository.unseen_tender_ids() == {ids[1]}


def test_import_window_is_maximized_on_owner_screen(owner, monkeypatch):
    observed = {}
    monkeypatch.setattr(Import1CDialog, 'read_clipboard', lambda self: None)
    original_exec = Import1CDialog.exec
    def inspect(self):
        def check():
            observed['maximized'] = self.isMaximized()
            observed['screen'] = self.windowHandle().screen() == owner.screen()
            observed['modal'] = self.isModal()
            observed['controls'] = bool(self.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint)
            self.reject()
        QTimer.singleShot(50, check)
        return original_exec(self)
    monkeypatch.setattr(Import1CDialog, 'exec', inspect)
    owner.open_import_1c()
    assert observed == dict(maximized=True, screen=True, modal=True, controls=True)


def test_v9_migration_preserves_v8_data_and_backup(tmp_path):
    path = tmp_path / 'fictional.db'
    create_demo_database(path)
    repo = ExecutionRepository(path)
    before = repo.all()
    repo.close()
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE tenders DROP COLUMN is_unseen')
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute('PRAGMA user_version=8')
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / 'before-execution-v9.db').unlink()
    repo = ExecutionRepository(path)
    try:
        assert repo.all() == before
        assert repo.unseen_tender_ids() == set()
        with sqlite3.connect(tmp_path / 'before-execution-v9.db') as backup:
            repo.check_structure(backup, 8)
            assert 'is_unseen' not in {r[1] for r in backup.execute('PRAGMA table_info(tenders)')}
        with pytest.raises(sqlite3.IntegrityError):
            with repo.connection:
                repo.connection.execute('UPDATE tenders SET is_unseen=2')
    finally:
        repo.close()


def test_search_and_import_results_clear_only_opened_card(owner, app, monkeypatch):
    ids = add(owner.repository)
    owner.tenders = owner.repository.all()
    panel = search(owner, 'НОВЫЙ-0')
    assert panel.isVisible() and panel.list.count() == 1
    assert owner.repository.unseen_tender_ids() == set(ids)
    QTest.keyClick(owner.global_search, Qt.Key.Key_Return)
    assert owner.repository.unseen_tender_ids() == {ids[1]}
    dialog = Import1CDialog(owner)
    dialog.added_ids = ids
    from PySide6.QtWidgets import QListWidget
    def choose_last(picker):
        listing = picker.findChild(QListWidget)
        listing.setCurrentRow(1)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog, 'exec', choose_last)
    dialog.open_results()
    assert owner.current.id == ids[1]
    assert owner.repository.unseen_tender_ids() == set()
    dialog.deleteLater()


def test_v9_migration_failure_rolls_back_and_can_retry(tmp_path, monkeypatch):
    path = tmp_path / 'fictional.db'
    create_demo_database(path)
    repo = ExecutionRepository(path)
    before = repo.all()
    repo.close()
    with sqlite3.connect(path) as connection:
        connection.execute('ALTER TABLE tenders DROP COLUMN is_unseen')
        connection.execute("DROP TABLE IF EXISTS execution_registry_comments")
        connection.execute('PRAGMA user_version=8')
        (path.parent / "before-execution-v10.db").unlink(missing_ok=True)
    (tmp_path / 'before-execution-v9.db').unlink()
    check = ExecutionRepository.check_structure
    def fail(self, connection, version):
        if version == 9:
            raise ValueError('verification failed')
        return check(self, connection, version)
    with monkeypatch.context() as patch:
        patch.setattr(ExecutionRepository, 'check_structure', fail)
        with pytest.raises(ValueError, match='verification failed'):
            ExecutionRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 8
        assert 'is_unseen' not in {r[1] for r in connection.execute('PRAGMA table_info(tenders)')}
    repo = ExecutionRepository(path)
    try:
        assert repo.all() == before
        ids = add(repo)
        backup = repo.backup(manual=True)
        repo.mark_tender_seen(ids[0])
        repo.restore(backup)
        assert repo.unseen_tender_ids() == set(ids)
    finally:
        repo.close()
