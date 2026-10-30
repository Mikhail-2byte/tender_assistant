from tender_app.execution_cards import ExecutionCards
from tender_app.execution_groups import counted, contract_key
"""Contract navigation and native execution cards; no database writes here."""

from math import ceil
from collections import OrderedDict
import re

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtGui import (
    QColor,
    QPalette,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QAbstractTextDocumentLayout,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyledItemDelegate,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QStyle,
    QStyleOptionViewItem,
    QBoxLayout,
    QTabBar,
    QSizePolicy,
)

from tender_app.execution_demo import DEMO_TODAY, date_label, is_overdue, delivery_due
from tender_app.widgets import WrappingLabel
from tender_app.window import STATUS_COLORS
from tender_app.execution_positions import application_numbers

from tender_app.theme import ENTITY_COLORS

ROLE = Qt.ItemDataRole.UserRole


class NavigationRows(QStyledItemDelegate):
    """Tender-list spacing, selection and status strips, with tree indentation."""

    def colors(self, status):
        palette = {
            "В исполнении": "В работе",
            "Без договора": "Интересный",
            "Завершён": "Победа",
            "Архив": "Не участвуем",
            "Тендер выигран": "Победа",
            "Договор на согласовании": "Интересный",
            "Спецификация подписана": "В работе",
            "Размещено у поставщика": "В работе",
            "В производстве": "Податься",
            "Готово к отгрузке": "Подался",
            "В пути": "Подался",
            "Частично отгружено": "Подался",
            "Разные этапы": "Переторжка",
            "Отгружено заказчику": "Победа",
            "Завершено": "Победа",
            "Приостановлено": "Не участвуем",
        }
        return STATUS_COLORS.get(palette.get(status), ("#a3adbd", "#191d24"))

    def document(self, option, width, index):
        document = QTextDocument()
        document.setDefaultFont(option.font)
        document.setDocumentMargin(0)
        document.setPlainText(index.data(ROLE + 3) or index.data(Qt.ItemDataRole.DisplayRole) or "")

        def format_block(number, color=None, bold=False):
            block = document.findBlockByNumber(number)
            if not block.isValid():
                return
            cursor = QTextCursor(block)
            cursor.movePosition(
                QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
            )
            style = QTextCharFormat()
            if color:
                style.setForeground(QColor(color))
            style.setFontWeight(600 if bold else 400)
            cursor.setCharFormat(style)

        format_block(0, ENTITY_COLORS["spec" if index.parent().isValid() else "company"], True)
        format_block(1, "#a3adbd" if not index.parent().isValid() else None)
        format_block(3, "#a3adbd")
        if index.parent().isValid():
            format_block(1, "#a3adbd")
            format_block(2, "#ecf0f6", False)
        status = index.data(ROLE + 1) or ""
        cursor = document.find(status) if status else QTextCursor()
        if not cursor.isNull():
            color, background = self.colors(status)
            style = QTextCharFormat()
            style.setForeground(QColor(color))
            style.setBackground(QColor(background))
            style.setFontWeight(600)
            cursor.setCharFormat(style)
        warning = document.find("Просрочка")
        if not warning.isNull():
            style = QTextCharFormat()
            style.setForeground(QColor("#f19b9f"))
            style.setFontWeight(600)
            warning.setCharFormat(style)
        document.setTextWidth(max(1, width - 30))
        return document

    def sizeHint(self, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        depth = 2 if index.parent().isValid() else 1
        width = max(
            60,
            self.parent().viewport().width() - self.parent().indentation() * depth - 2,
        )
        key = (width, option.font.key(), index.data(ROLE + 3) or index.data(Qt.ItemDataRole.DisplayRole),
               index.data(ROLE + 1))
        # Qt repeatedly asks for row sizes during resize/reparenting. Cache only
        # bounded scalar heights, rather than retaining rich text documents.
        if not hasattr(self, "_heights"):
            self._heights = OrderedDict()
        height = self._heights.get(key)
        if height is None:
            height = ceil(self.document(option, width, index).size().height()) + 24
            self._heights[key] = height
            if len(self._heights) > 2048:
                self._heights.popitem(last=False)
        else:
            self._heights.move_to_end(key)
        return QSize(width, height)

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect.adjusted(0, 2, -2, -2)
        painter.save()
        painter.fillRect(option.rect, QColor("#191d24"))
        background = "#25314a" if selected else "#252d3a" if hovered else "#1c2027"
        painter.fillRect(rect, QColor(background))
        painter.setPen(QColor("#4e6692" if selected else "#343d4b"))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        color, _ = self.colors(index.data(ROLE + 1))
        if selected:
            painter.fillRect(
                QRect(rect.left(), rect.top() + 8, 3, max(1, rect.height() - 16)),
                QColor("#8ba9ff"),
            )
        document = self.document(option, rect.width(), index)
        painter.setClipRect(rect.adjusted(18, 10, -12, -10))
        painter.translate(rect.left() + 18, rect.top() + 10)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(
            QPalette.ColorRole.Text, QColor("#ecf0f6" if selected else "#ecf0f6")
        )
        document.documentLayout().draw(painter, context)
        painter.restore()


class DetailGrid(QWidget):
    def __init__(self, values):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.fields = []
        self.columns = 0
        for caption, value in values:
            box = QWidget()
            column = QVBoxLayout(box)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(3)
            label = QLabel(caption)
            label.setObjectName("fieldLabel")
            label.setWordWrap(True)
            content = WrappingLabel(
                str(value) if value not in (None, "") else "Не указано"
            )
            content.setWordWrap(True)
            content.setTextFormat(Qt.TextFormat.PlainText)
            content.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            column.addWidget(label)
            column.addWidget(content)
            self.fields.append(box)
        self.arrange(2)

    def arrange(self, count):
        if count == self.columns:
            return
        self.columns = count
        for index, box in enumerate(self.fields):
            self.grid.addWidget(
                box, index // count, index % count, Qt.AlignmentFlag.AlignTop
            )
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, int(count == 2))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange(1 if event.size().width() < 540 else 2)


