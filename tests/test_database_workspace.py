from pathlib import Path

import pytest
from openpyxl import Workbook
from PySide6.QtCore import QLockFile, QProcess
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from tender_app.ai_dialog import AISettingsDialog
from tender_app.database_dialog import ExcelImportDialog, ExcelJob, require_idle
from tender_app.database_excel import INTRO, SHEETS, prepare_excel
from tender_app.database_workspace import (check_workspace, create_empty_database, publish_workspace,
    request_workspace_switch, restart_arguments, restart_workspace, select_data_directory)
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_window import ExecutionWindow
from tender_app.models import Tender
from tender_app.settings import Settings


def simple_excel(path):
    book = Workbook()
    book.active.title = INTRO
    for title, columns in SHEETS.items():
        sheet = book.create_sheet(title)
        sheet.append([label for _, label in columns])
    book['Тендеры'].append(['0001', 'Заказчик', 'Новый тендер'])
    book.save(path)
    book.close()
    return path


@pytest.fixture
def database_window(tmp_path, app):
    repository = ExecutionRepository(create_empty_database(tmp_path / 'current'))
    window = ExecutionWindow(repository, Settings(tmp_path / 'current' / 'settings.json'), tmp_path / 'documents')
    window.database_selection_path = tmp_path / 'selection' / 'database-selection.json'
    window.show()
    app.processEvents()
    yield window
    window.dirty = False
    window.pending_data_directory = None
    window.close()
    repository.close()


def wait_job(dialog, app):
    for _ in range(500):
        app.processEvents()
        if dialog.job is None:
            break
        QTest.qWait(10)
    assert dialog.job is None


def test_new_database_is_empty_and_never_overwrites(tmp_path):
    directory = tmp_path / 'Новая база'
    path = create_empty_database(directory)
    repository = ExecutionRepository(path)
    assert repository.all() == [] and repository.specifications() == []
    repository.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match='пустую'): create_empty_database(directory)
    assert path.read_bytes() == before


def test_lock_and_explicit_startup_priority(tmp_path):
    directory = tmp_path / 'employee'
    create_empty_database(directory)
    default = tmp_path / 'default'
    Settings(default / 'database-selection.json').save(active_data_dir=str(directory))
    assert select_data_directory(default) == directory
    assert select_data_directory(default, tmp_path / 'explicit') == tmp_path / 'explicit'
    lock = QLockFile(str(directory / 'application.lock'))
    assert lock.tryLock(100)
    try:
        with pytest.raises(ValueError, match='уже открыта'): check_workspace(directory)
    finally:
        lock.unlock()
    assert check_workspace(directory) == directory
    Settings(default / 'database-selection.json').save(active_data_dir=str(tmp_path / 'missing'))
    with pytest.raises(ValueError, match='недоступна'): select_data_directory(default)


def test_restart_selection_and_failed_launch_rollback(tmp_path, monkeypatch):
    selection = tmp_path / 'selection.json'
    Settings(selection).save(active_data_dir='previous')
    monkeypatch.setattr(QProcess, 'startDetached', lambda *args: (False, 0))
    with pytest.raises(OSError): restart_workspace(tmp_path, selection)
    assert Settings(selection).get('active_data_dir') == 'previous'
    monkeypatch.setattr(QProcess, 'startDetached', lambda *args: (True, 123))
    restart_workspace(tmp_path, selection)
    assert Settings(selection).get('active_data_dir') == str(tmp_path)
    assert restart_arguments(tmp_path, frozen=True) == ['--data-dir', str(tmp_path)]
    assert restart_arguments(tmp_path, frozen=False)[0].endswith('run_execution.py')


def test_new_import_publishes_only_finished_database_and_document_root(tmp_path):
    path = simple_excel(tmp_path / 'source.xlsx')
    plan = prepare_excel(path)
    directory = tmp_path / 'employee'
    job = ExcelJob(path, None, tmp_path / 'docs', plan=plan, new_directory=directory)
    job.run()
    assert not job.error, job.error
    repository = ExecutionRepository(directory / 'tenders.db')
    assert repository.all()[0].number == '0001'
    assert not repository.specifications()
    repository.close()
    assert Settings(directory / 'settings.json').get('documents_root') == str(tmp_path / 'docs')
    assert sorted(p.name for p in directory.iterdir()) == ['settings.json', 'tenders.db']
    assert 'Резервная копия:' not in job.result


def test_failed_new_import_leaves_no_partial_database(tmp_path):
    path = simple_excel(tmp_path / 'source.xlsx')
    plan = prepare_excel(path)
    path.write_bytes(b'corrupt')
    directory = tmp_path / 'employee'
    job = ExcelJob(path, None, tmp_path, plan=plan, new_directory=directory)
    job.run()
    assert job.error and not directory.exists()


def test_publish_preserves_nonempty_destination(tmp_path):
    source = create_empty_database(tmp_path / 'source')
    target = tmp_path / 'target'
    target.mkdir()
    sentinel = target / 'note.txt'
    sentinel.write_text('keep')
    with pytest.raises(ValueError): publish_workspace(source, target, tmp_path)
    assert list(target.iterdir()) == [sentinel]
    with pytest.raises(ValueError, match='отдельную папку'):
        create_empty_database(tmp_path / 'TenderAssistant' / 'data')
    assert not (tmp_path / 'TenderAssistant').exists()


def test_ui_preview_commit_refresh_and_report(tmp_path, database_window, app, monkeypatch):
    path = simple_excel(tmp_path / 'source.xlsx')
    dialog = ExcelImportDialog(database_window)
    dialog.show()
    dialog.start_job(path)
    wait_job(dialog, app)
    assert dialog.plan.valid
    assert '0001' in dialog.preview.toPlainText() and dialog.commit_button.isEnabled()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    dialog.commit()
    wait_job(dialog, app)
    assert dialog.imported and 'Загрузка завершена' in dialog.preview.toPlainText()
    assert not dialog.commit_button.isEnabled()
    assert len(database_window.repository.all()) == len(database_window.tenders) == 1
    report = tmp_path / 'Отчёт.txt'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(report), ''))
    dialog.save_report()
    assert 'Резервная копия:' in report.read_text(encoding='utf-8')
    dialog.reject()


def test_settings_navigation_and_switch_guard(database_window, tmp_path, app, monkeypatch):
    dialog = AISettingsDialog(database_window.ai_service, database_window)
    dialog.navigation.setCurrentRow(1)
    dialog.show()
    app.processEvents()
    assert dialog.settings_pages.currentWidget() is dialog.database_page
    new_directory = tmp_path / 'employee'
    create_empty_database(new_directory)
    monkeypatch.setattr(database_window, 'close', lambda: False)
    assert not request_workspace_switch(database_window, new_directory)
    assert database_window.pending_data_directory is None
    database_window.import_busy = True
    with pytest.raises(ValueError): require_idle(database_window)
    database_window.import_busy = False
    dialog.changed = False
    dialog.reject()


def test_new_database_switch_closes_via_draft_guard(database_window, tmp_path, monkeypatch):
    directory = tmp_path / 'employee'
    create_empty_database(directory)
    monkeypatch.setattr(database_window, 'close', lambda: True)
    assert request_workspace_switch(database_window, directory)
    assert database_window.pending_data_directory == directory


def test_report_cannot_replace_database(database_window, monkeypatch):
    dialog = ExcelImportDialog(database_window)
    before = database_window.repository.path.read_bytes()
    errors = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(database_window.repository.path), ''))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: errors.append(a[2]))
    dialog.save_report()
    assert errors and database_window.repository.path.read_bytes() == before
