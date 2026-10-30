from copy import deepcopy
import sqlite3

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from tender_app.execution_models import Position, STAGES, shown
from tender_app.execution_preview import WrappedRows
from tender_app.widgets import OptionalDateEdit


class DraftDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.modified = False
        self.edits = {}

    def changed(self, *_):
        self.modified = True
        if hasattr(self, "save_button"):
            self.save_button.setEnabled(True)
            self.save_button.setToolTip("Сохранить изменения · Ctrl+S")

    def add_edit(self, form, name, label, value, multiline=False, date=False):
        edit = (
            QPlainTextEdit(str(value or ""))
            if multiline
            else OptionalDateEdit(value) if date else QLineEdit(str(value or ""))
        )
        if multiline:
            edit.setFixedHeight(60)
        edit.setAccessibleName(label)
        edit.textChanged.connect(self.changed)
        self.edits[name] = edit
        form.addRow(label, edit)
        return edit

    def text(self, name):
        edit = self.edits[name]
        return edit.toPlainText() if isinstance(edit, QPlainTextEdit) else edit.text()

    def add_stage(self, form, current):
        self.stage = QComboBox()
        self.stage.addItems(STAGES)
        self.stage.setCurrentText(current)
        self.stage.currentTextChanged.connect(self.changed)
        form.addRow("Этап", self.stage)

    def add_buttons(self, layout):
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton(
            "Сохранить", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.save_button.setObjectName("primary")
        self.save_button.setEnabled(self.modified or (hasattr(self, "specification") and self.specification.id is None))
        self.save_button.setToolTip("Сохранить изменения · Ctrl+S" if self.save_button.isEnabled() else "Нет несохранённых изменений")
        from PySide6.QtGui import QShortcut, QKeySequence
        shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        shortcut.activated.connect(lambda: self.save() if self.save_button.isEnabled() else None)
        self.save_shortcut = shortcut
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def reject(self):
        if self.modified:
            answer = QMessageBox.question(
                self,
                "Несохранённые изменения",
                "Сохранить изменения перед закрытием?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Save:
                self.save()
                return
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()


class PositionDialog(DraftDialog):
    def __init__(self, position, parent=None):
        super().__init__(parent)
        self.position = deepcopy(position)
        self.setWindowTitle("Позиция спецификации")
        self.setMinimumSize(700, 500)
        screen = self.screen().availableGeometry()
        self.resize(min(920, screen.width() - 60), min(760, screen.height() - 60))
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(8)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setAlignment(Qt.AlignmentFlag.AlignTop)
        for name, label in (
            ("name", "Наименование / чертёж"),
            ("unit", "Единица измерения"),
            ("ordered", "Количество"),
            ("placed", "Размещено"),
            ("customer_shipped", "Отгружено заказчику"),
            ("supplier", "Поставщик"),
            ("supplier_document", "Приложение поставщика"),
            ("transport", "Транспорт"),
            ("location", "Местонахождение"),
            ("due", "Срок поставки позиции"),
            ("eta", "Ожидаемое прибытие"),
            ("note", "Исходные сведения / заметка"),
        ):
            value = getattr(position, name)
            self.add_edit(
                form,
                name,
                label,
                value if value is not None else "",
                multiline=name in {"name", "note", "comment", "working_note"},
                date=name in {"eta", "due"},
            )
        self.add_stage(form, position.stage)
        hint = QLabel(
            "Пустое количество — неизвестно; 0 — подтверждённое отсутствие. "
            "Допускается до 6 знаков после запятой. Отгружено — только заказчику."
        )
        hint.setWordWrap(True)
        hint.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        form.addRow(hint)
        scroll.setWidget(body)
        layout.addWidget(scroll)
        self.add_buttons(layout)

    def save(self):
        candidate = deepcopy(self.position)
        for name in self.edits:
            value = self.text(name).strip()
            setattr(
                candidate,
                name,
                (
                    value or None
                    if name in {"due", "eta", "placed", "customer_shipped"}
                    else value
                ),
            )
        candidate.stage = self.stage.currentText()
        try:
            candidate.validate()
        except ValueError as error:
            QMessageBox.warning(self, "Проверьте позицию", str(error))
            return False
        self.position = candidate
        self.modified = False
        self.accept()
        return True


class PositionQuickEditDialog(DraftDialog):
    def __init__(self, position, field, fallback_due=None, parent=None):
        super().__init__(parent)
        self.position = deepcopy(position)
        self.field = field
        labels = {"due": "Срок поставки позиции", "supplier": "Изготовитель",
                  "transport": "Транспорт", "supplier_document": "Приложение поставщика", "working_note": "Рабочая заметка"}
        self.setWindowTitle(labels[field])
        self.resize(580, 230 if field == "working_note" else 180)
        layout = QVBoxLayout(self)
        title = QLabel(position.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        layout.addWidget(title)
        form = QFormLayout()
        self.add_edit(form, field, labels[field], getattr(position, field),
                      multiline=field == "working_note", date=field == "due")
        layout.addLayout(form)
        if field == "due":
            from tender_app.execution_demo import date_label
            hint = QLabel(f"Пустая дата — общий срок спецификации: {date_label(fallback_due)}")
            hint.setWordWrap(True)
            layout.addWidget(hint)
        self.add_buttons(layout)

    def save(self):
        value = self.text(self.field).strip()
        candidate = deepcopy(self.position)
        setattr(candidate, self.field, (value or None) if self.field == "due" else value)
        try:
            candidate.validate()
        except ValueError as error:
            QMessageBox.warning(self, "Проверьте позицию", str(error))
            return False
        self.position = candidate
        self.modified = False
        self.accept()
        return True


class SpecificationDialog(DraftDialog):
    def __init__(self, repository, specification, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.specification = deepcopy(specification)
        self.setWindowTitle(
            "Спецификация · создание"
            if specification.id is None
            else f"Спецификация {specification.number}"
        )
        self.setMinimumSize(760, 560)
        screen = self.screen().availableGeometry()
        self.resize(min(1040, screen.width() - 60), min(800, screen.height() - 60))
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(8)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setAlignment(Qt.AlignmentFlag.AlignTop)
        tender = next(t for t in repository.all() if t.id == specification.tender_id)
        context = QLabel(
            f"{tender.number} · {tender.customer}\nКонтакты и документы — в карточке тендера."
        )
        context.setWordWrap(True)
        context.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        form.addRow(context)
        for name, label in (
            ("number", "Номер спецификации"),
            ("title", "Краткий предмет"),
            ("date", "Дата спецификации"),
            ("contract", "Реквизиты договора"),
            ("due", "Первоначальный договорный срок"),
            ("revised", "Согласованный перенос"),
            ("eta", "Прогноз прибытия"),
            ("risk", "Риск · пояснение, если есть"),
        ):
            self.add_edit(
                form,
                name,
                label,
                getattr(specification, name),
                multiline=name in {"title", "contract", "risk"},
                date=name in {"date", "due", "revised", "eta"},
            )
        self.add_stage(form, specification.stage)
        self.signed = QCheckBox("Спецификация подписана")
        self.signed.setChecked(specification.signed)
        self.signed.toggled.connect(self.changed)
        form.addRow(self.signed)
        self.completed = QCheckBox(
            "Подтверждаю завершение · все позиции отгружены заказчику"
        )
        self.completed.setChecked(specification.completed)
        self.completed.toggled.connect(self.changed)
        form.addRow(self.completed)
        self.add_edit(
            form, "history_note", "Новая запись в истории", "", multiline=True
        )
        scroll.setWidget(body)
        self.tabs.addTab(scroll, "Спецификация")
        positions = QWidget()
        positions_layout = QVBoxLayout(positions)
        self.positions = QTreeWidget()
        self.positions.setHeaderLabels(
            ["Позиция / поставщик", "Ед.", "Заказано", "Заказчику", "Этап"]
        )
        self.positions.setColumnWidth(0, 330)
        self.positions.setWordWrap(True)
        self.positions.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.positions.setItemDelegate(WrappedRows(self.positions))
        self.positions.header().sectionResized.connect(
            lambda *_: self.positions.doItemsLayout()
        )
        self.positions.itemDoubleClicked.connect(lambda *_: self.edit_position())
        positions_layout.addWidget(self.positions)
        buttons = QHBoxLayout()
        for label, callback in (
            ("+ Позиция", self.add_position),
            ("Изменить позицию", self.edit_position),
            ("Удалить позицию", self.remove_position),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        positions_layout.addLayout(buttons)
        self.tabs.addTab(positions, "Позиции")
        history = QPlainTextEdit()
        history.setReadOnly(True)
        history.setPlainText(
            "\n\n".join(
                f"{stamp}\n{message}" for stamp, message in specification.history
            )
            or "Записей пока нет. Новая запись добавляется при сохранении."
        )
        self.tabs.addTab(history, "История")
        self.refresh_positions()
        self.add_buttons(layout)

    def refresh_positions(self):
        self.positions.clear()
        for position in self.specification.positions:
            QTreeWidgetItem(
                self.positions,
                [
                    f"{position.name}\n{position.supplier}",
                    position.unit,
                    shown(position.ordered),
                    shown(position.customer_shipped),
                    position.stage,
                ],
            )
        self.tabs.setTabText(1, f"Позиции ({len(self.specification.positions)})")

    def add_position(self):
        dialog = PositionDialog(Position(), self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.specification.positions.append(dialog.position)
                self.changed()
                self.refresh_positions()

        finally:
            dialog.deleteLater()

    def edit_position(self):
        index = self.positions.indexOfTopLevelItem(self.positions.currentItem())
        if index < 0:
            return
        dialog = PositionDialog(self.specification.positions[index], self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.specification.positions[index] = dialog.position
                self.changed()
                self.refresh_positions()

        finally:
            dialog.deleteLater()

    def remove_position(self):
        index = self.positions.indexOfTopLevelItem(self.positions.currentItem())
        if index < 0:
            return
        if (
            QMessageBox.question(
                self,
                "Удаление позиции",
                "Удалить выбранную позицию? Изменение вступит в силу при сохранении спецификации.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.specification.positions.pop(index)
            self.changed()
            self.refresh_positions()

    def save(self):
        candidate = deepcopy(self.specification)
        for name in self.edits:
            if name != "history_note":
                value = self.text(name).strip()
                setattr(
                    candidate,
                    name,
                    (
                        value or None
                        if name in {"date", "due", "revised", "eta"}
                        else value
                    ),
                )
        candidate.stage = self.stage.currentText()
        candidate.signed = self.signed.isChecked()
        candidate.completed = self.completed.isChecked()
        try:
            saved = self.repository.save_specification(
                candidate, self.text("history_note")
            )
        except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Не удалось сохранить", str(error))
            return False
        self.specification = saved
        self.modified = False
        self.accept()
        return True
