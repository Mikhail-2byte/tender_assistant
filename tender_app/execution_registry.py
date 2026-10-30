"""Full-width contract → specification → position execution registry."""
from datetime import date, datetime
import sqlite3

from PySide6.QtCore import QTimer, Qt, QSize, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemDelegate, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget, QTabBar,
)

from tender_app.execution_groups import contract_key
from tender_app.execution_models import STAGES, shown
from tender_app.execution_positions import application_numbers
from tender_app.execution_registry_store import owner_key
from tender_app.execution_registry_table import CELL, RegistryItem, RegistryTable
from tender_app.models import now_moscow


HEADERS = {
    "contract": ["Договор", "Заказчик", "Спецификации / приложения", "Ближайшая\nпоставка", "Комментарий"],
    "specification": ["Спецификация", "Дата", "Тендер", "Приложения", "Ближайшая\nпоставка", "Статус", "Комментарий"],
    "position": ["Позиция / чертёж", "Отгружено /\nвсего", "Осталось", "Срок\nпоставки", "Осталось\nдней",
                 "Статус", "Изготовитель", "Приложение", "Авто", "Комментарий"],
}
WEIGHTS = {"contract": [210, 200, 280, 150, 300],
           "specification": [160, 100, 160, 110, 150, 170, 280],
           "position": [260, 130, 75, 110, 85, 150, 150, 100, 110, 240]}
MINIMUMS = {"contract": [150, 150, 180, 122, 160],
            "specification": [100, 94, 130, 100, 110, 130, 180],
            "position": [180, 105, 75, 96, 75, 115, 110, 100, 95, 170]}
DEFAULT_SORT = object()


def pending_due(spec):
    if spec.archived or spec.completed:
        return None
    if not spec.positions:
        return spec.revised or spec.due
    return min((p.due or spec.revised or spec.due for p in spec.positions
                if (p.remaining is None or p.remaining > 0) and (p.due or spec.revised or spec.due)), default=None)


def date_text(value):
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "—"


def applications(spec):
    value = application_numbers({"positions": [{"supplier_document": p.supplier_document} for p in spec.positions]})
    return "—" if value == "не указаны" else value


