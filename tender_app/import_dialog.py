"""Qt-only local clipboard import UI. No clipboard access from workers."""
from html import escape
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QListView, QFileDialog, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout)

from .import_1c import LABELS, ImportFormatError, validate_text_size, MAX_BYTES


class SourceEditor(QPlainTextEdit):
    """Reject oversized paste before Qt constructs a huge text document."""
    rejected = Signal(str)

    def insertFromMimeData(self, source):
        if not source.hasText():
            return
        incoming = source.text()
        try:
            incoming_size = validate_text_size(incoming)
            current = self.document().toRawText().replace('\u2029', '\n')
            selection = self.textCursor().selectedText().replace('\u2029', '\n')
            size = len(current.encode('utf-8')) - len(selection.encode('utf-8')) + incoming_size
            if size > MAX_BYTES:
                raise ImportFormatError('После вставки текст превысит 5 МиБ UTF-8. Разделите таблицу на пакеты.')
        except (ImportFormatError, UnicodeEncodeError) as exc:
            self.rejected.emit(str(exc))
            return
        super().insertFromMimeData(source)


class ImportJob(QThread):
    def __init__(self, repository_class, path, text=None, plan=None, selected=(), documents_root=None, parent=None):
        super().__init__(parent)
        self.repository_class, self.path = repository_class, path
        self.text, self.plan, self.selected = text, plan, tuple(selected)
        self.documents_root = documents_root
        self.result = None
        self.error = ''

    def run(self):
        repository = None
        try:
            repository = self.repository_class.open_existing_for_import(self.path, read_only=self.plan is None)
            self.result = (repository.preview_1c(self.text) if self.plan is None else
                           repository.import_1c(self.plan, self.selected, self.documents_root))
        except Exception as exc:
            self.error = str(exc)
        finally:
            if repository is not None:
                repository.close()


