"""Compact specification positions; full comments open in a separate dialog."""

from datetime import date
from PySide6.QtCore import Qt, Signal, Slot, QTimer, QSize, QPoint, QEvent
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import QApplication, QAbstractItemView, QHeaderView, QMenu, QTableWidget, QTableWidgetItem, QSizePolicy, QStyledItemDelegate, QStyleOptionViewItem, QStyle
from tender_app.theme import ENTITY_COLORS
from tender_app.record_search import application_identifiers
from tender_app.execution_demo import shown, date_label


def application_numbers(spec):
    numbers = []
    for position in spec["positions"]:
        document = position["supplier_document"].strip()
        for number in application_identifiers(document):
            if number not in numbers:
                numbers.append(number)
    return ", ".join(numbers) or "не указаны"


class DeadlineDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        colors = index.data(Qt.ItemDataRole.UserRole)
        if index.column() == 3 and colors:
            option = QStyleOptionViewItem(option)
            # The global selected-item stylesheet overrides palette colors.
            # Keep the deadline brush visible while other selected cells stay blue.
            option.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, option, index)


class PositionTable(QTableWidget):
    comment_requested = Signal(int)
    edit_requested = Signal(int, str)
    shipped_requested = Signal(int)
    complete_requested = Signal(int)
    widths_changed = Signal(list)

    def __init__(self, positions, read_only, widths=None, parent=None, fallback_due=None,
                 today=None, inactive=False):
        super().__init__(len(positions), 9, parent)
        self.positions = positions
        self.read_only = read_only
        self.saved_widths = widths if (isinstance(widths, list) and len(widths) == 9
            and all(type(w) is int and 42 <= w <= 3000 for w in widths)) else []
        self.resizing_columns = False
        self.columns_initialized = False
        self.setMinimumWidth(0)
        self.setItemDelegate(DeadlineDelegate(self))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setHorizontalHeaderLabels(["Позиция / чертёж", "Отгружено /\nвсего", "Осталось", "Срок поставки",
            "Приложение", "Изготовитель", "Осталось дней", "Комментарий", "Авто"])
        self.setAccessibleName("Все позиции спецификации")
        self.setStyleSheet("""
            QTableWidget { background: #1c2027; color: #ecf0f6;
                gridline-color: #343d4b; border: 1px solid #455166;
                selection-background-color: #25314a; selection-color: #ecf0f6; }
            QTableWidget::item { padding: 2px 4px; }
            QHeaderView::section { background: #232a34; color: #ecf0f6;
                border: 0; border-right: 1px solid #343d4b; padding: 4px; }
        """)
        self.setAlternatingRowColors(True)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setToolTip("Ctrl+C — копировать выделенное; Ctrl+A — выделить всё.\n"
                        "Двойной щелчок — изменить поле или открыть историю комментариев.\n"
                        "Полный текст ячейки — при наведении. Правой кнопкой — отгрузки и история.")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_copy_menu)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalHeader().hide()
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().setMinimumSectionSize(30)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setMinimumSectionSize(42)
        self.horizontalHeader().setStretchLastSection(False)
        for index, position in enumerate(positions):
            row = index
            shipped = position["customer_shipped"]
            remaining = None if shipped is None else position["ordered"] - shipped
            values = [position["name"], f"{shown(shipped) if shipped is not None else '?'} / {shown(position['ordered'])} {position.get('unit', '')}",
                      "✓ Всё" if remaining == 0 else shown(remaining),
                      date_label(position.get("due") or fallback_due),
                      application_numbers({"positions": [position]}), position["supplier"] or "—"]
            deadline = position.get("due") or fallback_due
            from tender_app.models import now_moscow
            days = (date.fromisoformat(deadline) - (today or now_moscow().date())).days if deadline else None
            comments = position.get("comments", [])
            latest = f"{comments[0][1]}\n{comments[0][0]}" if comments else position.get("working_note") or "—"
            values.extend(["Поставлено" if remaining == 0 else str(days) if days is not None else "—",
                           latest, position.get("transport") or "—"])
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 5:
                    item.setForeground(QColor(ENTITY_COLORS["company"]))
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | (
                    Qt.AlignmentFlag.AlignLeft if column not in (1, 2, 6) else Qt.AlignmentFlag.AlignRight))
                self.setItem(row, column, item)
            self.item(row, 4).setToolTip(position.get("supplier_document") or "Приложение не указано")
            self.item(row, 1).setToolTip(f"Отгружено заказчику: {shown(shipped)}. Заказано: {shown(position['ordered'])} {position.get('unit', '')}.\n"
                                        "Двойной щелчок — изменить общий итог отгрузок.")
            if remaining == 0:
                self.item(row, 2).setForeground(QColor("#8dd8ad"))
            if deadline and remaining != 0 and not inactive:
                if days <= 7:
                    item = self.item(row, 3)
                    colors = ("#ff9393", "#48282d") if days < 0 else ("#ffd27a", "#403725")
                    item.setData(Qt.ItemDataRole.UserRole, colors)
                    item.setForeground(QColor(colors[0]))
                    item.setBackground(QColor(colors[1]))
                    item.setToolTip(date_label(deadline) + (f"\nПросрочено на {abs(days)} дн." if days < 0
                                               else f"\nДо поставки: {days} дн."))
        self.horizontalHeader().sectionResized.connect(self.column_resized)
        if not read_only:
            self.cellDoubleClicked.connect(self.open_cell)
        if positions:
            self.setCurrentCell(0, 0)
        self.fit_columns_timer = QTimer(self)
        self.fit_columns_timer.setSingleShot(True)
        self.fit_columns_timer.timeout.connect(self.fit_columns)
        self.fit_columns_timer.start(0)

    def open_cell(self, row, column):
        if self.read_only:
            return
        pid = self.positions[row]["id"]
        if column in (1, 2):
            self.shipped_requested.emit(pid)
        elif column == 7:
            self.comment_requested.emit(pid)
        elif column in (3, 4, 5, 6, 8):
            self.edit_requested.emit(pid, {3: "due", 4: "supplier_document", 5: "supplier",
                6: "due", 8: "transport"}[column])

    def filter_remaining(self, only_remaining):
        for index, position in enumerate(self.positions):
            hidden = only_remaining and position["customer_shipped"] == position["ordered"]
            self.setRowHidden(index, hidden)
        self.fit_rows()

    def copy_cells(self, all_cells=False):
        cells = ({(row, column) for row in range(self.rowCount()) if not self.isRowHidden(row)
                  for column in range(self.columnCount()) if self.item(row, column)} if all_cells else
                 {(index.row(), index.column()) for index in self.selectedIndexes() if not self.isRowHidden(index.row())})
        if not cells:
            return
        rows = sorted({row for row, _ in cells})
        columns = range(min(column for _, column in cells), max(column for _, column in cells) + 1)
        lines = []
        if all_cells:
            lines.append("\t".join(" ".join(self.horizontalHeaderItem(column).text().split()) for column in columns))
        for row in rows:
            lines.append("\t".join(" ".join(self.item(row, column).text().split()) if (row, column) in cells and self.item(row, column) else ""
                                    for column in columns))
        QApplication.clipboard().setText("\n".join(lines))

    def copy_position_names(self):
        rows = sorted({index.row() for index in self.selectedIndexes() if not self.isRowHidden(index.row())})
        if rows:
            QApplication.clipboard().setText("\n".join(self.item(row, 0).text() for row in rows))

    def show_copy_menu(self, point):
        index = self.indexAt(point)
        if index.isValid() and not self.selectionModel().isSelected(index):
            self.setCurrentIndex(index)
        menu = QMenu(self)
        selected = menu.addAction("Копировать выделенное\tCtrl+C")
        selected.setEnabled(bool(self.selectedIndexes()))
        names = menu.addAction("Копировать названия позиций")
        names.setEnabled(bool(self.selectedIndexes()))
        all_cells = menu.addAction("Копировать всю таблицу с заголовками")
        callbacks = {selected: self.copy_cells, names: self.copy_position_names,
                     all_cells: lambda: self.copy_cells(True)}
        if index.isValid():
            position = self.positions[index.row()]
            pid = position.get("id")
            menu.addSeparator()
            if not self.read_only:
                shipped = menu.addAction("Изменить отгруженное количество…")
                callbacks[shipped] = lambda: self.shipped_requested.emit(pid)
                complete = menu.addAction("Отгружено всё")
                callbacks[complete] = lambda: self.complete_requested.emit(pid)
                complete.setEnabled(position["customer_shipped"] != position["ordered"])
            history = menu.addAction("История комментариев…")
            callbacks[history] = lambda: self.comment_requested.emit(pid)
        try:
            chosen = menu.exec(self.mapToGlobal(point))
        finally:
            menu.deleteLater()
        # An editor can rebuild this table. Leave the native menu event before
        # opening it, and cancel the callback if its table has been destroyed.
        if chosen in callbacks:
            QTimer.singleShot(0, self, callbacks[chosen])

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_cells()
            event.accept()
        else:
            super().keyPressEvent(event)

    def column_resized(self, *_):
        if self.resizing_columns or not self.columns_initialized:
            return
        self.saved_widths = [self.columnWidth(i) for i in range(self.columnCount())]
        self.widths_changed.emit(self.saved_widths)
        self.resizing_columns = True
        try:
            self.fit_rows()
        finally:
            self.resizing_columns = False

    @Slot()
    def fit_columns(self):
        if self.resizing_columns:
            return
        self.resizing_columns = True
        try:
            preferred = self.saved_widths or [220, 110, 70, 105, 95, 115, 105, 270, 90]
            if not self.saved_widths:
                extra = max(0, self.viewport().width() - sum(preferred))
                preferred[0] += int(extra * 0.6)
                preferred[7] += extra - int(extra * 0.6)
            available = self.viewport().width()
            if 0 < available < sum(preferred):
                # Keep saved preferences intact so widening restores them.
                extra = max(0, available - 42 * self.columnCount())
                total = sum(width - 42 for width in preferred)
                preferred = [42 + extra * (width - 42) // total for width in preferred] if total else preferred
                preferred[-1] += max(0, available - sum(preferred))
            for column, width in enumerate(preferred):
                self.setColumnWidth(column, width)
            self.columns_initialized = True
            self.fit_rows()
        finally:
            self.resizing_columns = False

    def fit_rows(self):
        width = self.horizontalHeader().length()
        for index in range(self.rowCount()):
            self.setRowHeight(index, 46)
        scrollbar = self.horizontalScrollBar().sizeHint().height() if width > self.viewport().width() else 0
        self.setFixedHeight(self.horizontalHeader().height() + sum(
            self.rowHeight(row) for row in range(self.rowCount()) if not self.isRowHidden(row))
            + self.frameWidth() * 2 + scrollbar)

    def minimumSizeHint(self):
        return QSize(240, 80)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "columns_initialized") and not self.resizing_columns:
            self.fit_columns()