class ExecutionRegistry(QWidget):
    new_requested = Signal()
    create_requested = Signal(tuple, str)
    edit_requested = Signal(int)
    archive_requested = Signal(int, bool)
    tender_requested = Signal(int)
    resource_requested = Signal(int, str)
    contacts_requested = Signal(int)
    position_requested = Signal(int, int)
    complete_requested = Signal(int, int)
    saved = Signal()

    def __init__(self, repository, preferences=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.building = False
        self.active_editor = None
        self.tables = []
        self.today = now_moscow().date()
        preferences = preferences if isinstance(preferences, dict) else {}
        saved_expanded = preferences.get("expanded", [])
        self.expanded = {k for k in saved_expanded if isinstance(k, str)} if isinstance(saved_expanded, list) else set()
        self.widths = preferences.get("widths", {}) if isinstance(preferences.get("widths"), dict) else {}
        self.sorting = {"contract": [3, 0], "specification": [4, 0]}
        for level, sorting in preferences.get("sorting", {}).items() if isinstance(preferences.get("sorting"), dict) else []:
            if level in HEADERS and isinstance(sorting, list) and len(sorting) == 2 and all(type(v) is int for v in sorting):
                self.sorting[level] = [max(0, min(len(HEADERS[level]) - 1, sorting[0])), int(bool(sorting[1]))]
        if preferences.get("position_comment_last") is not True:
            saved = self.widths.get("position")
            if isinstance(saved, list) and len(saved) == 10:
                self.widths = dict(self.widths, position=[*saved[:8], saved[9], saved[8]])
            if "position" in self.sorting and self.sorting["position"][0] in (8, 9):
                self.sorting["position"][0] = 17 - self.sorting["position"][0]
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        heading = QHBoxLayout()
        title = QLabel("Реестр исполнения")
        title.setObjectName("breadcrumb")
        heading.addWidget(title, 1)
        self.state = QTabBar()
        self.state.addTab("В работе")
        self.state.addTab("Поставлены")
        self.state.setExpanding(False)
        self.state.setDrawBase(False)
        state = preferences.get("state", 0)
        self.state.setCurrentIndex(state if type(state) is int and state in (0, 1) else 0)
        self.state.setAccessibleName("Подборка исполнения")
        self.state.setStyleSheet("""
            QTabBar::tab { background: #202934; color: #b8c8db; border: 1px solid #455166;
                border-radius: 4px; padding: 8px 14px; margin-right: 6px; }
            QTabBar::tab:selected { background: #263f5c; color: white;
                border-bottom: 3px solid #7fabdf; font-weight: 600; }
        """)
        heading.addWidget(self.state)
        self.count = QLabel()
        self.count.setWordWrap(True)
        heading.addWidget(self.count)
        self.new_button = QPushButton("+ Спецификация")
        self.new_button.clicked.connect(self.new_requested.emit)
        heading.addWidget(self.new_button)
        layout.addLayout(heading)
        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Договор, заказчик, тендер, позиция, приложение, авто, комментарий…")
        self.search.setAccessibleName("Поиск в реестре исполнения")
        self.search.setClearButtonEnabled(True)
        self.search.setText(preferences.get("search", "") if isinstance(preferences.get("search"), str) else "")
        controls.addWidget(self.search, 1)
        self.stage = QComboBox()
        self.stage.addItems(["Все этапы", *STAGES, "Частично отгружено", "Разные этапы", "Завершено"])
        saved_stage = preferences.get("stage", "Все этапы")
        self.stage.setCurrentText(saved_stage if isinstance(saved_stage, str) else "Все этапы")
        self.stage.setAccessibleName("Этап в реестре исполнения")
        controls.addWidget(self.stage)
        self.overdue = QCheckBox("Просрочены")
        self.overdue.setChecked(preferences.get("overdue") is True)
        controls.addWidget(self.overdue)
        self.expand_button = QPushButton("Раскрыть")
        self.collapse_button = QPushButton("Свернуть")
        self.expand_button.clicked.connect(self.expand_all)
        self.collapse_button.clicked.connect(self.collapse_all)
        controls.addWidget(self.expand_button)
        controls.addWidget(self.collapse_button)
        layout.addLayout(controls)
        self.feedback = QLabel("Двойной щелчок — правка или новый комментарий. Enter — сохранить; Alt+Enter — новая строка; Esc — отменить.")
        self.feedback.setWordWrap(True)
        self.feedback.setObjectName("muted")
        layout.addWidget(self.feedback)
        self.table = RegistryTable(self, "contract", HEADERS["contract"])
        self.tables.append(self.table)
        layout.addWidget(self.table, 1)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda *_: self.search_timer.start())
        self.state.currentChanged.connect(self.refresh)
        self.stage.currentIndexChanged.connect(self.refresh)
        self.overdue.toggled.connect(self.refresh)
        self.refresh()
        scroll = preferences.get("scroll", 0)
        self.restore_scroll = scroll if type(scroll) is int and scroll >= 0 else 0

    def preferences(self):
        return dict(expanded=sorted(self.expanded), widths=self.widths, sorting=self.sorting,
                    position_comment_last=True,
                    search=self.search.text(), state=self.state.currentIndex(), stage=self.stage.currentText(),
                    overdue=self.overdue.isChecked(), scroll=self.table.verticalScrollBar().value())

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "restore_scroll"):
            value = self.restore_scroll
            del self.restore_scroll
            QTimer.singleShot(0, self, lambda: self.table.verticalScrollBar().setValue(value))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "table") and self.active_editor is None:
            for table in self.tables:
                if table.level not in self.widths:
                    self.set_widths(table)

    def set_widths(self, table):
        values = self.widths.get(table.level)
        valid = (isinstance(values, list) and len(values) == table.columnCount()
                 and all(type(v) is int and 48 <= v <= 3000 for v in values))
        if not valid:
            depth = {"contract": 0, "specification": 1, "position": 2}[table.level]
            available = max(920, table.viewport().width() - 3 if table.isVisible()
                            else self.width() - 44 - depth * 36)
            weights = WEIGHTS[table.level]
            minimums = MINIMUMS[table.level]
            extra = max(0, available - sum(minimums))
            values = [minimum + int(weight * extra / sum(weights)) for minimum, weight in zip(minimums, weights)]
        # The flexible last column must not restore its expanded width as a minimum.
        values = list(values)
        values[-1] = MINIMUMS[table.level][-1]
        self._setting_widths = True
        try:
            for col, value in enumerate(values):
                table.setColumnWidth(col, value)
        finally:
            self._setting_widths = False

    def remember_widths(self, table, column):
        if table.retired or self.building or getattr(self, "_setting_widths", False) or column == table.columnCount() - 1:
            return
        values = [table.columnWidth(i) for i in range(table.columnCount())]
        self.widths[table.level] = values
        for other in self.tables:
            if other is not table and other.level == table.level:
                self.set_widths(other)
        table.schedule_height()

    def note(self, owner):
        return self.notes.get(owner_key(owner), "")

    def note_text(self, owner):
        entry = self.note_entries.get(owner_key(owner))
        if not entry or not entry["message"]:
            return "—"
        stamp = (datetime.fromisoformat(entry["stamp"]).strftime("%d.%m.%Y %H:%M МСК")
                 if entry["stamp"] else "Дата не указана")
        return f"{stamp}\n{entry['message']}"

    def cell(self, item, column, text, owner, field="", raw=None, editable=False, sort=DEFAULT_SORT, overdue=False):
        text = str(text) if text is not None else "—"
        item.setText(column, text)
        item.setToolTip(column, text)
        item.setData(column, CELL, dict(owner=owner, field=field, raw=raw, editable=editable,
                                        sort=(text.casefold() if text != "—" else None) if sort is DEFAULT_SORT else sort, overdue=overdue))
        if editable:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        if overdue:
            item.setForeground(column, QColor("#ffaaaa"))

    def add_expander(self, table, item, owner, payload):
        meta = item.data(0, CELL)
        meta.update(expand_key=owner_key(owner), payload=payload)
        item.setData(0, CELL, meta)
        placeholder = RegistryItem(item)
        placeholder.setFirstColumnSpanned(True)
        placeholder.setSizeHint(0, QSize(0, 1))
        if owner_key(owner) in self.expanded or self.search.text().strip():
            item.setExpanded(True)
            self.populate(item, table)

    def refresh(self, *_):
        if self.active_editor is not None:
            return
        self.today = now_moscow().date()
        token = (self.repository.connection.total_changes,
                 self.repository.connection.execute("PRAGMA data_version").fetchone()[0],
                 getattr(self.repository, "_restore_generation", 0), self.today,
                 self.search.text(), self.state.currentIndex(), self.stage.currentText(), self.overdue.isChecked())
        if token == getattr(self, "_token", None):
            return
        self._token = token
        scroll = self.table.verticalScrollBar().value()
        self.building = True
        self.table.setUpdatesEnabled(False)
        try:
            for table in self.tables[1:]:
                table.retire()
            self.tables = [self.table]
            self.table.clear()
            self.note_entries = self.repository.registry_comment_summaries()
            self.notes = {owner: entry["message"] for owner, entry in self.note_entries.items()}
            self.tenders = {t.id: t for t in self.repository.all()}
            self.specs = {s.id: s for s in self.repository.specifications()}
            delivered_count = sum(s.archived for s in self.specs.values())
            self.state.setTabText(0, f"В работе ({len(self.specs) - delivered_count})")
            self.state.setTabText(1, f"Поставлены ({delivered_count})")
            self.groups = {}
            for spec in self.specs.values():
                tender = self.tenders[spec.tender_id]
                key = contract_key(tender, {"contract": spec.contract})
                group = self.groups.setdefault(key, dict(customer=tender.customer, contract=spec.contract, specs=[], tenders={}))
                group["specs"].append(spec)
                group["tenders"][tender.id] = tender
            linked = {s.tender_id for s in self.specs.values()}
            for tender in self.tenders.values():
                if tender.status == "Победа" and tender.id not in linked:
                    historical = (tender.source_sheet.strip() == "Тендеры"
                                  and "Источник: единый Excel-шаблон" not in tender.import_issues)
                    self.groups[("unassigned", tender.id)] = dict(
                        customer=tender.customer, contract="", specs=[], tenders={tender.id: tender},
                        historical=historical,
                        supplied=historical or tender.source_sheet.strip().casefold() == "отгружено (архив)")
            visible_specs = 0
            visible_contracts = 0
            historical_count = 0
            for key, group in self.groups.items():
                owner = ("contract", key)
                query = self.search.text().strip().casefold()
                contract_match = query in "\n".join([group["customer"], group["contract"], self.note(owner)]).casefold()
                specs = [s for s in group["specs"] if self.matches(s, contract_match)]
                if group["specs"] and not specs:
                    continue
                if not group["specs"] and (self.state.currentIndex() != int(group.get("supplied", False))
                        or self.stage.currentIndex() or self.overdue.isChecked()
                        or not (contract_match or any(query in t.number.casefold() for t in group["tenders"].values()))):
                    continue
                visible_specs += len(specs)
                visible_contracts += bool(group["contract"])
                historical_count += bool(group.get("historical"))
                item = RegistryItem(self.table)
                tender = next(iter(group["tenders"].values()))
                historical = group.get("historical", False)
                label = f"Тендер №{tender.number}" if historical else group["contract"] or "Без договора"
                self.cell(item, 0, label, owner)
                if historical:
                    source = f"Лист «{tender.source_sheet}», строка {tender.source_row}"
                    item.setToolTip(0, f"{label}\n{tender.customer}\n{tender.title}\n{source}\n"
                                      "Старая запись реестра тендеров. Отдельная поставка не создавалась.")
                self.cell(item, 1, group["customer"] or "Заказчик не указан", owner)
                listing = ("\n".join(f"{s.number}: прил. {applications(s)}" for s in specs)
                           or ("Старая запись реестра тендеров\nБез спецификаций" if historical else "Без спецификаций"))
                self.cell(item, 2, listing, owner)
                due = min((pending_due(s) for s in group["specs"]
                           if s.archived == (self.state.currentIndex() == 1) and pending_due(s)), default=None)
                self.cell(item, 3, date_text(due), owner, sort=due, overdue=bool(due and due < self.today.isoformat()))
                self.cell(item, 4, self.note_text(owner), owner, "comment", self.note(owner), True,
                          sort=self.note(owner).casefold())
                self.add_expander(self.table, item, owner, (group, specs))
            self.set_widths(self.table)
            self.table.apply_sort()
            counts = f"Договоров: {visible_contracts} · Спецификаций: {visible_specs}"
            if historical_count:
                counts += f" · Исторических тендеров: {historical_count}"
            self.count.setText(counts)
            if not self.table.topLevelItemCount():
                self.feedback.setText("Ничего не найдено. Измените поиск или фильтры.")
        finally:
            self.building = False
            self.table.setUpdatesEnabled(True)
        QTimer.singleShot(0, self, lambda: self.table.verticalScrollBar().setValue(scroll))

    def matches(self, spec, contract_match):
        state = self.state.currentIndex()
        if spec.archived != (state == 1):
            return False
        if self.stage.currentIndex() and self.stage.currentText() not in [spec.summary, *[p.stage for p in spec.positions]]:
            return False
        due = pending_due(spec)
        if self.overdue.isChecked() and not (due and due < self.today.isoformat()):
            return False
        query = self.search.text().strip().casefold()
        tender = self.tenders[spec.tender_id]
        text = [tender.number, tender.title, spec.number, spec.title, self.note(("specification", spec.id))]
        for p in spec.positions:
            text.extend([p.name, p.supplier, p.supplier_document, p.transport, self.note(("position", spec.id, p.id))])
        return contract_match or query in "\n".join(text).casefold()

    def populate(self, item, parent_table):
        placeholder = item.child(0)
        if parent_table.itemWidget(placeholder, 0) is not None:
            return
        meta = item.data(0, CELL)
        owner = meta["owner"]
        level = "specification" if owner[0] == "contract" else "position"
        table = RegistryTable(self, level, HEADERS[level])
        self.tables.append(table)
        if level == "specification":
            group, specs = meta["payload"]
            for spec in specs:
                self.specification_row(table, spec)
            if not specs:
                empty = RegistryItem(table, ["Спецификаций пока нет. Правая кнопка по договору → + Спецификация."])
                empty.setFirstColumnSpanned(True)
        else:
            spec = meta["payload"]
            for position in spec.positions:
                self.position_row(table, spec, position)
            if not spec.positions:
                empty = RegistryItem(table, ["Позиций пока нет. Правая кнопка по спецификации → Изменить спецификацию."])
                empty.setFirstColumnSpanned(True)
        self.set_widths(table)
        table.apply_sort()
        def resize_child():
            size = QSize(0, table.height())
            if placeholder.sizeHint(0) != size:
                placeholder.setSizeHint(0, size)
                # An expansion/resize signal may still be inside Qt's layout.
                # Let it finish before laying out the containing tree again.
                parent_table.scheduleDelayedItemsLayout()
                parent_table.schedule_height()
        table.height_changed.connect(resize_child)
        parent_table.setItemWidget(placeholder, 0, table)
        table.fit_height()
        resize_child()

    def specification_row(self, table, spec):
        item = RegistryItem(table)
        owner = ("specification", spec.id)
        tender = self.tenders[spec.tender_id]
        self.cell(item, 0, spec.number, owner, "number", spec.number, True)
        self.cell(item, 1, date_text(spec.date), owner, "date", spec.date, True, sort=spec.date)
        self.cell(item, 2, tender.number, owner, "tender", tender.id)
        self.cell(item, 3, applications(spec), owner)
        due = pending_due(spec)
        self.cell(item, 4, date_text(due), owner, sort=due, overdue=bool(due and due < self.today.isoformat()))
        self.cell(item, 5, "Поставлена" if spec.archived else spec.summary, owner)
        if spec.archived:
            item.setToolTip(5, f"Подборка «Поставлены». Сохранённый этап: {spec.summary}")
        self.cell(item, 6, self.note_text(owner), owner, "comment", self.note(owner), True,
                  sort=self.note(owner).casefold())
        self.add_expander(table, item, owner, spec)

    def position_row(self, table, spec, p):
        item = RegistryItem(table)
        owner = ("position", spec.id, p.id)
        due = p.due or spec.revised or spec.due
        active = not (spec.archived or spec.completed or p.remaining == 0)
        days = (date.fromisoformat(due) - self.today).days if due else None
        values = [
            (p.name, "name", p.name, True, None),
            (f"{shown(p.customer_shipped) if p.customer_shipped is not None else '?'} / {shown(p.ordered)} {p.unit}", "customer_shipped", p.customer_shipped, True, p.customer_shipped),
            (shown(p.remaining), "", None, False, p.remaining),
            (date_text(due), "due", p.due, True, due),
            ("Поставлено" if p.remaining == 0 else str(days) if days is not None else "—", "", None, False, days),
            (p.stage, "stage", p.stage, True, None),
            (p.supplier or "—", "supplier", p.supplier, True, None),
            (p.supplier_document or "—", "supplier_document", p.supplier_document, True, None),
            (p.transport or "—", "transport", p.transport, True, None),
            (self.note_text(owner), "comment", self.note(owner), True, None),
        ]
        for column, (text, field, raw, editable, sort) in enumerate(values):
            if column in (0, 5, 6, 7, 8, 9):
                sort = DEFAULT_SORT
            if field == "comment":
                sort = self.note(owner).casefold()
            self.cell(item, column, text, owner, field, raw, editable, sort, overdue=bool(column in (3, 4) and active and due and due < self.today.isoformat()))
        if not p.due:
            item.setToolTip(3, f"Общий срок спецификации: {date_text(due)}. Пустая дата в редакторе использует общий срок.")

    def activate_cell(self, item, column):
        meta = item.data(column, CELL) or {}
        if meta.get("field") == "tender" and self.finish_edit():
            self.tender_requested.emit(meta["raw"])

    def reveal_specification(self, spec_id):
        if not self.finish_edit():
            return
        self.search.clear()
        target = next((s for s in self.repository.specifications() if s.id == spec_id), None)
        if target is None:
            return
        self.state.setCurrentIndex(int(target.archived))
        self.stage.setCurrentIndex(0)
        self.overdue.setChecked(False)
        self.refresh()
        for i in range(self.table.topLevelItemCount()):
            contract = self.table.topLevelItem(i)
            group, specs = contract.data(0, CELL)["payload"]
            if not any(s.id == spec_id for s in specs):
                continue
            contract.setExpanded(True)
            self.populate(contract, self.table)
            table = self.table.itemWidget(contract.child(0), 0)
            for row in range(table.topLevelItemCount()):
                item = table.topLevelItem(row)
                if item.data(0, CELL).get("owner") == ("specification", spec_id):
                    item.setExpanded(True)
                    table.setCurrentItem(item)
                    self.table.scrollToItem(contract)
                    return

    def finish_edit(self):
        editor = self.active_editor
        if editor is None:
            return True
        meta = editor.registry_meta
        value = editor.toPlainText() if isinstance(editor, QPlainTextEdit) else editor.currentText() if isinstance(editor, QComboBox) else editor.text()
        try:
            if meta["field"] in ("due", "date") and value.strip():
                value = value.strip()
                if "." in value:
                    value = datetime.strptime(value, "%d.%m.%Y").date().isoformat()
                else:
                    value = date.fromisoformat(value).isoformat()
            if meta["field"] == "comment":
                changed = self.repository.update_registry_comment(meta["owner"], value, meta["raw"], append=True)
            else:
                changed = self.repository.update_registry_field(meta["owner"], meta["field"], value, meta["raw"])
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            self.feedback.setText(str(error))
            self.feedback.setStyleSheet("color: #ffaaaa;")
            editor.setToolTip(str(error))
            return False
        self.active_editor = None
        editor.registry_delegate.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
        self.feedback.setText(("Комментарий добавлен" if meta["field"] == "comment" else "Изменение сохранено")
                              if changed else "Без изменений")
        self.feedback.setStyleSheet("")
        if changed:
            QTimer.singleShot(0, self, self.after_save)
        return True

    def cancel_edit(self):
        editor = self.active_editor
        if editor is not None:
            self.active_editor = None
            editor.registry_delegate.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.RevertModelCache)
            self.feedback.setText("Изменение отменено")
            self.feedback.setStyleSheet("")

    def after_save(self):
        self.refresh()
        self.saved.emit()

    def expand_all(self):
        if not self.finish_edit():
            return
        # Populate lazily, then expand the newly created specification tables.
        for i in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(i)
            item.setExpanded(True)
            self.populate(item, self.table)
        for table in list(self.tables):
            if table.level == "specification":
                for i in range(table.topLevelItemCount()):
                    item = table.topLevelItem(i)
                    if item.childCount():
                        item.setExpanded(True)

    def collapse_all(self):
        if not self.finish_edit():
            return
        self.expanded.clear()
        for table in reversed(self.tables):
            table.collapseAll()

    def open_history(self, owner):
        if not self.finish_edit():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("История комментариев")
        dialog.resize(760, 560)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        entries = self.repository.registry_comment_entries(owner)
        lines = []
        for entry in entries:
            stamp = datetime.fromisoformat(entry["stamp"]).strftime("%d.%m.%Y %H:%M МСК") if entry["stamp"] else "Дата не указана"
            label = " · История событий" if entry.get("legacy") else " · Комментарий"
            if entry.get("source_deleted"):
                label += " · Исходная запись удалена"
            lines.append(f"{stamp}" + label + "\n" + (entry["message"] or "(комментарий очищен)"))
        text.setPlainText("\n\n".join(lines) or "Комментариев пока нет.")
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Закрыть")
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()

    def show_menu(self, table, point):
        item = table.itemAt(point)
        if item is None or not self.finish_edit():
            return
        meta = item.data(0, CELL) or {}
        owner = meta.get("owner")
        if not owner:
            return
        menu = QMenu(self)
        menu.addAction("История комментариев…", lambda: self.open_history(owner))
        if owner[0] == "contract":
            group = self.groups[tuple(owner[1])]
            menu.addAction("+ Спецификация", lambda: self.create_requested.emit(tuple(group["tenders"]), group["contract"]))
            edit_menu = menu.addMenu("Реквизиты спецификаций")
            for spec in group["specs"]:
                edit_menu.addAction(spec.number, lambda checked=False, sid=spec.id: self.edit_requested.emit(sid))
            tenders = list(group["tenders"].values())
        else:
            spec = self.specs[owner[1]]
            menu.addAction("Изменить спецификацию / позиции…", lambda: self.edit_requested.emit(spec.id))
            menu.addAction("Вернуть в работу" if spec.archived else "Перенести в «Поставлены»",
                           lambda: self.archive_requested.emit(spec.id, not spec.archived))
            if owner[0] == "position":
                menu.addAction("Изменить позицию…", lambda: self.position_requested.emit(spec.id, owner[2]))
                menu.addAction("Всё отгружено заказчику", lambda: self.complete_requested.emit(spec.id, owner[2]))
            tenders = [self.tenders[spec.tender_id]]
        menu.addSeparator()
        for tender in tenders:
            actions = menu.addMenu(f"Тендер {tender.number}") if len(tenders) > 1 else menu
            actions.addAction("Открыть тендер", lambda checked=False, tid=tender.id: self.tender_requested.emit(tid))
            actions.addAction("Контакты заказчика…", lambda checked=False, tid=tender.id: self.contacts_requested.emit(tid))
            folder = actions.addAction("Открыть папку", lambda checked=False, tid=tender.id: self.resource_requested.emit(tid, "folder"))
            folder.setEnabled(bool(tender.folder_path.strip()))
            url = actions.addAction("Открыть ЭТП", lambda checked=False, tid=tender.id: self.resource_requested.emit(tid, "url"))
            url.setEnabled(bool(tender.url.strip()))
        menu.exec(table.viewport().mapToGlobal(point))
        menu.deleteLater()