class Import1CDialog(QDialog):
    def __init__(self, owner, text=''):
        super().__init__(owner)
        self.owner = owner
        self._sync_after_import = False
        self.documents_root = owner.settings.get("import_documents_root", r"C:\Работа\В работе")
        self.setWindowTitle('Из 1С · добавление новых тендеров')
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinMaxButtonsHint)
        self.setModal(True)
        self.plan = None
        self.busy = False
        self.added_ids = ()
        self._jobs = []
        initial_error = ''
        try:
            validate_text_size(text)
        except ImportFormatError as exc:
            initial_error = str(exc)
            text = ''
        self._raw_text = text
        self._revision = 0
        self._job_revision = 0
        layout = QVBoxLayout(self)
        info = QLabel('Только новые карточки. Существующие не изменяются. Сроки: МСК (UTC+3).\n'
                      f'Папки: {self.documents_root} (изменить: «Рабочая папка…»).\n'
                      'B2B / Tender.Pro / УГМК / ТЭК-Торг: ссылки, срок и документы новых тендеров после добавления. Доступы: «Реестр → Площадки…».')
        self.info = info
        info.setWordWrap(True)
        layout.addWidget(info)
        commands = QHBoxLayout()
        self.paste_button = QPushButton('Вставить из буфера')
        self.parse_button = QPushButton('Разобрать заново')
        self.folder_button = QPushButton('Рабочая папка…')
        self.folder_button.clicked.connect(self.choose_work_folder)
        self.paste_button.clicked.connect(self.read_clipboard)
        self.parse_button.clicked.connect(self.start_preview)
        commands.addWidget(self.paste_button)
        commands.addWidget(self.parse_button)
        commands.addWidget(self.folder_button)
        commands.addStretch()
        layout.addLayout(commands)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.source = SourceEditor()
        self.source.setPlaceholderText('Вставьте таблицу 1С или нажмите «Вставить из буфера».')
        self.source.setPlainText(text)
        self.source.setMaximumBlockCount(0)  # No silent truncation of logical records.
        self.source.setMinimumHeight(50)
        self.source.setAccessibleName('Исходный текст 1С')
        self.splitter.addWidget(self.source)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(('Выбор', 'Строки', 'Категория / проверка', 'Номер', 'Заказчик',
                                              'Наименование', 'ЭТП', 'Срок · МСК', 'Комментарий', 'Подробная проверка'))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in enumerate((50,60,200,130,150,190,110,180,200,300)):
            self.table.setColumnWidth(col, width)
        self.table.setMinimumHeight(100)
        self.splitter.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText('Выберите строку: здесь видны полный комментарий, проверка и исходные поля.')
        self.details.setMinimumHeight(60)
        self.details.setAccessibleName('Полный комментарий и диагностика')
        self.splitter.addWidget(self.details)
        self.splitter.setSizes([95, 280, 100])
        layout.addWidget(self.splitter, 1)
        self.summary = QLabel(initial_error or 'Выполните разбор. До подтверждения база не изменяется.')
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        self.select_all = QPushButton('Выбрать новые')
        self.select_none = QPushButton('Снять выбор')
        self.select_all.clicked.connect(lambda: self.select_new(True))
        self.select_none.clicked.connect(lambda: self.select_new(False))
        self.open_added = QPushButton('Открыть добавленные')
        self.open_added.hide()
        self.open_added.clicked.connect(self.open_results)
        self.confirm = QPushButton('Добавить выбранные')
        self.confirm.setObjectName('primary')
        self.confirm.clicked.connect(self.confirm_import)
        self.close_button = QPushButton('Закрыть')
        self.close_button.clicked.connect(self.reject)
        for button in (self.select_all,self.select_none,self.open_added,self.confirm,self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.source.textChanged.connect(self.invalidate)
        self.source.rejected.connect(self.reject_source)
        self.table.currentCellChanged.connect(self.show_details)
        self.table.itemChanged.connect(self.update_selection)
        area = owner.screen().availableGeometry()
        self.resize(min(1120, area.width()-50), min(720, area.height()-70))
        self.update_selection()

    def choose_work_folder(self):
        chosen = QFileDialog.getExistingDirectory(self, 'Рабочая папка тендеров', self.documents_root)
        if chosen:
            try:
                self.owner.settings.save(import_documents_root=chosen)
            except OSError as error:
                self.summary.setText(str(error))
                return
            self.documents_root = chosen
            self.info.setText('Только новые карточки. Сроки: МСК (UTC+3).\n'
                f'Папки: {chosen} (изменить: «Рабочая папка…»).\n'
                'B2B / Tender.Pro / УГМК / ТЭК-Торг: ссылки, срок и документы новых тендеров после добавления. Доступы: «Реестр → Площадки…».')
            if getattr(self.owner, 'resource_sync_enabled', False):
                self.owner.sync_resource_links()

    def invalidate(self):
        self._raw_text = self.source.document().toRawText().replace('\u2029', '\n')
        self._revision += 1
        self.plan = None
        self.table.setRowCount(0)
        self.details.clear()
        self.summary.setText('Текст изменён. Нужен новый разбор; прежний план недействителен.')
        self.update_selection()

    def reject_source(self, message):
        self.invalidate()
        self.summary.setText(message + ' Исходный текст не заменён; прежний план отменён.')

    def read_clipboard(self):
        if self.busy:
            return
        # Only an explicit UI command reaches this code; never from a timer/worker.
        snapshot = QApplication.clipboard().text()
        try:
            validate_text_size(snapshot)
        except ImportFormatError as exc:
            self.reject_source(str(exc))
            return
        self.source.setPlainText(snapshot)
        self._raw_text = snapshot
        self.start_preview()

    def _set_busy(self, value):
        self.busy = value
        self.owner.import_busy = value
        self.source.setReadOnly(value)
        for widget in (self.paste_button,self.parse_button,self.folder_button,self.select_all,self.select_none,self.close_button,self.table):
            widget.setEnabled(not value)
        self.update_selection()

    def _start_job(self, *, plan=None, selected=()):
        if self.busy or getattr(self.owner, 'import_busy', False):
            self.summary.setText('Другая операция импорта ещё выполняется. Дождитесь её завершения.')
            return
        self._set_busy(True)
        self._job_revision = self._revision
        job = ImportJob(type(self.owner.repository), self.owner.repository.path,
                        text=self._raw_text if plan is None else None,
                        plan=plan, selected=selected, documents_root=self.documents_root, parent=self)
        self._jobs.append(job)
        job.finished.connect(lambda: self._finished(job))
        job.start()

    def start_preview(self):
        if self.busy:
            return
        self.plan = None
        self.table.setRowCount(0)
        self.open_added.hide()
        try:
            validate_text_size(self._raw_text)
        except ImportFormatError as exc:
            self.summary.setText(str(exc))
            self.update_selection()
            return
        self.summary.setText('Разбирается снимок текста и проверяется вся база…')
        self._start_job()

    def _finished(self, job):
        # Keep the owner protected until the completion handler has consumed the
        # plan and reported whether commit succeeded, even if refreshing fails.
        try:
            if job.error:
                self.plan = None
                self.summary.setText('Операция не выполнена: ' + job.error)
            elif job.plan is None:
                if self._job_revision == self._revision:
                    self.plan = job.result
                    self.render_plan()
                else:
                    self.summary.setText('Текст изменён во время разбора. Выполните новый разбор.')
            else:
                self.added_ids, backup = job.result
                self._sync_after_import = True
                counts = job.plan.counts
                self.plan = None
                refresh_error = ''
                try:
                    self.owner.tenders = self.owner.repository.all()
                    # Native batched layout avoids blocking on 10 000 row heights.
                    self.owner.list.setLayoutMode(QListView.LayoutMode.Batched)
                    self.owner.list.setBatchSize(100)
                    self.owner.list.setUpdatesEnabled(False)
                    self.owner.refresh_list()
                    if hasattr(self.owner, 'refresh_execution'):
                        self.owner.refresh_execution()
                except Exception as exc:
                    refresh_error = f'\nПакет уже сохранён, но обновление интерфейса не завершено: {exc}. Перезапустите приложение; не повторяйте импорт.'
                finally:
                    self.owner.list.setUpdatesEnabled(True)
                problems = counts['conflict'] + counts['error']
                skipped = len(job.plan.rows) - len(self.added_ids) - problems
                selected = set(job.selected)
                review = sum(row.deadline_needs_review for row in job.plan.rows if row.ordinal in selected)
                self.summary.setText(f'Добавлено: {len(self.added_ids)}; пропущено: {skipped}; '
                    f'конфликтов/ошибок: {problems}. Всего: {len(job.plan.rows)}. '
                    f'Добавлено с предупреждением о сроке: {review}.\n'
                    f'Проверенная резервная копия: {backup}' + refresh_error)
                self.open_added.show()
        finally:
            if job in self._jobs:
                self._jobs.remove(job)
            job.deleteLater()
            self._set_busy(False)
            if self._sync_after_import and getattr(self.owner, 'resource_sync_enabled', False):
                self._sync_after_import = False
                from tender_app.resource_sync import start_sync
                start_sync(self.owner, set(self.added_ids), enrich=True)

    def render_plan(self):
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.plan.rows))
        for i, row in enumerate(self.plan.rows):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable |
                           (Qt.ItemFlag.ItemIsUserCheckable if row.category == 'new' and not self.plan.fatal else Qt.ItemFlag.NoItemFlags))
            if row.category == 'new' and not self.plan.fatal:
                check.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(i, 0, check)
            raw = row.deadline_raw.strip()
            if row.deadline_date:
                raw = row.deadline_date + (' ' + row.deadline_time if row.deadline_time else ' · время не указано')
            elif not raw:
                raw = 'Не указан'
            category = LABELS[row.category] + (' · проверить срок' if row.deadline_needs_review else '')
            values = (f'{row.line_start}–{row.line_end}', category, row.number, row.customer,
                      row.title, row.platform, raw, row.comment, '; '.join(row.messages))
            for col, text in enumerate(values, 1):
                item = QTableWidgetItem(text)
                item.setToolTip('<qt><pre>' + escape(text) + '</pre></qt>')
                self.table.setItem(i, col, item)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        self.update_selection()
        if self.table.rowCount():
            self.table.setCurrentCell(0, 2)

    def chosen(self):
        if self.plan is None or self.plan.fatal:
            return ()
        return tuple(row.ordinal for i,row in enumerate(self.plan.rows)
                     if row.category == 'new' and self.table.item(i,0) is not None
                     and self.table.item(i,0).checkState() == Qt.CheckState.Checked)

    def update_selection(self, *_):
        chosen = self.chosen()
        self.confirm.setEnabled(bool(chosen) and not self.busy)
        if self.plan is not None and not self.busy:
            warnings = sum(bool(row.deadline_needs_review) for row in self.plan.rows)
            counts = f'Проверить срок: {warnings}; ' + '; '.join(f'{LABELS[key]}: {n}' for key,n in self.plan.counts.items())
            prefix = (self.plan.fatal + '\nРаспознанные записи и неразобранный диапазон: '
                      if self.plan.fatal else 'Всего записей: ')
            self.summary.setText(prefix + f'{len(self.plan.rows)}. {counts}. Выбрано: {len(chosen)}.')

    def select_new(self, value):
        if self.busy or self.plan is None:
            return
        self.table.blockSignals(True)
        for i,row in enumerate(self.plan.rows):
            if row.category == 'new' and not self.plan.fatal:
                self.table.item(i,0).setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
        self.table.blockSignals(False)
        self.update_selection()

    def show_details(self, current, *_):
        if self.plan is None or not 0 <= current < len(self.plan.rows):
            return
        row = self.plan.rows[current]
        self.details.setPlainText(f'Строки {row.line_start}–{row.line_end}; {LABELS[row.category]}\n'
            f'Статус 1С: {row.source_status or "не указан"}; новый статус: В работе\n'
            f'Исходный срок: {row.deadline_raw!r}; интерпретация: МСК (UTC+3)\n'
            f'Совпадения ID: {row.matches or "нет"}\n' + '\n'.join(row.messages) +
            '\n\nПолный комментарий:\n' + row.comment + '\n\nИсходные поля:\n' +
            '\n'.join(f'{i}: {value}' for i,value in enumerate(row.raw_fields,1)))

    def confirm_import(self):
        if self.busy or self.plan is None or not self.chosen():
            return
        # Saving an open card is a separate explicit user decision. It changes the
        # DB and requires a refreshed preview; Cancel never discards the draft.
        was_dirty = self.owner.dirty
        if not self.owner.guard_changes():
            return
        if was_dirty and not self.owner.dirty:
            self.start_preview()
            return
        selected = self.chosen()
        answer = QMessageBox.question(self, 'Добавить из 1С',
            f'Добавить новых карточек: {len(selected)}?\nСуществующие карточки не изменяются. '
            f'Папки будут найдены или созданы в: {self.documents_root}.\n'
            'Перед записью будет создана свежая проверенная резервная копия.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.busy or self.plan is None:
            return
        self.summary.setText('Создаётся резервная копия и записывается пакет. Не закрывайте программу…')
        self._start_job(plan=self.plan, selected=selected)

    def open_results(self):
        if self.busy or not self.added_ids:
            return
        picker = QDialog(self)
        picker.setWindowTitle('Добавленные тендеры')
        layout = QVBoxLayout(picker)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem, QDialogButtonBox
        listing = QListWidget()
        ids = set(self.added_ids)
        for tender in self.owner.repository.all():
            if tender.id in ids:
                item = QListWidgetItem(f'№ {tender.number} · {tender.customer}')
                item.setData(Qt.ItemDataRole.UserRole, tender.id)
                listing.addItem(item)
        layout.addWidget(listing)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        controls.accepted.connect(picker.accept)
        controls.rejected.connect(picker.reject)
        listing.itemDoubleClicked.connect(lambda _: picker.accept())
        layout.addWidget(controls)
        picker.resize(650,350)
        try:
            if picker.exec() == QDialog.DialogCode.Accepted and listing.currentItem():
                identifier = listing.currentItem().data(Qt.ItemDataRole.UserRole)
                tender = next((t for t in self.owner.repository.all() if t.id == identifier),None)
                if tender:
                    if hasattr(self.owner, 'registry'):
                        if not self.owner.open_registry_tender(tender.id):
                            return
                    else:
                        if not self.owner.guard_changes():
                            return
                        self.owner.load_tender(tender)
                        self.owner.mark_opened_tender_seen(tender.id)
                        self.owner.refresh_list()
                    self.accept()

        finally:
            picker.deleteLater()

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)
