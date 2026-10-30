"""Regression tests for queued completion and worker/UI ownership."""
from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal, QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog

from tender_app.ai_runtime import AIService
from tender_app.resource_sync import start_sync
from tests.test_execution_window import execution_window


class CompletedJob(QObject):
    progress = Signal(str)
    finished = Signal()

    def __init__(self, owner, ids=None, enrich=False):
        super().__init__(owner)
        self.enrich = enrich
        self.result = ({'folder_path': 0, 'url': 0}, [])
        self.reports = []
        self.interrupted = False

    def start(self):
        pass

    def isRunning(self):
        return False

    def requestInterruption(self):
        self.interrupted = True


def test_ai_remains_busy_until_gui_completion_is_processed():
    # QThread can have stopped while its result/finished signals are still queued.
    service = SimpleNamespace(jobs=[SimpleNamespace(isRunning=lambda: False)])
    assert AIService.busy(service)


def test_close_waits_for_resource_completion_even_when_thread_has_stopped(window, monkeypatch):
    job = CompletedJob(window)
    window.resource_job = job
    window.close()
    assert window.isVisible()
    assert job.interrupted
    window.resource_job = None
    window.resource_sync_closing = False


def test_resource_cleanup_survives_ui_refresh_failure(window, monkeypatch):
    import sys
    errors = []
    monkeypatch.setattr(sys, 'excepthook', lambda *args: errors.append(args))
    monkeypatch.setattr('tender_app.resource_sync.ResourceJob', CompletedJob)
    monkeypatch.setattr(window, 'refresh_list', lambda: (_ for _ in ()).throw(ValueError('refresh failed')))
    start_sync(window)
    window.resource_job.finished.emit()
    assert window.resource_job is None
    assert not window.loading
    assert not errors
    assert 'сохран' in window.statusBar().currentMessage().lower()


def test_platform_progress_dialog_does_not_accumulate(window, monkeypatch):
    monkeypatch.setattr('tender_app.resource_sync.ResourceJob', CompletedJob)
    for _ in range(5):
        start_sync(window, {1}, enrich=True)
        window.resource_job.finished.emit()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    dialogs = [d for d in window.findChildren(QDialog) if d.windowTitle() == 'Получение с площадок']
    assert len(dialogs) == 1


def test_accepted_close_stops_owned_timers(window):
    from PySide6.QtCore import QTimer
    assert window.clock_timer.isActive()
    window.close()
    assert not any(timer.isActive() for timer in window.findChildren(QTimer))
    start_sync(window)
    assert getattr(window, 'resource_job', None) is None


def test_position_menu_defers_actions_until_native_menu_has_returned(execution_window, app, monkeypatch):
    from PySide6.QtWidgets import QMenu
    owner = execution_window
    owner.open_specification(owner.repository.specifications()[0].id)
    table = owner.execution.position_tables[0]
    events = []
    table.shipped_requested.disconnect()
    table.shipped_requested.connect(events.append)

    class Menu(QMenu):
        def exec(self, *_):
            action = next(a for a in self.actions() if a.text() == 'Изменить отгруженное количество…')
            action.trigger()
            assert events == []
            return action

    monkeypatch.setattr('tender_app.execution_positions.QMenu', Menu)
    table.show_copy_menu(table.visualRect(table.model().index(0, 0)).center())
    assert events == []
    app.processEvents()
    assert events == [table.positions[0]['id']]
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not table.findChildren(QMenu)
