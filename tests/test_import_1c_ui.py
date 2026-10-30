"""Qt UI tests: fake input only, never read the real clipboard or call AI."""
from dataclasses import replace
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QPushButton

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.import_dialog import Import1CDialog
from tender_app.settings import Settings
from tests.test_import_1c import line


@pytest.fixture
def owner(tmp_path,app):
    path=tmp_path/'fictional.db'
    create_demo_database(path)
    repo=ExecutionRepository(path)
    settings=Settings(tmp_path/'settings.json')
    settings.save(documents_root=str(tmp_path), import_documents_root=str(tmp_path))
    win=ExecutionWindow(repo,settings,tmp_path)
    win.resize(1366,768)
    win.show();app.processEvents()
    yield win
    win.dirty=False
    win.close();repo.close();app.processEvents()


def wait(dialog,app,timeout=20):
    end=time.monotonic()+timeout
    while dialog.busy and time.monotonic()<end:
        app.processEvents();QTest.qWait(5)
        # qWait can retain the GIL on Windows. Let Python workers import and
        # process the workbook instead of starving them in this polling loop.
        time.sleep(.001)
    assert not dialog.busy, 'Worker did not finish'
    app.processEvents()


def show(owner,text,app):
    dialog=Import1CDialog(owner,text)
    dialog.show();dialog.start_preview();wait(dialog,app)
    return dialog


def yes(monkeypatch):
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**k: QMessageBox.StandardButton.Yes)


def test_button_in_tender_registry(owner):
    button=owner.registry.import_button
    assert button.text()=='Из 1С'
    assert owner.registry.isAncestorOf(button)
    assert [owner.pages.tabText(i) for i in range(owner.pages.count())]==['Реестр','Исполнение','Агент','Реестр исполнения']


def test_preview_selection_and_edit_invalidation(owner,app):
    before=owner.repository.all(),owner.repository.specifications()
    dialog=show(owner,line()+line()+line('002',comment='<b>текст</b>\n'+'длинный '*300),app)
    try:
        assert dialog.plan.counts['new']==2 and len(dialog.chosen())==2
        assert dialog.table.rowCount()==3
        assert 'Всего записей: 3' in dialog.summary.text()
        dialog.table.setCurrentCell(2,2)
        assert '<b>текст</b>' in dialog.details.toPlainText() and 'длинный '*299 in dialog.details.toPlainText()
        dialog.select_none.click();assert not dialog.confirm.isEnabled()
        dialog.select_all.click();assert len(dialog.chosen())==2
        dialog.source.setPlainText(line('003'))
        assert dialog.plan is None and not dialog.confirm.isEnabled()
        assert (owner.repository.all(),owner.repository.specifications())==before
        dialog.reject();assert not dialog.isVisible()
    finally:dialog.reject()


def test_cancel_guard_preserves_card_and_note(owner,app,monkeypatch):
    dialog=show(owner,line(),app)
    owner.edits['contacts'].setPlainText('Не сохранённый контакт')
    owner.note.setPlainText('Черновик истории')
    before=owner.repository.all()
    monkeypatch.setattr(owner,'guard_changes',lambda:False)
    dialog.confirm_import()
    assert not dialog.busy and owner.dirty
    assert owner.edits['contacts'].toPlainText()=='Не сохранённый контакт'
    assert owner.note.toPlainText()=='Черновик истории'
    assert owner.repository.all()==before
    dialog.reject()
    assert owner.dirty


def test_worker_commit_double_confirmation_and_refresh(owner,app,monkeypatch):
    dialog=show(owner,line()+line('002'),app)
    before=owner.repository.all();specs=owner.repository.specifications()
    yes(monkeypatch)
    dialog.confirm_import()
    assert dialog.busy and not dialog.confirm.isEnabled()
    dialog.confirm_import()
    dialog.reject();assert dialog.isVisible()
    event=QCloseEvent();dialog.closeEvent(event);assert not event.isAccepted()
    event=QCloseEvent();owner.closeEvent(event);assert not event.isAccepted()
    wait(dialog,app)
    assert len(dialog.added_ids)==2 and dialog.plan is None and not dialog.confirm.isEnabled()
    dialog.confirm_import()
    assert len(owner.repository.all())==len(before)+2
    assert owner.repository.all()[:-2]==before and owner.repository.specifications()==specs
    assert len(owner.tenders)==len(before)+2
    assert len(owner.execution.tenders)==len(before)+2
    assert owner.list.count()==len(before)+2
    assert 'Добавлено: 2' in dialog.summary.text()
    assert dialog.open_added.isVisible()
    dialog.reject()


def test_changed_database_needs_new_preview(owner,app,monkeypatch):
    dialog=show(owner,line(),app)
    owner.repository.save(replace(owner.repository.all()[0],comment='Вымышленное изменение'))
    yes(monkeypatch)
    dialog.confirm_import();wait(dialog,app)
    assert not dialog.added_ids and not dialog.confirm.isEnabled()
    assert 'Обновите предпросмотр' in dialog.summary.text()
    dialog.start_preview();wait(dialog,app)
    assert dialog.confirm.isEnabled()
    dialog.reject()


def test_snapshot_not_reread_and_raw_nbsp_retained(owner,app,monkeypatch):
    text=line('000\xa0 123')
    dialog=show(owner,text,app)
    assert dialog.plan.text==text and dialog.plan.rows[0].raw_fields[4]=='000\xa0 123'
    # Any unexpected clipboard read during confirmation must fail this test.
    from PySide6.QtWidgets import QApplication
    monkeypatch.setattr(QApplication,'clipboard',lambda: (_ for _ in ()).throw(AssertionError('Clipboard reread')))
    yes(monkeypatch)
    dialog.confirm_import();wait(dialog,app)
    assert len(dialog.added_ids)==1
    dialog.reject()


