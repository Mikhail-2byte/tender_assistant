"""Creation and persistent selection of independent local data directories."""
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QLockFile, QProcess

from tender_app.repository import require_local
from tender_app.settings import Settings


def validate_workspace_path(directory):
    directory = require_local(directory)
    root = Path(__file__).resolve().parents[1]
    protected = [root / 'tender_app/data', root / 'data/sample',
                 root / 'dist/TenderAssistant', root / 'dist/TenderAssistantChatPreview']
    if (any(directory == path.resolve() or path.resolve() in directory.parents for path in protected)
            or any(path.name in {'TenderAssistant', 'TenderAssistantChatPreview'} for path in (directory, *directory.parents))):
        raise ValueError('Выберите отдельную папку данных пробной версии исполнения.')
    return directory


def _publish_file(source, destination):
    # Windows rename is atomic and refuses an existing destination (also works on FAT/exFAT).
    if os.name == 'nt':
        os.rename(source, destination)
    else:
        os.link(source, destination)


def create_empty_database(directory):
    from tender_app.execution_repository import ExecutionRepository
    directory = validate_workspace_path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('Для новой базы выберите пустую папку. Существующие файлы не заменяются.')
    directory.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='tender-empty-', dir=directory.parent) as temporary:
        staging = Path(temporary) / 'tenders.db'
        schema = Path(__file__).parent / 'assets' / 'base_schema.sql'
        with closing(sqlite3.connect(staging)) as connection:
            connection.executescript(schema.read_text(encoding='utf-8'))
        repository = ExecutionRepository(staging)
        repository.close()
        directory.mkdir(exist_ok=True)
        # Atomic exclusive publication: never overwrite a competing creator's DB.
        _publish_file(staging, directory / 'tenders.db')
    return directory / 'tenders.db'


def check_workspace(directory):
    from tender_app.execution_repository import ExecutionRepository
    directory = validate_workspace_path(directory)
    if not (directory / 'tenders.db').is_file():
        raise ValueError('В папке нет tenders.db. Выберите папку базы программы.')
    lock = QLockFile(str(directory / 'application.lock'))
    if not lock.tryLock(100):
        raise ValueError('Эта база уже открыта другим экземпляром программы.')
    try:
        repository = ExecutionRepository.open_existing_for_import(directory / 'tenders.db', read_only=True)
        repository.close()
    finally:
        lock.unlock()
    return directory


def publish_workspace(database, directory, documents_root):
    """Publish a finished import exclusively, preserving the root of relative document paths."""
    directory = validate_workspace_path(directory)
    if directory.exists() and any(directory.iterdir()):
        raise ValueError('Папка новой базы уже не пуста.')
    staging_settings = Path(database).parent / 'settings.json'
    Settings(staging_settings).save(documents_root=str(documents_root))
    directory.mkdir(exist_ok=True)
    created = []
    try:
        for source in (staging_settings, Path(database)):
            destination = directory / source.name
            _publish_file(source, destination)
            created.append(destination)
    except Exception:
        for destination in reversed(created):
            destination.unlink()
        raise


def select_data_directory(default, explicit=None):
    if explicit is not None:
        return require_local(explicit)
    selected = Settings(Path(default) / 'database-selection.json').get('active_data_dir', '')
    if selected and not (Path(selected) / 'tenders.db').is_file():
        raise ValueError(f'Выбранная база недоступна: {selected}. Восстановите папку или запустите с --data-dir.')
    return require_local(selected or default)


def request_workspace_switch(window, directory):
    """Close through the normal draft/job guards; restart only after the old lock is released."""
    if Path(directory).resolve() == window.repository.path.parent:
        raise ValueError('Эта база уже открыта.')
    directory = check_workspace(directory)
    if not hasattr(window, 'database_selection_path'):
        raise ValueError('Переключение доступно при штатном запуске приложения.')
    # closeEvent already owns all draft and background-task decisions.
    window.pending_data_directory = directory
    if not window.close():
        window.pending_data_directory = None
        return False
    return True


def restart_arguments(directory, frozen=None):
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    arguments = [] if frozen else [str(Path(__file__).resolve().parents[1] / 'run_execution.py')]
    return arguments + ['--data-dir', str(directory)]


def restart_workspace(directory, selection_path):
    # Called after repository.close() and QLockFile.unlock() in main's finally.
    previous = Settings(selection_path)
    old_value = previous.get('active_data_dir', '')
    previous.save(active_data_dir=str(directory))
    result = QProcess.startDetached(sys.executable, restart_arguments(directory))
    started = result[0] if isinstance(result, tuple) else result
    if not started:
        previous.save(active_data_dir=old_value)
        raise OSError('Не удалось перезапустить приложение. Выбор базы не изменён.')
