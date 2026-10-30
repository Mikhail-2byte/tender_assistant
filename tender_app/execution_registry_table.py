"""Nested registry tables and editors that retain failed drafts."""
from datetime import datetime
import sys

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAccessible, QColor, QFontMetrics, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QAbstractItemView, QAccessibleWidget, QApplication, QComboBox, QHeaderView,
    QLineEdit, QPlainTextEdit, QStyledItemDelegate, QStyleOptionViewItem,
    QTreeWidget, QTreeWidgetItem, QStyle,
)

from tender_app.execution_models import STAGES
from tender_app.theme import ENTITY_COLORS

CELL = Qt.ItemDataRole.UserRole
LEVEL_COLORS = {
    "contract": dict(row="#263f5c", alternate="#263f5c", header="#1c3048",
                     accent="#7fabdf", border="#466a94", text="#eef5ff", gap=12, height=66),
    "specification": dict(row="#1d3d38", alternate="#1d3d38", header="#17312f",
                          accent="#6bc7b2", border="#38695f", text="#e2f5ef", gap=8, height=62),
    "position": dict(row="#1b2028", alternate="#242b35", header="#2a323e",
                     accent="#73839a", border="#465265", text="#ecf0f6", gap=0, height=52),
}
BLOCK_BACKGROUND = "#111720"


class RegistryItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        left = self.data(column, CELL) or {}
        right = other.data(column, CELL) or {}
        a, b = left.get("sort", self.text(column).casefold()), right.get("sort", other.text(column).casefold())
        # Missing values remain last in either sort direction.
        if (a is None) != (b is None):
            ascending = self.treeWidget().header().sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
            return (a is not None) if ascending else (a is None)
        return (a if a is not None else "") < (b if b is not None else "")


