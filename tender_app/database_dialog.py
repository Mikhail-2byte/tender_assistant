"""Settings page and background Excel onboarding wizard."""
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QThread, Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                              QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from tender_app.database_excel import prepare_excel, save_template
from tender_app.database_workspace import (create_empty_database, publish_workspace,
                                           request_workspace_switch, validate_workspace_path)
from tender_app.execution_repository import ExecutionRepository
from tender_app.storage_io import atomic_text


def require_idle(window):
    if (getattr(window, 'import_busy', False) or getattr(window, 'resource_job', None) is not None
            or window.ai_service.busy() or window.ai_service.pending_dialog is not None):
        raise ValueError('Дождитесь завершения импорта, обработки площадок или запроса агента и закройте предложение агента.')


class ExcelJob(QThread):
    def __init__(self, path, database, documents_root, *, plan=None, new_directory=None, parent=None):
        super().__init__(parent)
        self.path, self.database, self.documents_root = path, database, documents_root
        self.plan, self.new_directory = plan, new_directory
        self.result = None
        self.error = ''

    def run(self):
        repository = None
        try:
            if self.new_directory is not None and self.plan is not None:
                from tender_app.database_import import commit_excel
                directory = validate_workspace_path(self.new_directory)
                if directory.exists() and any(directory.iterdir()):
                    raise ValueError('Папка новой базы уже не пуста. Выберите другую папку.')
                with TemporaryDirectory(prefix='tender-import-', dir=directory.parent) as temporary:
                    database = create_empty_database(Path(temporary) / 'data')
                    repository = ExecutionRepository(database)
                    fresh = prepare_excel(self.path, repository, self.documents_root)
                    if fresh.digest != self.plan.digest or not fresh.valid:
                        raise ValueError('Excel изменился после проверки. Проверьте файл снова.')
                    report = commit_excel(repository, fresh, self.documents_root, backup_current=False)
                    repository.close()
                    repository = None
                    publish_workspace(database, directory, self.documents_root)
                    self.result = report + f'\nНовая база: {directory / "tenders.db"}'
            else:
                if self.database is not None:
                    repository = ExecutionRepository.open_existing_for_import(self.database, read_only=self.plan is None)
                self.result = (prepare_excel(self.path, repository, self.documents_root) if self.plan is None
                               else repository.import_excel(self.plan, self.documents_root))
        except Exception as error:
            self.error = str(error)
        finally:
            if repository:
                repository.close()


