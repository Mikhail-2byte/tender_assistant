"""Application commands, external resources, backup/restore and base shutdown."""
import os
import sqlite3
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QFileDialog, QMessageBox, QToolButton
from tender_app.models import resolve_folder, valid_web_url

class WindowActions:
    def _menus(self):
        self.list_toggle = QToolButton()
        self.list_toggle.setText("☰ Список")
        self.list_toggle.setCheckable(True)
        self.list_toggle.setChecked(True)
        self.list_toggle.setToolTip("Скрыть или показать список тендеров")
        self.list_toggle.toggled.connect(self.toggle_registry)
        self.menuBar().setCornerWidget(self.list_toggle, Qt.Corner.TopLeftCorner)
        menu = self.menuBar().addMenu("Реестр")
        for label, callback, shortcut in (
            ("Новый тендер", self.new_tender, "Ctrl+N"),
            ("Сохранить", self.save_current, "Ctrl+S"),
            ("Экспорт всех полей в CSV…", self.export_csv, ""),
            ("Резервная копия базы", self.make_backup, ""),
            ("Восстановить базу из копии…", self.restore_backup, ""),
            ("Корневая папка документов…", self.choose_root, ""),
            ("Привязать папки и обновить ссылки B2B", self.sync_resource_links, ""),
            ("Площадки…", self.configure_platforms, ""),
        ):
            action = QAction(label, self)
            action.triggered.connect(callback)
            if label == "Сохранить":
                self.save_action = action
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            menu.addAction(action)
        help_menu = self.menuBar().addMenu("Справка")
        info = help_menu.addAction("О данных и папках")
        info.triggered.connect(
            lambda: QMessageBox.information(
                self,
                "Локальный реестр",
                f"База: {self.repository.path}\n\nДокументы: {self.documents_root}\n\n"
                "Сроки — московское время (МСК, UTC+3).\nИсходные сроки не пересчитываются.\n"
                "Резервные копии содержат только базу. Документы копируйте отдельно.\n"
                "Исходный тестовый комплект не изменяется.",
            )
        )

    def open_import_1c(self):
        service = getattr(self, "ai_service", None)
        if service is not None and (service.busy() or
                (service.pending_dialog is not None and service.pending_dialog.isVisible())):
            QMessageBox.information(self, "Запрос агента ещё не завершён",
                "Дождитесь завершения запроса или закройте предложение агента перед импортом. Буфер не читался.")
            return
        if getattr(self, "_import_dialog", None) is not None:
            self._import_dialog.raise_()
            self._import_dialog.activateWindow()
            return
        from .import_dialog import Import1CDialog
        dialog = Import1CDialog(self)
        self._import_dialog = dialog
        try:
            dialog.read_clipboard()  # Explicit user click, UI thread only.
            dialog.winId()  # Create the native handle before choosing its screen.
            dialog.windowHandle().setScreen(self.screen())
            dialog.setGeometry(self.screen().availableGeometry())
            dialog.setWindowState(dialog.windowState() | Qt.WindowState.WindowMaximized)
            dialog.exec()
        finally:
            self._import_dialog = None
            dialog.deleteLater()

    def choose_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Папка тендера", str(self.documents_root)
        )
        if chosen:
            self.edits["folder_path"].setText(chosen)

    def configure_platforms(self):
        from .platform_settings import show_platform_settings
        show_platform_settings(self)

    def sync_resource_links(self):
        from tender_app.resource_sync import start_sync
        start_sync(self)

    def choose_root(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Корневая папка документов", str(self.documents_root)
        )
        if chosen:
            try:
                self.settings.save(documents_root=chosen)
            except OSError as error:
                self.error(error)
                return
            self.documents_root = Path(chosen)
            self.statusBar().showMessage("Корневая папка документов изменена", 5000)

    def open_folder(self):
        if not self.edits["folder_path"].text().strip():
            self.choose_folder()
            return
        try:
            path = resolve_folder(self.edits["folder_path"].text(), self.documents_root)
            if not path.is_dir():
                dialog = QMessageBox(self)
                dialog.setWindowTitle("Папка отсутствует")
                dialog.setText(f"Папка не найдена:\n{path}")
                choose = dialog.addButton(
                    "Выбрать существующую", QMessageBox.ButtonRole.ActionRole
                )
                create = dialog.addButton(
                    "Создать эту папку", QMessageBox.ButtonRole.ActionRole
                )
                dialog.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
                dialog.exec()
                dialog.deleteLater()
                if dialog.clickedButton() == choose:
                    self.choose_folder()
                    return
                if dialog.clickedButton() != create:
                    return
                path.mkdir(parents=True, exist_ok=True)
                from tender_app.request_workbook import create_request_workbook
                create_request_workbook(path, self.edits["number"].text().strip())
            os.startfile(str(path))
        except (OSError, ValueError) as error:
            self.error(error)

    def open_url(self):
        value = self.edits["url"].text().strip()
        if not value:
            self.edit_url()
            return
        if not valid_web_url(value):
            self.error("Укажите корректную ссылку http:// или https:// на площадку.")
            return
        if not QDesktopServices.openUrl(QUrl(value)):
            self.error(
                "Windows не смогла открыть ссылку. Проверьте браузер по умолчанию."
            )

    def export_csv(self):
        if not self.guard_changes():
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт сохранённых данных", "Тендеры.csv", "CSV (*.csv)"
        )
        if path:
            try:
                self.repository.export_csv(path)
                self.statusBar().showMessage(
                    f"Экспортированы все {len(self.tenders)} записей: {path}", 8000
                )
            except (OSError, ValueError) as error:
                self.error(error)

    def make_backup(self):
        try:
            path = self.repository.backup(manual=True)
            QMessageBox.information(
                self,
                "Резервная копия",
                f"Сохранённые данные скопированы:\n{path}\n\nДокументы резервируются отдельно.",
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            self.error(error)

    def restore_backup(self):
        job = getattr(self, 'resource_job', None)
        if job is not None or getattr(self, 'import_busy', False):
            self.statusBar().showMessage('Дождитесь завершения импорта и обработки площадок перед восстановлением базы.', 15000)
            return
        if not self.guard_changes():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Восстановление базы",
            str(self.repository.backup_dir),
            "SQLite (*.db)",
        )
        if not path:
            return
        answer = QMessageBox.question(
            self,
            "Восстановить базу?",
            "Сохранённые данные будут заменены выбранной копией.\n"
            "Перед восстановлением будет создана резервная копия текущей базы. Продолжить?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            safety = self.repository.restore(path)
            self.tenders = self.repository.all()
        except (OSError, ValueError, sqlite3.Error) as error:
            self.error(error)
            return
        self.current = None
        self.dirty = False
        self.refresh_list()
        if self.list.count():
            first_id = self.list.item(0).data(Qt.ItemDataRole.UserRole)
            self.load_tender(next(t for t in self.tenders if t.id == first_id))
            self.refresh_list()
        elif self.tenders:
            self.load_tender(self.tenders[0])
            self.refresh_list()
        else:
            self.card.setEnabled(False)
        QMessageBox.information(
            self,
            "База восстановлена",
            f"Данные восстановлены.\nПредыдущая база: {safety}",
        )

    def closeEvent(self, event):
        job = getattr(self, 'resource_job', None)
        if job is not None:
            self.resource_sync_closing = True
            job.requestInterruption()
            self.statusBar().showMessage('Завершается обработка площадок…')
            event.ignore()
            return
        if getattr(self, "import_busy", False):
            event.ignore()
            return
        if not self.guard_changes():
            event.ignore()
            return
        try:
            sizes = (
                self.splitter.sizes()
                if self.registry_panel.isVisible()
                else [self.last_list_width, self.card.width()]
            )
            self.settings.save(
                window_size=[self.width(), self.height()],
                splitter_sizes=sizes,
                list_visible=self.list_toggle.isChecked(),
                assistant_model=self.assistant.model_selector.currentData(),
                status=self.status_filter.currentData(),
                sort=self.sort.currentText(),
                query=self.search.text(),
                selected_id=self.current.id if self.current else None,
                documents_root=str(self.documents_root),
                deadline_timezone="Europe/Moscow",
            )
        except OSError as error:
            self.error(error)
            event.ignore()
            return
        # close() hides a QWidget without destroying it. Timers must not keep
        # querying its repository after the application/owner closes that DB.
        for timer in self.findChildren(QTimer):
            timer.stop()
        self._closed = True
        event.accept()
