"""Small modal editors for cells in the tender registry."""
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QMessageBox,
)

from tender_app.models import STATUSES, now_moscow
from tender_app.tender_registry import COMMENT_HEADER, REGISTRY_FIELDS, latest_comment
from tender_app.widgets import OptionalDateEdit, submission_fields


class RegistryCellDialog(QDialog):
    def __init__(self, tender, field, parent=None):
        super().__init__(parent)
        self.tender = tender
        self.field = field
        labels = dict(zip(REGISTRY_FIELDS, (
            "Номер", "Заказчик", "Предмет", "Срок подачи · МСК", "Статус",
            "Площадка", "Последний комментарий",
        )))
        label = "Контакты заказчика" if field == "contacts" else labels.get(field, "Новый комментарий")
        self.setWindowTitle(f"{label} · № {tender.number}")
        self.resize(560, 230)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"№ {tender.number} · {label}"))
        if field == "deadline":
            form = QFormLayout()
            self.date = OptionalDateEdit(tender.deadline_date, display_format="dd.MM.yyyy")
            self.time = QLineEdit(tender.deadline_time or "")
            self.time.setPlaceholderText("ЧЧ:ММ · пусто — время не указано")
            self.time.setClearButtonEnabled(True)
            form.addRow("Дата подачи", self.date)
            form.addRow("Время по Москве", self.time)
            layout.addLayout(form)
            layout.addWidget(QLabel("Оставьте время пустым для срока только с датой.\n"
                                    "Очистите оба поля, чтобы убрать срок."))
        elif field == "status":
            self.editor = QComboBox()
            self.editor.addItems(STATUSES)
            if tender.status not in STATUSES:
                self.editor.addItem(tender.status)
            self.editor.setCurrentText(tender.status)
            layout.addWidget(self.editor)
        elif field in ("title", "action", "comment", "new_comment", "contacts"):
            self.editor = QPlainTextEdit()
            value = (latest_comment(tender.comment) if field == "comment" else
                     "" if field == "new_comment" else getattr(tender, field))
            self.editor.setPlainText(value)
            self.initial_text = self.editor.document().toRawText().replace("\u2029", "\n")
            layout.addWidget(self.editor)
            if field == "comment":
                comment_help = QLabel("Изменяется только последняя запись; прежняя история сохраняется.")
                layout.addWidget(comment_help)
                add = QPushButton("Добавить новый комментарий")
                def new_comment():
                    self.field = "new_comment"
                    self.editor.clear()
                    self.initial_text = ""
                    self.setWindowTitle(f"Новый комментарий · № {tender.number}")
                    comment_help.setText("Новая запись будет добавлена в историю с текущей датой.")
                    self.editor.setFocus()
                    add.hide()
                add.clicked.connect(new_comment)
                layout.addWidget(add)
        else:
            self.editor = QLineEdit(getattr(tender, field))
            layout.addWidget(self.editor)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet("color: #f19b9f;")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("Сохранить", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if field == "contacts":
            self.setMinimumSize(560, 380)
            screen = self.screen().availableGeometry()
            self.resize(min(920, screen.width() - 60), min(680, screen.height() - 60))
            self.setSizeGripEnabled(True)
            self.editor.setAccessibleName("Полный список контактов заказчика")
            from PySide6.QtGui import QKeySequence, QShortcut
            self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
            self.save_shortcut.activated.connect(self.save_button.click)

    def reject(self):
        if self.field == "contacts" and self.changes():
            answer = QMessageBox.question(
                self, "Несохранённые контакты", "Сохранить изменения контактов?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Save:
                self.save_button.click()
                return
            if answer != QMessageBox.StandardButton.Discard:
                return
        super().reject()

    def changes(self):
        field = self.field
        if field == "deadline":
            day = self.date.date().toString("yyyy-MM-dd") if self.date.text().strip() else None
            clock = self.time.text().strip() or None
            if (day, clock) == (self.tender.deadline_date, self.tender.deadline_time):
                return {}
            values = submission_fields(self.date, self.time)
            return values
        value = (self.editor.currentText() if field == "status" else
                 self.editor.document().toRawText().replace("\u2029", "\n")
                 if isinstance(self.editor, QPlainTextEdit) else self.editor.text())
        if isinstance(self.editor, QPlainTextEdit) and value == self.initial_text:
            return {}
        if field in ("comment", "new_comment"):
            previous = latest_comment(self.tender.comment) if field == "comment" else ""
            if value == previous:
                return {}
            if not value.strip():
                raise ValueError("Комментарий не может быть пустым. Для удаления используйте карточку.")
            headers = list(COMMENT_HEADER.finditer(self.tender.comment))
            if field == "comment" and self.tender.comment:
                prefix = self.tender.comment[:headers[-1].end()] if headers else ""
                return {"comment": prefix + value}
            prefix = self.tender.comment + ("\n\n" if self.tender.comment else "")
            return {"comment": prefix + f"[{now_moscow():%d.%m.%Y %H:%M} МСК]\n" + value}
        return {} if value == getattr(self.tender, field) else {field: value}