class StickyPositionHeader(QHeaderView):
    """Reuse the table model in a header pinned to its outer scroll viewport."""

    def __init__(self, table, scroll):
        super().__init__(Qt.Orientation.Horizontal, scroll.viewport())
        self.table, self.scroll = table, scroll
        self.setModel(table.model())
        self.setFont(table.horizontalHeader().font())
        self.viewport().setAutoFillBackground(True)
        self.setStyleSheet(table.styleSheet())
        self.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.setMinimumSectionSize(42)
        self.setStretchLastSection(False)
        self.sectionResized.connect(lambda column, old, width: table.setColumnWidth(column, width))
        # The outer scrollbar outlives cards. A QObject slot disconnects when
        # this header dies; a lambda would retain every deleted header/table.
        table.horizontalHeader().sectionResized.connect(self.update_position)
        table.horizontalScrollBar().valueChanged.connect(self.update_position)
        scroll.verticalScrollBar().valueChanged.connect(self.update_position)
        scroll.viewport().installEventFilter(self)
        table.installEventFilter(self)
        table.destroyed.connect(self.deleteLater)
        self.position_timer = QTimer(self)
        self.position_timer.setSingleShot(True)
        self.position_timer.timeout.connect(self.update_position)
        self.hide()
        self.position_timer.start(0)

    @Slot()
    def update_position(self):
        from shiboken6 import isValid

        # A queued resize/scroll callback can outlive the table's deleteLater.
        if not isValid(self) or not isValid(self.table) or not isValid(self.scroll):
            return
        table = self.table
        point = table.mapTo(self.scroll.viewport(), QPoint(0, 0))
        height = table.horizontalHeader().height()
        self.blockSignals(True)
        for column in range(table.columnCount()):
            self.resizeSection(column, table.columnWidth(column))
        self.setOffset(table.horizontalScrollBar().value())
        self.blockSignals(False)
        self.setGeometry(point.x() + table.frameWidth(), 0,
                         table.viewport().width(), height)
        self.setVisible(table.isVisible() and point.y() < 0
                        and point.y() + table.height() > height)
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self.position_timer.start(0)
        return super().eventFilter(watched, event)
