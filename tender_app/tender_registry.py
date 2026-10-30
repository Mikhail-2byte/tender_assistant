"""Full-width tender overview with right-click editing and number copying."""
import re

from PySide6.QtCore import QEvent, QRect, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QStyledItemDelegate,
    QStyleOptionViewItem, QToolTip, QStyle,
)

from tender_app.models import ACTIVE, STATUSES, deadline, deadline_label, deadline_copy_text, overdue
from tender_app.window import STATUS_COLORS
from tender_app.theme import ENTITY_COLORS

REGISTRY_FIELDS = ("number", "customer", "title", "deadline", "status", "platform", "comment")
COMMENT_HEADER = re.compile(r"(?:\A|\n\n)\[\d{2}\.\d{2}\.\d{4} \d{2}:\d{2} МСК\]\n")
UNSEEN_ROLE = Qt.ItemDataRole.UserRole + 3
DEADLINE_COPY_ROLE = Qt.ItemDataRole.UserRole + 4


class NumberDelegate(QStyledItemDelegate):
    """Keep copy controls and badges outside the number's display/sort value."""

    @staticmethod
    def copy_rect(rect):
        return QRect(rect.right() - 25, rect.center().y() - 12, 24, 24)

    def copy_text(self, index):
        return index.data(Qt.ItemDataRole.DisplayRole)

    def paint(self, painter, option, index):
        badge = (index.column() == 0 and index.data(UNSEEN_ROLE)
                 and option.rect.width() >= option.fontMetrics.horizontalAdvance(index.data() or "") + 128)
        text_option = QStyleOptionViewItem(option)
        text_option.rect.adjust(0, 0, -28, 0)
        if badge:
            text_option.rect.adjust(0, 0, -70, 0)
        if index.column() == 3:
            # Draw the two deadline lines explicitly: styled Qt tables can elide
            # a multiline delegate value into one line before painting it.
            background = QStyleOptionViewItem(option)
            self.initStyleOption(background, index)
            text = background.text
            background.text = ""
            option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem,
                                             background, painter, option.widget)
            painter.save()
            painter.setFont(background.font)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            color = background.palette.color(QPalette.ColorRole.HighlightedText if selected
                                             else QPalette.ColorRole.Text)
            painter.setPen(color)
            rect = option.rect.adjusted(6, 0, -34, 0)
            painter.setClipRect(rect)
            lines = text.splitlines()
            if rect.height() < background.fontMetrics.lineSpacing() * 2 and len(lines) == 2:
                text = lines[0][:5] + " " + ("—" if lines[1] == "Без времени" else lines[1])
            text = "\n".join(background.fontMetrics.elidedText(
                line, Qt.TextElideMode.ElideRight, rect.width()) for line in text.splitlines())
            painter.drawText(rect,
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             text)
            painter.restore()
        else:
            super().paint(painter, text_option, index)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor("#a9c8e3" if self.copy_text(index) else "#596575"))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        icon = self.copy_rect(option.rect).center()
        painter.drawRoundedRect(QRect(icon.x() - 6, icon.y() - 7, 9, 11), 1, 1)
        painter.drawRoundedRect(QRect(icon.x() - 2, icon.y() - 3, 9, 11), 1, 1)
        painter.restore()
        if not badge:
            return
        painter.save()
        rect = option.rect.adjusted(option.rect.width() - 94, 6, -33, -6)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#345b78"))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor("#d7efff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Новый")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                             QEvent.Type.MouseButtonDblClick)
                and event.button() == Qt.MouseButton.LeftButton
                and self.copy_rect(option.rect).contains(event.position().toPoint())):
            if event.type() == QEvent.Type.MouseButtonRelease and self.copy_text(index):
                QApplication.clipboard().setText(self.copy_text(index))
            return True
        return super().editorEvent(event, model, option, index)

    def helpEvent(self, event, view, option, index):
        if self.copy_rect(option.rect).contains(event.pos()):
            QToolTip.showText(event.globalPos(), "Копировать номер тендера", view)
            return True
        return super().helpEvent(event, view, option, index)


class DeadlineDelegate(NumberDelegate):
    edit_requested = Signal(int)

    def copy_text(self, index):
        return index.data(DEADLINE_COPY_ROLE) or ""

    def editorEvent(self, event, model, option, index):
        if (event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                             QEvent.Type.MouseButtonDblClick)
                and event.button() == Qt.MouseButton.LeftButton):
            if self.copy_rect(option.rect).contains(event.position().toPoint()):
                return super().editorEvent(event, model, option, index)
            if event.type() == QEvent.Type.MouseButtonRelease:
                identifier = index.data(Qt.ItemDataRole.UserRole)
                # Saving rebuilds table items; finish Qt's mouse event before opening the dialog.
                QTimer.singleShot(0, self, lambda: self.edit_requested.emit(identifier))
            return True
        return super().editorEvent(event, model, option, index)

    def helpEvent(self, event, view, option, index):
        text = ("Копировать дату и время без МСК" if self.copy_text(index) else "Срок не указан") \
            if self.copy_rect(option.rect).contains(event.pos()) else (
                f"{index.data(Qt.ItemDataRole.ToolTipRole)}\nНажмите для изменения даты и времени")
        QToolTip.showText(event.globalPos(), text, view)
        return True