class ExcelImportDialog(QDialog):
    def __init__(self, owner, *, new_directory=None, parent=None):
        super().__init__(parent or owner)
        self.owner, self.new_directory = owner, new_directory
        self.plan = None
        self.job = None
        self.imported = False
        self.setWindowTitle('Новая база из Excel' if new_directory else 'Добавление из Excel')
        self.resize(1000, 610)
        self.setMinimumSize(720, 430)
        layout = QVBoxLayout(self)
        intro = QLabel('1. Выберите заполненный шаблон.  2. Проверьте все записи.  3. Загрузите.\n'
                       'Ошибки блокируют весь файл. Совпавшие номера пропускаются вместе с исполнением.\n'
                       'Документы остаются на своих местах. Даты и время подачи — МСК.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.destination = QLabel(f'База: {new_directory or owner.repository.path.parent}')
        self.destination.setTextFormat(Qt.TextFormat.PlainText)
        self.destination.setWordWrap(True)
        layout.addWidget(self.destination)
        row = QHBoxLayout()
        self.choose_button = QPushButton('Выбрать / проверить Excel…')
        self.report_button = QPushButton('Сохранить отчёт…')
        self.commit_button = QPushButton('Загрузить в базу')
        self.commit_button.setEnabled(False)
        row.addWidget(self.choose_button)
        row.addWidget(self.report_button)
        row.addStretch()
        row.addWidget(self.commit_button)
        layout.addLayout(row)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText('После проверки здесь появятся записи, совпадения и ошибки с номерами строк.')
        layout.addWidget(self.preview, 1)
        self.close_button = QPushButton('Закрыть')
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.choose_button.clicked.connect(self.choose_file)
        self.commit_button.clicked.connect(self.commit)
        self.report_button.clicked.connect(self.save_report)

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Заполненный шаблон', '', 'Excel (*.xlsx)')
        if path:
            self.start_job(path)

    def start_job(self, path, commit=False):
        try:
            require_idle(self.owner)
            if not self.owner.guard_changes():
                return
            # Execution drafts are not overwritten by a new-record-only import.
            self.owner.import_busy = True
            self.job = ExcelJob(path, None if self.new_directory else self.owner.repository.path,
                                self.owner.documents_root, plan=self.plan if commit else None,
                                new_directory=self.new_directory, parent=self)
            self._committing = commit
            for button in (self.choose_button, self.commit_button, self.close_button):
                button.setEnabled(False)
            self.preview.setPlainText('Сохраняется пакет…' if commit else 'Проверяется файл…')
            self.job.finished.connect(self.job_finished)
            self.job.start()
        except Exception as error:
            self.owner.import_busy = False
            QMessageBox.warning(self, 'Загрузка Excel', str(error))

    def job_finished(self):
        job = self.job
        self.job = None
        self.owner.import_busy = False
        self.choose_button.setEnabled(True)
        self.close_button.setEnabled(True)
        if job.error:
            self.plan = None
            self.preview.setPlainText('Загрузка не завершена.\n' + job.error)
        elif self._committing:
            self.imported = True
            self.plan = None
            self.preview.setPlainText(job.result)
            self.choose_button.setEnabled(self.new_directory is None)
            if not self.new_directory:
                try:
                    self.owner.refresh_execution()
                    self.owner.refresh_list()
                except Exception as error:
                    self.preview.appendPlainText(f'Данные сохранены; обновление интерфейса не завершено: {error}. Перезапустите программу, не повторяйте загрузку.')
            else:
                self.close_button.setText('Открыть новую базу')
        else:
            self.plan = job.result
            self.preview.setPlainText(self.plan.report())
            self.commit_button.setEnabled(self.plan.valid and len(self.plan.tenders) > len(self.plan.skipped))
        job.deleteLater()

    def commit(self):
        if self.plan and self.plan.valid:
            answer = QMessageBox.question(self, 'Загрузить проверенные записи?',
                self.plan.report()[:2500] + '\n\nСохранить перечисленные новые записи?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.Yes:
                self.start_job(self.plan.path, commit=True)

    def save_report(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Отчёт переноса', 'Отчёт_переноса.txt', 'Текст (*.txt)')
        if path:
            try:
                if Path(path).suffix.lower() != '.txt':
                    raise ValueError('Для отчёта выберите отдельный файл .txt.')
                with atomic_text(Path(path)) as stream:
                    stream.write(self.preview.toPlainText())
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, 'Отчёт не сохранён', str(error))

    def reject(self):
        if self.job is None:
            super().reject()

    def closeEvent(self, event):
        if self.job is not None:
            event.ignore()
        else:
            super().closeEvent(event)


class DatabaseSettingsPage(QWidget):
    def __init__(self, owner, settings_dialog):
        super().__init__()
        self.owner, self.settings_dialog = owner, settings_dialog
        layout = QVBoxLayout(self)
        title = QLabel('База данных')
        title.setStyleSheet('font-size:18px;font-weight:600;')
        layout.addWidget(title)
        current = QLabel(f'Текущая база:\n{owner.repository.path}')
        current.setTextFormat(Qt.TextFormat.PlainText)
        current.setWordWrap(True)
        layout.addWidget(current)
        description = QLabel('Перенесите тендеры и исполнение без нейросети. Сначала сохраните шаблон и заполните его.\n'
                             'Можно добавить новые тендеры в текущую базу или создать собственную базу в пустой папке. '
                             'Открытие другой базы перезапускает программу; её выбор сохраняется.')
        description.setWordWrap(True)
        layout.addWidget(description)
        for label, callback in (
            ('1. Сохранить Excel-шаблон…', self.template),
            ('2. Проверить и добавить Excel в текущую базу…', self.import_current),
            ('Создать новую базу из Excel…', self.create_database),
            ('Открыть существующую базу…', self.open_database),
            ('Резервная копия текущей базы', self.backup),
            ('Восстановить текущую базу из копии…', self.restore),
        ):
            button = QPushButton(label)
            button.setMinimumHeight(36)
            button.clicked.connect(callback)
            layout.addWidget(button)
        layout.addStretch()

    def template(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Сохранить пустой шаблон', 'Шаблон_базы.xlsx', 'Excel (*.xlsx)')
        if path:
            try:
                save_template(path)
                QMessageBox.information(self, 'Шаблон сохранён', f'Заполните файл и выберите загрузку Excel.\n{path}')
            except Exception as error:
                QMessageBox.warning(self, 'Шаблон не сохранён', str(error))

    def run_import(self, directory=None):
        try:
            require_idle(self.owner)
            if not self.settings_dialog.discard_ok():
                return
            dialog = ExcelImportDialog(self.owner, new_directory=directory, parent=self.settings_dialog)
            try:
                dialog.exec()
                imported = dialog.imported
            finally:
                dialog.deleteLater()
            if imported and directory:
                self.switch(directory)
        except Exception as error:
            QMessageBox.warning(self, 'База данных', str(error))

    def import_current(self):
        self.run_import()

    def create_database(self):
        directory = QFileDialog.getExistingDirectory(self, 'Выберите пустую локальную папку для новой базы')
        if directory:
            if any(Path(directory).iterdir()):
                QMessageBox.warning(self, 'Папка не пуста', 'Создайте и выберите пустую папку. Файлы заменяться не будут.')
                return
            self.run_import(Path(directory))

    def open_database(self):
        directory = QFileDialog.getExistingDirectory(self, 'Папка существующей базы (с файлом tenders.db)')
        if directory:
            self.switch(Path(directory))

    def switch(self, directory):
        try:
            require_idle(self.owner)
            if not self.settings_dialog.discard_ok():
                return
            if request_workspace_switch(self.owner, directory):
                self.settings_dialog.accept()
        except Exception as error:
            QMessageBox.warning(self, 'База не открыта', str(error))

    def backup(self):
        try:
            require_idle(self.owner)
            self.owner.make_backup()
        except Exception as error:
            QMessageBox.warning(self, 'Резервная копия', str(error))

    def restore(self):
        try:
            require_idle(self.owner)
            self.owner.restore_backup()
        except Exception as error:
            QMessageBox.warning(self, 'Восстановление', str(error))