class SpecificationHeader(QWidget):
    def __init__(self, information, contacts):
        super().__init__()
        self.information = information
        self.contacts = contacts
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.columns.addWidget(information)
        self.columns.addWidget(contacts)
        self.side_by_side = None
        self.arrange(True)

    def minimumSizeHint(self):
        return QSize(
            self.information.minimumSizeHint().width(),
            self.columns.minimumSize().height(),
        )

    def arrange(self, wide):
        if wide == self.side_by_side:
            return
        self.side_by_side = wide
        self.columns.setDirection(
            QBoxLayout.Direction.LeftToRight
            if wide
            else QBoxLayout.Direction.TopToBottom
        )
        self.columns.setSpacing(20 if wide else 10)
        self.columns.setStretch(0, 1 if wide else 0)
        self.columns.setStretch(1, 1 if wide else 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange(event.size().width() >= 640)


class ExecutionPanel(ExecutionCards, QWidget):
    tender_requested = Signal(int)
    edit_requested = Signal(int)
    new_requested = Signal()
    create_requested = Signal(object, str)
    resource_requested = Signal(int, str)
    contacts_requested = Signal(int)
    archive_requested = Signal(int, bool)
    comment_requested = Signal(int, int)
    position_edit_requested = Signal(int, int, str)
    shipped_requested = Signal(int, int)
    shipment_complete_requested = Signal(int, int)
    spec_comment_requested = Signal(int, str)
    spec_comment_delete_requested = Signal(int, int)
    draft_changed = Signal()

    def __init__(self, tenders, specifications, today=DEMO_TODAY, read_only=True):
        super().__init__()
        self.tenders = tenders
        self.specifications = specifications
        self.today = today
        self.read_only = read_only
        self.groups = {}
        self.current_key = None
        self.current_specification_id = None
        self.expanded_sections = set()
        self.expanded_keys = set()
        self.recent_keys = []
        self._refreshing_navigation = False
        self._pending_navigation_scroll = None
        self._navigation_scroll_timer = QTimer(self)
        self._navigation_scroll_timer.setSingleShot(True)
        self._navigation_scroll_timer.timeout.connect(self.apply_navigation_scroll)
        self.rendered_signature = None
        self.spec_links = {}
        self.comment_drafts = {}
        self.position_tables = []
        self.position_column_widths = []
        self.only_remaining_positions = False
        self.comment_input_height = 64
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter)
        left = QWidget()
        left.setMinimumWidth(260)
        left.setObjectName("registryPanel")
        navigation = QVBoxLayout(left)
        navigation.setContentsMargins(12, 12, 10, 8)
        navigation.setSpacing(8)
        heading = QLabel("Договоры")
        heading.setObjectName("heading")
        self.navigation_header = QHBoxLayout()
        self.navigation_header.addWidget(heading, 1)
        navigation.addLayout(self.navigation_header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Заказчик, договор, тендер, спецификация…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Поиск исполнения")
        # The common search lives in the top bar in the full app. It stays visible
        # here for the stand-alone execution preview, whose shell has no global input.
        navigation.addWidget(self.search)
        self.state = QComboBox()
        self.state.addItems(["В работе", "Архив", "Все"])
        self.state.setAccessibleName("Состояние исполнения")
        self.filters_toggle = QToolButton()
        self.filters_toggle.setText("Фильтры")
        self.filters_toggle.setCheckable(True)
        self.filters_toggle.setAccessibleName("Показать дополнительные фильтры исполнения")
        from tender_app.ui_icons import icon
        self.filters_toggle.setIcon(icon("filter"))
        self.filters_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        filter_row = QHBoxLayout()
        filter_row.addWidget(self.state, 1)
        filter_row.addWidget(self.filters_toggle)
        navigation.addLayout(filter_row)
        self.filters_panel = QWidget()
        filters = QVBoxLayout(self.filters_panel)
        filters.setContentsMargins(0, 0, 0, 4)
        filters.setSpacing(6)
        self.stage = QComboBox()
        self.stage.addItem("Все этапы")
        self.stage.setAccessibleName("Этап спецификации или позиции")
        filters.addWidget(self.stage)
        controls = QHBoxLayout()
        self.overdue = QCheckBox("Просроченные")
        reset = QPushButton("Сбросить")
        reset.clicked.connect(self.reset_filters)
        controls.addWidget(self.overdue)
        controls.addWidget(reset)
        filters.addLayout(controls)
        navigation.addWidget(self.filters_panel)
        self.filters_panel.hide()
        self.filters_toggle.toggled.connect(self.filters_panel.setVisible)
        self.new_button = QPushButton()
        self.new_button.setIcon(icon("plus"))
        self.new_button.setAccessibleName("Добавить спецификацию")
        self.new_button.setToolTip("Добавить спецификацию")
        self.new_button.setFixedWidth(34)
        self.new_button.setVisible(not read_only)
        self.new_button.clicked.connect(self.new_requested)
        self.navigation_header.addWidget(self.new_button)
        self.navigation_modes = QTabBar()
        self.navigation_modes.setObjectName("navigationModes")
        self.navigation_modes.setAccessibleName("Режим списка договоров")
        self.navigation_modes.setExpanding(True)
        for text in ("Все", "Внимание", "Недавние"):
            self.navigation_modes.addTab(text)
        self.navigation_modes.setTabToolTip(1, "Незавершённые: просрочка, указанный риск, нет срока или договора")
        self.navigation_modes.setTabToolTip(2, "Последние открытые договоры и спецификации на этом компьютере")
        navigation.addWidget(self.navigation_modes)
        self.navigation_modes.currentChanged.connect(self.refresh)
        self.count = QLabel()
        self.count.setWordWrap(True)
        self.count.setObjectName("muted")
        self.tree = QTreeWidget()
        self.tree.setObjectName("executionNavigation")
        self.tree.setStyleSheet("""
            QTreeWidget#executionNavigation { background: #191d24; border: 1px solid #455166; outline: none; }
        """)
        self.tree.setMouseTracking(True)
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(1)
        self.tree.setWordWrap(True)
        self.tree.setVerticalScrollMode(QTreeWidget.ScrollMode.ScrollPerPixel)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.setItemDelegate(NavigationRows(self.tree))
        self.tree.setAccessibleName("Договоры и спецификации")
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().sectionResized.connect(lambda *_: self.tree.doItemsLayout())
        self.tree.currentItemChanged.connect(self.choose_item)
        navigation.addWidget(self.tree, 1)
        navigation.addWidget(self.count)
        self.splitter.addWidget(left)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumWidth(400)
        self.card = QWidget()
        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(16, 12, 16, 16)
        self.card_layout.setSpacing(14)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.card)
        self.splitter.addWidget(self.scroll)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([300, 1000])
        self.search.textChanged.connect(self.refresh)
        self.state.currentTextChanged.connect(self.refresh)
        self.stage.currentTextChanged.connect(self.refresh)
        self.overdue.toggled.connect(self.refresh)
        self.refresh()

    def reset_filters(self):
        self.navigation_modes.setCurrentIndex(0)
        self.search.clear()
        self.state.setCurrentIndex(0)
        self.stage.setCurrentIndex(0)
        self.overdue.setChecked(False)

    def matches(self, tender, spec):
        query = self.search.text().strip().casefold()
        searchable = [
            tender.customer,
            tender.number,
            tender.title,
            spec["number"],
            spec["title"],
            spec["contract"],
        ]
        for position in spec["positions"]:
            searchable.extend(
                [position["name"], position["supplier"], position["supplier_document"],
                 position.get("working_note", "")]
            )
        return (
            query in "\n".join(searchable).casefold()
            and (
                self.state.currentIndex() == 2
                or spec["archived"] == (self.state.currentIndex() == 1)
            )
            and (
                not self.stage.currentIndex()
                or self.stage.currentText()
                in [spec["stage"]] + [p["stage"] for p in spec["positions"]]
            )
            and (not self.overdue.isChecked() or is_overdue(spec, self.today))
        )

    def needs_attention(self, spec):
        return not spec["archived"] and not spec["completed"] and (
            is_overdue(spec, self.today) or bool(spec["risk"].strip())
            or not (delivery_due(spec)) or not spec["contract"].strip()
        )

    def remember_visit(self, key):
        if key and not self._refreshing_navigation:
            self.recent_keys = [key] + [k for k in self.recent_keys if k != key][:19]

    def apply_navigation_scroll(self):
        if self.tree.isVisible() and self._pending_navigation_scroll is not None:
            self.tree.verticalScrollBar().setValue(self._pending_navigation_scroll)
            self._pending_navigation_scroll = None

    def showEvent(self, event):
        super().showEvent(event)
        if self._pending_navigation_scroll is not None:
            self._navigation_scroll_timer.start(0)

    def navigation_preferences(self):
        expanded = set(self.expanded_keys)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.isExpanded():
                expanded.add(item.data(0, ROLE))
            else:
                expanded.discard(item.data(0, ROLE))
        return {"expanded": [list(k) for k in sorted(expanded, key=repr)],
                "recent": [list(k) for k in self.recent_keys],
                "selected": list(self.current_key) if self.current_key else None,
                "scroll": (self._pending_navigation_scroll if self._pending_navigation_scroll is not None else self.tree.verticalScrollBar().value()),
                "mode": self.navigation_modes.currentIndex(),
                "state": self.state.currentIndex(), "stage": self.stage.currentText(),
                "overdue": self.overdue.isChecked()}

    def restore_navigation_preferences(self, values):
        if not isinstance(values, dict):
            return
        def keys(items):
            return [tuple(k) for k in items if isinstance(k, list) and len(k) in (2, 3)
                    and all(type(v) in (str, int) for v in k)] if isinstance(items, list) else []
        self.expanded_keys = set(keys(values.get("expanded", [])))
        self.recent_keys = keys(values.get("recent", []))[:20]
        selected = keys([values.get("selected")])
        self.current_key = selected[0] if selected else None
        self.navigation_modes.blockSignals(True)
        mode = values.get("mode", 0)
        self.navigation_modes.setCurrentIndex(mode if type(mode) is int and mode in (0, 1, 2) else 0)
        self.navigation_modes.blockSignals(False)
        for control in (self.state, self.stage, self.overdue):
            control.blockSignals(True)
        state = values.get("state", 0)
        self.state.setCurrentIndex(state if type(state) is int and state in (0, 1, 2) else 0)
        stage = values.get("stage", "Все этапы")
        if isinstance(stage, str):
            self.stage.setCurrentIndex(max(0, self.stage.findText(stage)))
        self.overdue.setChecked(values.get("overdue") is True)
        for control in (self.state, self.stage, self.overdue):
            control.blockSignals(False)
        position = values.get("scroll", 0)
        self._pending_navigation_scroll = max(0, position) if type(position) is int else 0
        self._restoring_navigation = True
        self._navigation_signature = None
        self.refresh()
        self._restoring_navigation = False

    def refresh(self):
        if not hasattr(self, "tree"):
            return
        signature = repr((self.tenders, self.specifications, self.today,
                          self.search.text(), self.state.currentIndex(),
                          self.stage.currentText(), self.overdue.isChecked(),
                          self.navigation_modes.currentIndex(), self.recent_keys))
        if signature == getattr(self, "_navigation_signature", None):
            return
        old_scroll = (self._pending_navigation_scroll if self._pending_navigation_scroll is not None
                      else self.tree.verticalScrollBar().value())
        visible_keys = {self.tree.topLevelItem(i).data(0, ROLE) for i in range(self.tree.topLevelItemCount())}
        if not getattr(self, "_restoring_navigation", False):
            self.expanded_keys.difference_update(visible_keys)
        expanded = {
            self.tree.topLevelItem(i).data(0, ROLE)
            for i in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(i).isExpanded()
        }
        if not getattr(self, "_restoring_navigation", False):
            self.expanded_keys.update(expanded)
        expanded = self.expanded_keys
        self._refreshing_navigation = True
        current_stage = self.stage.currentText()
        self.stage.blockSignals(True)
        self.stage.clear()
        self.stage.addItems(
            ["Все этапы"]
            + sorted(
                {s["stage"] for s in self.specifications}
                | {p["stage"] for s in self.specifications for p in s["positions"]}
            )
        )
        self.stage.setCurrentIndex(max(0, self.stage.findText(current_stage)))
        self.stage.blockSignals(False)
        tenders = {t.id: t for t in self.tenders}
        self.groups = {}
        for spec in self.specifications:
            tender = tenders[spec["tender_id"]]
            key = contract_key(tender, spec)
            group = self.groups.setdefault(
                key,
                dict(
                    customer=tender.customer,
                    contract=spec["contract"],
                    specs=[],
                    tenders={},
                ),
            )
            group["specs"].append(spec)
            group["tenders"][tender.id] = tender
        linked = {s["tender_id"] for s in self.specifications}
        for tender in self.tenders:
            if tender.status == "Победа" and tender.id not in linked:
                self.groups[("unassigned", tender.id)] = dict(
                    customer=tender.customer,
                    contract="",
                    specs=[],
                    tenders={tender.id: tender},
                )
        self.tree.blockSignals(True)
        self.tree.clear()
        self.visible_specifications = []
        self.without_specifications = 0
        self.visible_contracts = 0
        self.without_contract = 0
        selected = None

        def due(group):
            return min(
                (delivery_due(s) or "9999-12-31" for s in group["specs"]),
                default="9999-12-31",
            )

        mode = self.navigation_modes.currentIndex()
        def recent_rank(pair):
            key, group = pair
            keys = [key] + [("spec", spec["id"]) for spec in group["specs"]]
            return min((self.recent_keys.index(k) for k in keys if k in self.recent_keys), default=999)
        for key, group in sorted(self.groups.items(), key=lambda pair:
                (recent_rank(pair) if mode == 2 else 0, due(pair[1]), pair[1]["customer"].casefold(), repr(pair[0]))):
            if mode == 2 and recent_rank((key, group)) == 999:
                continue
            specs = [
                s for s in group["specs"] if self.matches(tenders[s["tender_id"]], s)
            ]
            if mode == 1:
                specs = [s for s in specs if self.needs_attention(s)]
            elif mode == 2 and key not in self.recent_keys:
                specs = [s for s in specs if ("spec", s["id"]) in self.recent_keys]
            if group["specs"] and not specs:
                continue
            if not group["specs"]:
                query = self.search.text().strip().casefold()
                tender = next(iter(group["tenders"].values()))
                if (
                    self.state.currentIndex() == 1
                    or self.stage.currentIndex()
                    or self.overdue.isChecked()
                    or query
                    not in (
                        tender.number + "\n" + tender.customer + "\n" + tender.title
                    ).casefold()
                ):
                    continue
                self.without_specifications += 1
            status = (
                "Без договора"
                if not group["contract"].strip()
                else (
                    "Архив"
                    if specs and all(s["archived"] for s in specs)
                    else (
                        "Завершён"
                        if group["specs"]
                        and all(s["completed"] for s in group["specs"])
                        else "В исполнении"
                    )
                )
            )
            marks = (
                f"{status} · Спецификаций: {len(specs)}"
                if specs
                else f"{status} · Без спецификаций"
            )
            if any(is_overdue(s, self.today) for s in specs):
                marks += " · Просрочка"
            if any(t.status != "Победа" for t in group["tenders"].values()):
                marks += " · Результат тендера изменён"
            parent = QTreeWidgetItem(
                [
                    f"{group['customer']}\n{group['contract'] or 'Договор не указан'}\n{marks}"
                ]
            )
            overdue_count = sum(is_overdue(spec, self.today) for spec in specs)
            short_contract = re.sub(r"^Договор\s*", "", group["contract"], flags=re.IGNORECASE)
            short_contract = re.sub(r"\s+от\s+\d{2}[./]\d{2}[./]\d{4}\s*$", "", short_contract)
            summary = counted(len(specs), "спецификация", "спецификации", "спецификаций") if specs else "Без спецификаций"
            summary += f" · Просрочка: {overdue_count}" if overdue_count else f" · {status}"
            parent.setData(0, ROLE + 3, f"{group['customer']}\n{short_contract or 'Договор не указан'}\n{summary}")
            parent.setData(0, ROLE, key)
            parent.setData(0, ROLE + 1, status)
            self.tree.addTopLevelItem(parent)
            if group["contract"].strip():
                self.visible_contracts += 1
            else:
                self.without_contract += 1
            parent.setExpanded(key in expanded or bool(self.search.text().strip()))
            if key == self.current_key:
                selected = parent
            for spec in specs:
                tender = tenders[spec["tender_id"]]
                number = re.sub(
                    r"\s+от\s+\d{2}[./]\d{2}[./]\d{4}\s*$",
                    "",
                    spec["number"],
                    flags=re.IGNORECASE,
                )
                spec_date = (
                    f"от {date_label(spec['date'])}"
                    if spec["date"]
                    else "· дата не указана"
                )
                child = QTreeWidgetItem(
                    [
                        f"Спецификация {number} {spec_date}\n"
                        f"Тендер № {tender.number}\n"
                        f"Приложения №: {application_numbers(spec)}\n"
                        f"Поставить до: {date_label(delivery_due(spec))}\n"
                        f"Статус: {spec['stage']}"
                        + (" · Архив" if spec["archived"] else "")
                        + (" · Просрочка" if is_overdue(spec, self.today) else "")
                    ]
                )
                child.setData(0, ROLE + 3,
                    f"{number} · {spec['title'] or 'Предмет не указан'}\n"
                    f"{spec['stage']}\n"
                    f"До {date_label(delivery_due(spec))}" + (" · Просрочка" if is_overdue(spec, self.today) else "")
                    + (" · Архив" if spec["archived"] else ""))
                child.setData(0, ROLE, ("spec", spec["id"]))
                child.setData(0, ROLE + 1, spec["stage"])
                child.setToolTip(0, child.text(0))
                parent.addChild(child)
                if child.data(0, ROLE) == self.current_key:
                    selected = child
                    parent.setExpanded(True)
                self.visible_specifications.append(spec)
            parent.setToolTip(0, parent.text(0))
        self.tree.setCurrentItem(selected or self.tree.topLevelItem(0))
        self.tree.blockSignals(False)
        self.count.setText(
            f"Договоров: {self.visible_contracts} · Спецификаций: {len(self.visible_specifications)}"
            + (
                f" · Без договора: {self.without_contract}"
                if self.without_contract
                else ""
            )
            + (
                f" · Без спецификаций: {self.without_specifications}"
                if self.without_specifications
                else ""
            )
            + (" · Ничего не найдено" if not self.tree.topLevelItemCount() else "")
        )
        full_count = self.count.text()
        self.count.setToolTip(full_count)
        self.count.setText(counted(self.visible_contracts, "договор", "договора", "договоров") + " · " + counted(len(self.visible_specifications), "спецификация", "спецификации", "спецификаций"))
        active_filters = int(self.stage.currentIndex() > 0) + int(self.overdue.isChecked())
        self.filters_toggle.setText("Фильтры" + (f" · {active_filters}" if active_filters else ""))
        self.choose_item(self.tree.currentItem())
        self._navigation_signature = signature
        self._refreshing_navigation = False
        self._pending_navigation_scroll = old_scroll
        self._navigation_scroll_timer.start(0)


    def choose_item(self, item, previous=None):
        key = item.data(0, ROLE) if item else None
        specs = ([s for s in self.specifications if s["id"] == key[1]]
                 if key and key[0] == "spec" else self.groups.get(key, {}).get("specs", []))
        tender_ids = {s["tender_id"] for s in specs}
        tender_ids.update(self.groups.get(key, {}).get("tenders", {}))
        signature = repr(
            (
                key,
                [t for t in self.tenders if t.id in tender_ids],
                specs,
                self.today,
                (self.state.currentIndex(), self.search.text(), self.stage.currentIndex(),
                 self.overdue.isChecked(), self.navigation_modes.currentIndex())
                if not key or key[0] != "spec" else None,
            )
        )
        if signature == self.rendered_signature:
            return
        old_scroll = (
            self.scroll.verticalScrollBar().value() if key == self.current_key else 0
        )
        if key != self.current_key:
            self.remember_visit(key)
        self.current_key = key
        self.rendered_signature = signature
        self.current_specification_id = key[1] if key and key[0] == "spec" else None
        while self.card_layout.count():
            widget = self.card_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.spec_links = {}
        self.position_tables = []
        if key is None:
            self.card_layout.addWidget(
                self.label(
                    ("Недавно открытых записей пока нет. Откройте договор или спецификацию в режиме «Все»."
                     if self.navigation_modes.currentIndex() == 2 else
                     "Ничего не найдено. Измените поиск или сбросьте фильтры."), "heading"
                )
            )
        elif key[0] == "spec":
            self.show_specification(
                next(s for s in self.specifications if s["id"] == key[1])
            )
        else:
            self.show_contract(key, self.groups[key])
        QTimer.singleShot(
            0, self, lambda: self.scroll.verticalScrollBar().setValue(old_scroll)
            if self.rendered_signature == signature else None
        )


    def open_tender(self, tender_id):
        self.tender_requested.emit(tender_id)

    def open_contract(self, key):
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.data(0, ROLE) == key:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                self._pending_navigation_scroll = None
                self._navigation_scroll_timer.stop()
                return

    def open_specification(self, spec_id):
        spec = next(s for s in self.specifications if s["id"] == spec_id)
        # Set the complete filter/selection state before rendering. Resetting
        # controls separately used to build and destroy several interim cards.
        controls = (self.navigation_modes, self.search, self.state, self.stage, self.overdue)
        blockers = [QSignalBlocker(control) for control in controls]
        self.reset_filters()
        self.state.setCurrentText("Архив" if spec["archived"] else "В работе")
        for blocker in blockers:
            blocker.unblock()
        key = ("spec", spec_id)
        if self.current_key != key:
            self.scroll.verticalScrollBar().setValue(0)
            self.remember_visit(key)
        self.current_key = key
        self._navigation_signature = None
        self.refresh()
        for index in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(index)
            for child_index in range(parent.childCount()):
                item = parent.child(child_index)
                if item.data(0, ROLE) == ("spec", spec_id):
                    parent.setExpanded(True)
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(item)
                    self._pending_navigation_scroll = None
                    self._navigation_scroll_timer.stop()
                    return
