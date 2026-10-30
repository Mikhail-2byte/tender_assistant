"""Read-only search across saved tenders and execution, with a shared result list."""
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget


from tender_app.record_search import SearchResult, normalized, search_records
from tender_app.search_presentation import HTML_ROLE, SearchResultDelegate, result_html


class SearchResults(QFrame):
    def __init__(self, owner, search):
        super().__init__(owner)
        self.owner, self.search = owner, search
        self.setObjectName('globalSearchResults')
        self.setStyleSheet('QFrame#globalSearchResults { background: #20262f; border: 1px solid #64789b; border-radius: 7px; }')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.count = QLabel()
        self.count.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.count)
        self.list = QListWidget()
        self.list.setStyleSheet('QListWidget::item { padding: 6px; border-bottom: 1px solid #343d4b; } '
            'QListWidget::item:selected { background: #25314a; color: #ecf0f6; } '
            'QListWidget::item:hover:!selected { background: #293345; }')
        self.list.setAccessibleName('Результаты поиска по всей работе')
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setItemDelegate(SearchResultDelegate(self.list))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.list)
        self.hint = QLabel('↑↓ — выбор · Enter — открыть · Esc — закрыть')
        self.hint.setObjectName('muted')
        layout.addWidget(self.hint)
        self.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(160)
        self.timer.timeout.connect(self.refresh)
        search.textChanged.connect(self.changed)
        self.list.itemClicked.connect(self.activate)
        self.list.itemActivated.connect(self.activate)
        owner.installEventFilter(self)
        QApplication.instance().installEventFilter(self)

    def changed(self):
        if not self.search.text().strip():
            self.timer.stop()
            self.hide()
            self.list.clear()
        else:
            self.timer.start()

    def refresh(self):
        query = self.search.text().strip().removeprefix('№').strip()
        if not query:
            self.hide()
            return
        repository = self.owner.repository
        specs = repository.specifications() if hasattr(repository, 'specifications') else []
        results = search_records(repository.all(), specs, query)
        width = min(max(650, self.search.width()), self.owner.width() - 24)
        self.list.clear()
        for result in results[:200]:
            text = f'{result.kind} · {result.title}\n{result.context}\n{result.field}: {result.snippet}'
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, result)
            item.setData(HTML_ROLE, result_html(result, query))
            self.list.addItem(item)
        self.count.setText(f'Найдено: {len(results)}' + (' · Показаны первые 200, уточните запрос' if len(results) > 200 else '')
            if results else 'Ничего не найдено. Попробуйте часть номера, имя или слово из комментария.')
        self.count.setWordWrap(True)
        if results:
            self.list.setCurrentRow(0)
        point = self.search.mapTo(self.owner, QPoint(0, self.search.height() + 5))
        x = min(max(12, point.x()), self.owner.width() - width - 12)
        self.setGeometry(x, point.y(), width, min(460, self.owner.height() - point.y() - 16))
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):
        if watched is self.owner:
            if event.type() == QEvent.Type.Show:
                QApplication.instance().installEventFilter(self)
            elif event.type() == QEvent.Type.Hide:
                QApplication.instance().removeEventFilter(self)
        if watched is self.search and event.type() == QEvent.Type.MouseButtonPress:
            if self.search.text().strip():
                self.timer.start()
        if watched in (self.search, self.list) and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.timer.stop()
                self.hide()
                self.search.setFocus()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self.timer.isActive() or not self.isVisible():
                    self.timer.stop()
                    self.refresh()
                if self.isVisible() and self.list.currentItem():
                    self.activate(self.list.currentItem())
                return True
            if watched is self.search and event.key() == Qt.Key.Key_Down:
                if not self.isVisible():
                    self.refresh()
                self.list.setFocus()
                return True
        if event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            if watched is not self.search and not self.search.isAncestorOf(watched) and watched is not self and not self.isAncestorOf(watched):
                self.timer.stop()
                self.hide()
        if watched is self.owner and event.type() in (QEvent.Type.Resize, QEvent.Type.Hide):
            self.timer.stop()
            self.hide()
        return super().eventFilter(watched, event)

    def activate(self, item):
        if not self.isVisible() or item is None:
            return
        result = item.data(Qt.ItemDataRole.UserRole)
        owner = self.owner
        # Resolve again: results may outlive a save, archive or restore operation.
        tenders = owner.repository.all()
        tender = next((t for t in tenders if t.id == result.tender_id), None)
        specs = owner.repository.specifications() if hasattr(owner.repository, 'specifications') else []
        spec = next((s for s in specs if s.id == result.spec_id), None)
        if tender is None or (result.spec_id is not None and spec is None):
            self.refresh()
            owner.statusBar().showMessage('Запись изменилась. Результаты поиска обновлены.', 5000)
            return
        self.hide()
        if not owner.guard_changes():
            return
        if owner.dirty:  # Explicit discard: clear that draft before the guarded section switch.
            saved = next((t for t in owner.repository.all() if owner.current and t.id == owner.current.id), None)
            if saved:
                owner.load_tender(saved)
            else:
                owner.current = None
                owner.dirty = False
                owner.note.clear()
                owner.card.setEnabled(False)
        owner.tenders = owner.repository.all()
        if result.kind == 'Тендер':
            if hasattr(owner, 'pages'):
                owner.pages.setCurrentIndex(0)
            owner.search.clear()
            owner.status_filter.setCurrentIndex(0)
            owner.load_tender(next(t for t in owner.tenders if t.id == result.tender_id))
            if hasattr(owner, 'show_registry_card'):
                owner.show_registry_card()
            marked_seen = owner.mark_opened_tender_seen(result.tender_id)
            owner.refresh_list()
            if result.field == 'История работы':
                owner.scroll.ensureWidgetVisible(owner.history_section)
        else:
            if result.position_id is not None:
                owner.open_search_position(result.spec_id, result.position_id)
            else:
                owner.open_specification(result.spec_id)
            if result.field == 'Комментарии спецификации':
                QTimer.singleShot(0, owner.execution, lambda: owner.execution.scroll.ensureWidgetVisible(owner.execution.spec_comment_feed))
        if result.kind != 'Тендер' or marked_seen:
            owner.statusBar().showMessage(f'{result.field}: {result.snippet}', 12000)