def latest_comment(text):
    headers = list(COMMENT_HEADER.finditer(text))
    return (text[headers[-1].end():] if headers else text).strip()


class RegistryItem(QTableWidgetItem):
    def __init__(self, text, key):
        super().__init__(text)
        self.key = key

    def __lt__(self, other):
        return self.key < other.key


class TenderRegistry(QWidget):
    tender_requested = Signal(int)
    edit_requested = Signal(int, str)
    new_requested = Signal()
    import_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tenders = []
        self.unseen_ids = set()
        layout = QVBoxLayout(self)
        heading = QLabel("Реестр тендеров")
        heading.setObjectName("breadcrumb")
        header = QHBoxLayout()
        header.addWidget(heading, 1)
        self.new_button = QPushButton("+ Тендер")
        self.new_button.clicked.connect(self.new_requested.emit)
        self.import_button = QPushButton("Из 1С")
        self.import_button.clicked.connect(self.import_requested.emit)
        header.addWidget(self.new_button)
        header.addWidget(self.import_button)
        layout.addLayout(header)
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Номер, заказчик, предмет, действие, комментарии…")
        self.search.setAccessibleName("Поиск в реестре тендеров")
        self.search.setClearButtonEnabled(True)
        self.status_filter = QComboBox()
        self.status_filter.setAccessibleName("Статус в реестре")
        self.status_filter.addItem("В работе и к подаче", "active")
        self.status_filter.addItem("Все тендеры", "all")
        for status in STATUSES:
            self.status_filter.addItem(status, status)
        self.count = QLabel()
        controls.addWidget(self.search, 1)
        controls.addWidget(self.status_filter)
        controls.addWidget(self.count)
        layout.addLayout(controls)
        self.table = QTableWidget(0, len(REGISTRY_FIELDS))
        self.table.setItemDelegateForColumn(0, NumberDelegate(self.table))
        deadline_delegate = DeadlineDelegate(self.table)
        deadline_delegate.edit_requested.connect(lambda identifier: self.edit_requested.emit(identifier, "deadline"))
        self.table.setItemDelegateForColumn(3, deadline_delegate)
        self.table.setHorizontalHeaderLabels([
            "Номер", "Заказчик", "Предмет", "Срок подачи · МСК", "Статус",
            "Площадка", "Последний комментарий",
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet("""
            QTableWidget { background: #1c2027; alternate-background-color: #202630;
                color: #ecf0f6; gridline-color: #303947; selection-background-color: #654585;
                selection-color: #ffffff; }
            QTableWidget::item { padding: 4px; }
            QTableWidget::item:selected { background: #654585; color: #ffffff; }
            QHeaderView::section { background: #232a34; color: #ecf0f6;
                padding: 7px; border: 1px solid #303947; }
        """)
        self.table.verticalHeader().show()
        self.table.verticalHeader().setMinimumWidth(36)
        self.table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table.verticalHeader().setDefaultSectionSize(self.table.fontMetrics().lineSpacing() * 2 + 4)
        self.table.horizontalHeader().setMinimumSectionSize(40)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeaderItem(3).setText("Срок подачи\nМСК")
        self.table.horizontalHeader().sectionResized.connect(self.remember_widths)
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self.fit_columns)
        self.table.viewport().installEventFilter(self)
        self.table.setSortingEnabled(True)
        self.table.sortItems(3, Qt.SortOrder.AscendingOrder)
        self.table.cellActivated.connect(self.activate_row)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.edit_cell_at)
        self.copy_number_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        self.copy_number_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.copy_number_shortcut.activated.connect(self.copy_number)
        layout.addWidget(self.table, 1)
        self.empty = QLabel("Нет тендеров по выбранным условиям")
        layout.addWidget(self.empty)
        hint = QLabel("Клик по сроку — правка · Значки — копировать номер или срок · Двойной клик / Enter — карточка · Правая кнопка — правка ячейки, кроме номера")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.search.textChanged.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def eventFilter(self, watched, event):
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self.fit_timer.start(0)
        return super().eventFilter(watched, event)

    def remember_widths(self, column, *_):
        if not getattr(self, "_fitting_columns", False):
            self._manual_widths = [self.table.columnWidth(i) for i in range(7)]
            self._manual_column = column

    def fit_columns(self):
        """Fit every field into the viewport, leaving long text in tooltips."""
        table = self.table
        line = table.fontMetrics().lineSpacing()
        row_height = line * 2 + 4
        if table.rowCount():
            row_height = max(line + 6, min(row_height, table.viewport().height() // table.rowCount()))
        table.verticalHeader().setDefaultSectionSize(row_height)
        available = table.viewport().width()
        if available <= 0 or (available == getattr(self, "_fitted_width", None)
                              and table.horizontalHeader().length() <= available):
            return
        self._fitted_width = available
        metrics = table.fontMetrics()
        number = max((metrics.horizontalAdvance(table.item(row, 0).text()) + 60
                      for row in range(table.rowCount())), default=130)
        widths = [min(number, 220), 80, 95,
                  metrics.horizontalAdvance("00.00.0000") + 56,
                  metrics.horizontalAdvance("Не участвуем") + 16, 75, 110]
        widths = getattr(self, "_manual_widths", widths).copy()
        weights = (0, 2, 3, 0, 0, 1, 4)
        extra = available - sum(widths)
        if extra >= 0:
            widths = [width + extra * weight // sum(weights)
                      for width, weight in zip(widths, weights)]
        else:
            locked = getattr(self, "_manual_column", None)
            flexible = [i for i in range(7) if i != locked]
            capacity = sum(max(0, widths[i] - 40) for i in flexible)
            if capacity >= -extra:
                for i in flexible:
                    widths[i] -= (-extra) * max(0, widths[i] - 40) // capacity
            else:
                widths = [int(width * available / sum(widths)) for width in widths]
        widths[-1] += available - sum(widths)
        self._fitting_columns = True
        try:
            for column, width in enumerate(widths):
                table.setColumnWidth(column, width)
        finally:
            self._fitting_columns = False

    def activate_row(self, row, _column):
        if _column == 3:
            return
        item = self.table.item(row, 0)
        if item is not None:
            self.tender_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def edit_cell_at(self, position):
        item = self.table.itemAt(position)
        if item is None:
            return
        self.table.setCurrentCell(item.row(), item.column())
        if item.column() != 0:
            self.edit_requested.emit(item.data(Qt.ItemDataRole.UserRole), REGISTRY_FIELDS[item.column()])

    def copy_number(self):
        item = self.table.item(self.table.currentRow(), 0)
        if item is not None:
            QApplication.clipboard().setText(item.text())

    def set_tenders(self, tenders, unseen_ids=None):
        self.tenders = tenders
        if unseen_ids is not None:
            self.unseen_ids = set(unseen_ids)
        self.refresh()

    def refresh(self, *_):
        table = self.table
        old_row = table.currentRow()
        selected = table.item(old_row, 0)
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        vertical = table.verticalScrollBar().value()
        horizontal = table.horizontalScrollBar().value()
        query = self.search.text().strip().casefold()
        status = self.status_filter.currentData()
        visible = [t for t in self.tenders
                   if (status == "all" or (status == "active" and t.status in ACTIVE)
                       or t.status == status)
                   and (not query or query in "\n".join(
                       (t.number, t.customer, t.title, t.action, t.comment)).casefold())]
        signature = repr((visible, self.unseen_ids, [overdue(t) for t in visible]))
        if signature == getattr(self, "_rows_signature", None):
            return
        self._rows_signature = signature
        table.setSortingEnabled(False)
        table.setRowCount(len(visible))
        for row, tender in enumerate(visible):
            comment = latest_comment(tender.comment)
            values = (tender.number, tender.customer, tender.title, deadline_label(tender),
                      tender.status, tender.platform, comment)
            day, clock = deadline(tender)
            date_key = (day is None, day.toordinal() if day else 0,
                        clock.isoformat() if clock else "24:00", tender.id or 0)
            for column, value in enumerate(values):
                display = (value.replace(" · ", "\n").replace(" МСК", "")
                           .replace("время не указано", "Без времени")) if column == 3 else value
                item = RegistryItem(display, date_key if column == 3 else value.casefold())
                item.setToolTip(value)
                item.setData(Qt.ItemDataRole.UserRole, tender.id)
                item.setData(UNSEEN_ROLE, tender.id in self.unseen_ids)
                if column == 3:
                    item.setData(DEADLINE_COPY_ROLE, deadline_copy_text(tender))
                if tender.id in self.unseen_ids:
                    item.setBackground(QColor("#23394d"))
                    item.setToolTip(f"Новый · {value}")
                if column in (0, 1):
                    item.setForeground(QColor(ENTITY_COLORS['tender' if column == 0 else 'company']))
                if column == 4 and tender.status in STATUS_COLORS:
                    foreground, background = STATUS_COLORS[tender.status]
                    item.setForeground(QColor(foreground))
                    item.setBackground(QColor(background))
                if column == 3 and tender.status in ACTIVE and overdue(tender):
                    item.setForeground(QColor("#f19b9f"))
                table.setItem(row, column, item)
        self.fit_timer.start(0)
        table.setSortingEnabled(True)
        target = next((r for r in range(table.rowCount())
                       if table.item(r, 0).data(Qt.ItemDataRole.UserRole) == selected_id),
                      min(old_row, table.rowCount() - 1))
        if target >= 0:
            table.setCurrentCell(target, 0)
        table.verticalScrollBar().setValue(vertical)
        table.horizontalScrollBar().setValue(horizontal)
        self.count.setText(f"Найдено: {len(visible)}")
        self.empty.setVisible(not visible)