class RegistryDelegate(QStyledItemDelegate):
    def __init__(self, registry, parent):
        super().__init__(parent)
        self.registry = registry

    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, option.widget)
        painter.save()
        table = self.parent()
        colors = LEVEL_COLORS[table.level]
        gap = colors["gap"]
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        body = option.rect.adjusted(0, gap, 0, 0)
        painter.fillRect(option.rect, QColor(BLOCK_BACKGROUND))
        background = "#654585" if selected else colors["alternate"] if index.row() % 2 else colors["row"]
        painter.fillRect(body, QColor(background))
        if table.level != "position":
            painter.setPen(QColor(colors["border"]))
            painter.drawLine(body.topLeft(), body.topRight())
            painter.drawLine(body.bottomLeft(), body.bottomRight())
            if index.column() == 0:
                painter.fillRect(body.left(), body.top(), 4, body.height(), QColor(colors["accent"]))
        rect = body.adjusted(10 if index.column() == 0 and table.level != "position" else 6, 2, -6, -2)
        painter.setClipRect(rect)
        font = styled.font
        font.setBold((table.level == "contract" and index.column() in (0, 1))
                     or (table.level == "specification" and index.column() == 0))
        painter.setFont(font)
        metrics = QFontMetrics(font)
        painter.setPen(QColor("#ffffff" if selected else colors["text"]))
        meta = index.data(CELL) or {}
        role = ({1: 'company', 2: 'spec'}.get(index.column()) if table.level == 'contract' else
                {0: 'spec', 2: 'tender'}.get(index.column()) if table.level == 'specification' else
                {6: 'company'}.get(index.column()))
        if role:
            painter.setPen(QColor(ENTITY_COLORS[role]))
        if meta.get("overdue"):
            painter.setPen(QColor("#ffaaaa"))
        lines = text.splitlines() or [""]
        if len(lines) == 1 and metrics.horizontalAdvance(lines[0]) > rect.width():
            words = lines[0].split(" ")
            first = ""
            while words and metrics.horizontalAdvance((first + " " + words[0]).strip()) <= rect.width():
                first = (first + " " + words.pop(0)).strip()
            if first and words:
                lines = [first, " ".join(words)]
        shown = [metrics.elidedText(line, Qt.TextElideMode.ElideRight, max(1, rect.width())) for line in lines[:2]]
        if len(lines) > 2:
            shown[-1] = metrics.elidedText(shown[-1] + " …", Qt.TextElideMode.ElideRight, max(1, rect.width()))
        painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "\n".join(shown))
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(40, self.parent().row_height)

    def createEditor(self, parent, option, index):
        meta = index.data(CELL) or {}
        if not meta.get("editable") or not self.registry.finish_edit():
            return None
        if meta["field"] == "comment":
            editor = QPlainTextEdit(parent)
        elif meta["field"] == "stage":
            editor = QComboBox(parent)
            editor.addItems(STAGES)
        else:
            editor = QLineEdit(parent)
        editor.registry_meta = dict(meta)
        editor.registry_delegate = self
        self.registry.active_editor = editor
        return editor

    def setEditorData(self, editor, index):
        value = editor.registry_meta["raw"]
        text = str(value) if value is not None else ""
        if isinstance(editor, QPlainTextEdit):
            editor.setPlainText("")
            editor.setPlaceholderText("Новый комментарий…\nAlt+Enter — новая строка; Enter — добавить в историю")
        elif isinstance(editor, QComboBox):
            editor.setCurrentText(text)
        else:
            if editor.registry_meta["field"] in ("date", "due") and text:
                text = datetime.strptime(text, "%Y-%m-%d").strftime("%d.%m.%Y")
            editor.setText(text)
            editor.selectAll()

    def setModelData(self, editor, model, index):
        pass  # Persistence happens before closing, so failures cannot discard text.

    def updateEditorGeometry(self, editor, option, index):
        rect = option.rect.adjusted(0, 0, -1, -1)
        if isinstance(editor, QPlainTextEdit):
            rect.setHeight(min(120, max(52, editor.parentWidget().height())))
            if rect.bottom() > editor.parentWidget().height():
                rect.moveBottom(editor.parentWidget().height())
        editor.setGeometry(rect)

    def eventFilter(self, editor, event):
        if self.registry.active_editor is not editor:
            return super().eventFilter(editor, event)
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.registry.cancel_edit()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if isinstance(editor, QPlainTextEdit) and event.modifiers() & (
                        Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
                    editor.insertPlainText("\n")
                    return True
                self.registry.finish_edit()
                return True
        if event.type() == QEvent.Type.FocusOut:
            if event.reason() == Qt.FocusReason.PopupFocusReason:
                return False
            # Page changes and explicit Enter decide commit. A failed draft stays open.
            if not self.registry.finish_edit():
                QTimer.singleShot(0, editor, editor.setFocus)
            return True
        return super().eventFilter(editor, event)

    def destroyEditor(self, editor, index):
        if self.registry.active_editor is editor:
            self.registry.active_editor = None
        super().destroyEditor(editor, index)


class RegistryTable(QTreeWidget):
    height_changed = Signal()

    def __init__(self, registry, level, headers, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.level = level
        self.retired = False
        self.inner = level != "contract"
        self.row_height = LEVEL_COLORS[level]["height"]
        self.setColumnCount(len(headers))
        self.setHeaderLabels(headers)
        self.setAccessibleName({"contract": "Договоры реестра исполнения", "specification": "Спецификации договора",
                                "position": "Позиции спецификации"}[level])
        self.setItemDelegate(RegistryDelegate(registry, self))
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(False)
        self.setWordWrap(False)
        self.setRootIsDecorated(level != "position")
        self.setIndentation(18)
        self.setExpandsOnDoubleClick(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        if self.inner:
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.header().setMinimumSectionSize(48)
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.header().setStretchLastSection(True)
        self.header().setSectionsClickable(True)
        self.header().sectionClicked.connect(self.sort_clicked)
        self.header().sectionResized.connect(lambda column, *_: registry.remember_widths(self, column))
        self.itemExpanded.connect(self.expansion_changed)
        self.itemCollapsed.connect(self.expansion_changed)
        self.itemDoubleClicked.connect(registry.activate_cell)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda point: registry.show_menu(self, point))
        self.setMinimumWidth(0)
        colors = LEVEL_COLORS[level]
        self.setStyleSheet("""
            QTreeWidget { background: %(background)s; alternate-background-color: %(alternate)s;
                color: %(text)s; border: 1px solid %(border)s; outline: none; }
            QTreeWidget::item { border-bottom: 1px solid #343d4b; padding: 2px; }
            QTreeWidget::item:selected { background: #654585; color: white; }
            QHeaderView::section { background: %(header)s; color: %(heading)s;
                border: 0; border-right: 1px solid %(border)s;
                border-bottom: 2px solid %(accent)s; padding: 6px; }
            QLineEdit, QPlainTextEdit, QComboBox { background: #132638; color: white;
                border: 2px solid #7eafff; padding: 3px; }
        """ % dict(colors, background=BLOCK_BACKGROUND if self.level != "position" else colors["row"],
                   heading=colors["accent"] if self.level != "position" else colors["text"]))
        self.header().geometriesChanged.connect(self.schedule_height)
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self.fit_height)

    def sort_clicked(self, column):
        if not self.registry.finish_edit():
            return
        saved = self.registry.sorting.get(self.level, [column, 1])
        direction = 1 - saved[1] if saved[0] == column else 0
        self.registry.sorting[self.level] = [column, direction]
        for table in self.registry.tables:
            if table.level == self.level:
                table.apply_sort()

    def apply_sort(self):
        saved = self.registry.sorting.get(self.level)
        if saved and 0 <= saved[0] < self.columnCount():
            order = Qt.SortOrder.AscendingOrder if saved[1] == 0 else Qt.SortOrder.DescendingOrder
            self.sortItems(saved[0], order)
            self.header().setSortIndicator(saved[0], order)
            self.header().setSortIndicatorShown(True)

    def expansion_changed(self, item):
        meta = item.data(0, CELL) or {}
        key = meta.get("expand_key")
        if key and not self.registry.building:
            if item.isExpanded():
                self.registry.expanded.add(key)
                self.registry.populate(item, self)
            else:
                self.registry.expanded.discard(key)
        self.schedule_height()

    def schedule_height(self):
        if not self.retired and hasattr(self, "_height_timer"):
            self._height_timer.start(0)

    def retire(self):
        # clear() deletes items immediately, but their widgets live until the
        # next event-loop turn. They must stop referring to the deleted rows.
        self.retired = True
        self._height_timer.stop()
        self.height_changed.disconnect()

    def fit_height(self):
        if not self.inner:
            return
        height = self.header().height() + 2 * self.frameWidth()
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            height += self.row_height
            if item.isExpanded() and item.childCount():
                height += item.child(0).sizeHint(0).height()
        if self.horizontalScrollBar().maximum() > 0:
            height += self.horizontalScrollBar().sizeHint().height()
        if self.height() != height:
            self.setFixedHeight(height)
            self.height_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.retired:
            return
        if self.level not in self.registry.widths and not getattr(self.registry, "_setting_widths", False):
            self.registry.set_widths(self)
        self.schedule_height()

    def drawBranches(self, painter, rect, index):
        colors = LEVEL_COLORS[self.level]
        painter.fillRect(rect, QColor(BLOCK_BACKGROUND))
        body = rect.adjusted(0, colors["gap"], 0, 0)
        painter.fillRect(body, QColor(colors["row"]))
        super().drawBranches(painter, rect, index)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            selected = sorted(self.selectedIndexes(), key=lambda i: (i.row(), i.column()))
            rows = {}
            for index in selected:
                rows.setdefault(index.row(), []).append(str(index.data() or "").replace("\n", " "))
            if rows:
                QApplication.clipboard().setText("\n".join("\t".join(values) for values in rows.values()))
            return
        super().keyPressEvent(event)


def _accessible_registry_table(class_name, widget):
    if isinstance(widget, RegistryTable):
        # Qt's Windows table provider can recurse indefinitely through these
        # nested views. Expose their real widget/editor hierarchy instead of
        # its virtual table cells; leave all other application controls alone.
        interface = QAccessibleWidget(widget, QAccessible.Role.Grouping)
        widget._accessible_interface = interface
        return interface


if sys.platform == "win32":
    QAccessible.installFactory(_accessible_registry_table)
