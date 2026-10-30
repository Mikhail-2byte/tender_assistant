from PySide6.QtCore import QDate
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPlainTextEdit

from tender_app.models import Tender
from tender_app.resource_sync import ResourceJob, start_sync


def wait_job(window, app):
    for _ in range(400):
        app.processEvents()
        if window.resource_job is None:
            return
        QTest.qWait(5)
    raise AssertionError('Background job did not finish')


def test_unsaved_deadline_and_comment_survive_result(window, app, monkeypatch):
    tender = window.repository.save(Tender(number='123', platform='B2B', deadline_date='2026-11-01', deadline_time='12:00'))
    window.load_tender(tender)
    window.date.setDate(QDate(2026, 11, 5))
    window.time.setText('17:30')
    window.note.setPlainText('Мой несохранённый комментарий')

    def run(job):
        repository = job.repository_class.open_existing_for_import(job.path)
        try:
            with repository.connection:
                repository.connection.execute('UPDATE tenders SET deadline_date=?,deadline_time=? WHERE id=?',
                                             ('2026-10-28', '10:00', tender.id))
        finally:
            repository.close()
        job.progress.emit('Срок площадки получен')

    monkeypatch.setattr(ResourceJob, 'run', run)
    start_sync(window, {tender.id}, enrich=True)
    wait_job(window, app)
    candidate = window.candidate()
    assert candidate.deadline_date == '2026-11-05' and candidate.deadline_time == '17:30'
    assert window.note.toPlainText() == 'Мой несохранённый комментарий'
    assert window.dirty
    assert 'Срок площадки получен' in window.platform_progress_dialog.findChildren(QPlainTextEdit)[0].toPlainText()


def test_unedited_deadline_refreshes_and_startup_never_downloads(window, app, monkeypatch):
    tender = window.repository.save(Tender(number='123', deadline_date='2026-11-01', deadline_time='12:00'))
    window.load_tender(tender)
    modes = []
    def run(job):
        modes.append(job.enrich)
        repository = job.repository_class.open_existing_for_import(job.path)
        try:
            if job.enrich:
                with repository.connection:
                    repository.connection.execute('UPDATE tenders SET deadline_date=?,deadline_time=? WHERE id=?',
                                                 ('2026-10-28', '10:00', tender.id))
        finally:
            repository.close()
    monkeypatch.setattr(ResourceJob, 'run', run)
    start_sync(window)
    wait_job(window, app)
    start_sync(window, {tender.id}, enrich=True)
    wait_job(window, app)
    assert modes == [False, True]
    assert window.date.date().toString('yyyy-MM-dd') == '2026-10-28'
    assert window.time.text() == '10:00'
    assert not window.deadline_changed and not window.dirty


def test_queued_import_keeps_its_ids_and_mode(window, app, monkeypatch):
    seen = []
    def run(job):
        seen.append((job.ids, job.enrich))
        job.msleep(40)
    monkeypatch.setattr(ResourceJob, 'run', run)
    start_sync(window)
    start_sync(window, {42}, enrich=True)
    for _ in range(400):
        app.processEvents()
        QTest.qWait(5)
        if len(seen) == 2 and window.resource_job is None:
            break
    assert seen == [(None, False), ({42}, True)]
