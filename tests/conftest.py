import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from tender_app.repository import Repository
from tender_app.settings import Settings
from tender_app.window import Window


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "sample" / "tenders.db"


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def release_test_widgets(app):
    """A closed Qt window is hidden, not destroyed; isolate test ownership."""
    previous = set(app.topLevelWidgets())
    yield
    for widget in set(app.topLevelWidgets()) - previous:
        if not isValid(widget):
            continue
        workers = widget.findChildren(QThread)
        assert not any(job.isRunning() for job in workers), 'Test left a running QThread'
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "Папка с пробелами" / "tenders.db", SEED)
    yield repository
    repository.close()


@pytest.fixture
def window(repo, app, tmp_path):
    documents = tmp_path / "documents"
    documents.mkdir()
    for tender in repo.all():
        if tender.folder_path and not Path(tender.folder_path).is_absolute():
            (documents / tender.folder_path).mkdir(parents=True, exist_ok=True)
    win = Window(repo, Settings(tmp_path / "settings.json"), documents)
    win.show()
    app.processEvents()
    yield win
    win.dirty = False
    win.close()
    app.processEvents()