def test_warning_plain_text_and_no_default_selection_for_errors(owner,app):
    dialog=show(owner,line(deadline='31.02.2026',comment='<script>literal</script>')+'bad\n',app)
    assert dialog.table.rowCount()==2 and len(dialog.chosen())==1
    dialog.table.setCurrentCell(0,2)
    assert 'требуется проверка' in dialog.details.toPlainText()
    assert '<script>literal</script>' in dialog.details.toPlainText()
    assert dialog.table.item(1,0).checkState()==Qt.CheckState.Unchecked
    dialog.reject()


@pytest.mark.parametrize('size',[(1024,640),(1152,700),(1366,768)])
def test_laptop_dialog_controls_fit(owner,app,size):
    dialog=show(owner,line(comment='Длинная заметка '*100),app)
    try:
        dialog.resize(*size);QTest.qWait(30)
        for widget in (dialog.confirm,dialog.close_button,dialog.summary,dialog.paste_button,dialog.details):
            assert widget.isVisible()
            point=widget.mapTo(dialog,widget.rect().bottomRight())
            assert 0<=point.x()<dialog.width() and 0<=point.y()<dialog.height()
        assert dialog.table.horizontalScrollBar().maximum()>0
    finally:dialog.reject()


def test_backup_error_is_visible_without_database_changes(owner,app,monkeypatch):
    dialog=show(owner,line(),app)
    before=owner.repository.all(),owner.repository.specifications()
    def fail(self,*a,**kw):raise PermissionError('Вымышленная ошибка резервной копии')
    monkeypatch.setattr(ExecutionRepository,'backup',fail)
    yes(monkeypatch)
    dialog.confirm_import();wait(dialog,app)
    assert 'Вымышленная ошибка' in dialog.summary.text() and not dialog.confirm.isEnabled()
    assert (owner.repository.all(),owner.repository.specifications())==before
    dialog.reject()


def test_programmatic_source_change_during_parse_invalidates_result(owner,app):
    dialog=Import1CDialog(owner,line())
    dialog.show();dialog.start_preview()
    dialog.source.setPlainText(line('CHANGED'))
    wait(dialog,app)
    assert dialog.plan is None and not dialog.confirm.isEnabled()
    dialog.reject()


def test_large_preview_ui_heartbeat(owner,app,monkeypatch):
    from PySide6.QtCore import QTimer
    # This stress test measures Qt/SQLite responsiveness; real folder writes
    # and rollback are covered separately in test_import_resources.py.
    monkeypatch.setattr('tender_app.import_storage.import_folder',
        lambda root, number, created, index: Path(root) / number)
    dialog=Import1CDialog(owner,''.join(line(f'УЧЕБНЫЙ-{i}') for i in range(10_000)))
    dialog.show()
    ticks=[]
    timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start()
    started=time.monotonic()
    dialog.start_preview();wait(dialog,app,60)
    assert dialog.table.rowCount()==10_000 and len(dialog.chosen())==10_000
    assert ticks, 'GUI event loop must keep running during parse'
    yes(monkeypatch)
    ticks.clear();dialog.confirm_import();wait(dialog,app,60)
    timer.stop()
    assert ticks and len(dialog.added_ids)==10_000
    dialog.reject()


def test_saved_open_card_requires_second_review_not_automatic_import(owner,app,monkeypatch):
    dialog=show(owner,line(),app)
    owner.edits['contacts'].setPlainText('Учебный новый контакт')
    monkeypatch.setattr(owner,'guard_changes',lambda:owner.save_tender())
    yes(monkeypatch)
    dialog.confirm_import();wait(dialog,app)
    assert dialog.plan is not None and not dialog.added_ids
    assert not any(t.number=='000123/А-26' for t in owner.repository.all())
    dialog.confirm_import();wait(dialog,app)
    assert len(dialog.added_ids)==1
    dialog.reject()


def test_unchanged_ai_settings_history_and_execution_drafts(owner,app,monkeypatch):
    settings=owner.settings.path.parent/'ai-settings.json'
    settings.write_text('{"fictional_marker":"must not change"}',encoding='utf-8')
    before=settings.read_bytes()
    owner.execution.comment_drafts[1]='Учебный черновик спецификации'
    dialog=show(owner,line(),app)
    yes(monkeypatch)
    dialog.confirm_import();wait(dialog,app)
    assert len(dialog.added_ids)==1 and settings.read_bytes()==before
    assert owner.execution.comment_drafts[1]=='Учебный черновик спецификации'
    owner.execution.comment_drafts.clear()
    dialog.reject()


def test_no_commit_if_final_confirmation_cancelled(owner,app,monkeypatch):
    dialog=show(owner,line(),app)
    before=owner.repository.all()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.No)
    dialog.confirm_import()
    assert not dialog.busy and dialog.plan is not None
    assert owner.repository.all()==before
    dialog.reject()


def test_post_commit_ui_error_does_not_claim_rollback(owner,app,monkeypatch):
    dialog=show(owner,line(),app)
    def fail():raise RuntimeError('Учебная ошибка UI')
    monkeypatch.setattr(owner,'refresh_list',fail)
    yes(monkeypatch)
    dialog.confirm_import();wait(dialog,app)
    assert len(dialog.added_ids)==1
    assert 'Пакет уже сохранён' in dialog.summary.text()
    assert not dialog.confirm.isEnabled()
    dialog.reject()
