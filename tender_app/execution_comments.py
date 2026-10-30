"""Timestamped comments for a specification or a single position."""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)

from tender_app.widgets import WrappingLabel
from tender_app.execution_positions import application_numbers


def display_comments(values):
    return [
        (
            (
                datetime.fromisoformat(stamp).strftime("%d.%m.%Y %H:%M МСК")
                if stamp
                else "Дата не указана"
            ),
            message,
        )
        for stamp, message in values
    ]


class CommentResizeHandle(QFrame):
    height_changed = Signal(int)

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.setFixedHeight(8)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip(
            "Потяните вверх или вниз, чтобы изменить высоту поля комментария"
        )
        self.setStyleSheet(
            "QFrame { border-bottom: 2px solid #455166; } QFrame:hover { background: #25314a; }"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_y = event.globalPosition().y()
            self.start_height = self.editor.height()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, "start_y"):
            height = max(
                48,
                min(
                    400,
                    self.start_height + int(event.globalPosition().y() - self.start_y),
                ),
            )
            self.editor.setFixedHeight(height)
            self.height_changed.emit(height)
            event.accept()


class CommentFeed(QWidget):
    submitted = Signal(str)
    delete_requested = Signal(int)
    draft_changed = Signal(str)
    height_changed = Signal(int)

    def __init__(
        self,
        comments,
        read_only=False,
        draft="",
        parent=None,
        compact=False,
        input_height=64,
        comment_ids=None,
    ):
        super().__init__(parent)
        self.comment_ids = comment_ids or []
        self.read_only = read_only
        self.setStyleSheet("QFrame#historyEntry QLabel { background: transparent; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        if not read_only:
            self.edit = QPlainTextEdit()
            self.edit.setPlaceholderText("Что сделано или о чём договорились…")
            self.edit.setAccessibleName("Новый комментарий")
            self.edit.setMinimumHeight(88)
            self.edit.setMaximumHeight(140)
            self.edit.setPlainText(draft)
            if compact:
                self.edit.setFixedHeight(
                    max(48, min(400, input_height)) if type(input_height) is int else 64
                )
                composer = QWidget()
                composer.setSizePolicy(
                    QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
                )
                composer_layout = QVBoxLayout(composer)
                composer_layout.setContentsMargins(0, 0, 0, 0)
                composer_layout.setSpacing(0)
                composer_layout.addWidget(self.edit)
                self.resize_handle = CommentResizeHandle(self.edit)
                self.resize_handle.height_changed.connect(self.height_changed)
                composer_layout.addWidget(self.resize_handle)
                layout.addWidget(composer)
            else:
                layout.addWidget(self.edit)
            actions = QHBoxLayout()
            self.add_button = QPushButton("Сохранить")
            self.add_button.setToolTip("Сохранить комментарий · Ctrl+S / Ctrl+Enter")
            self.add_button.setObjectName("primary")
            self.add_button.clicked.connect(self.submit)
            actions.addWidget(self.add_button)
            hint = QLabel("МСК · Ctrl+S / Ctrl+Enter")
            hint.setObjectName("fieldLabel")
            actions.addWidget(hint)
            actions.addStretch(1)
            layout.addLayout(actions)
            self.shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.edit)
            self.shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.shortcut.activated.connect(self.submit)
            self.edit.textChanged.connect(self.changed)
            self.changed()
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.entries = QVBoxLayout()
        self.entries.setSpacing(6)
        self.entries.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(self.entries)
        layout.addStretch(1)
        self.set_comments(comments)

    def changed(self):
        text = self.edit.toPlainText()
        self.add_button.setEnabled(bool(text.strip()))
        if hasattr(self, "feedback"):
            self.feedback.hide()
        self.draft_changed.emit(text)

    def show_feedback(self, message, error=False):
        self.feedback.setText(message)
        self.feedback.setStyleSheet("color: #f19b9f;" if error else "color: #8bdbb3;")
        self.feedback.show()

    def submit(self):
        if self.edit.toPlainText().strip():
            self.submitted.emit(self.edit.toPlainText())

    def set_comments(self, comments):
        while self.entries.count():
            widget = self.entries.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        if not comments:
            self.entries.addWidget(QLabel("Комментариев пока нет."))
        for index, (stamp, message) in enumerate(comments):
            frame = QFrame()
            frame.setObjectName("historyEntry")
            column = QVBoxLayout(frame)
            column.setContentsMargins(10, 8, 10, 8)
            date = QLabel(stamp)
            date.setObjectName("historyDate")
            header = QHBoxLayout()
            header.addWidget(date, 1)
            if not self.read_only and index < len(self.comment_ids):
                delete = QPushButton("Удалить")
                delete.setObjectName("deleteComment")
                delete.clicked.connect(lambda _, key=self.comment_ids[index]: self.delete_requested.emit(key))
                header.addWidget(delete)
            column.addLayout(header)
            body = WrappingLabel(message)
            body.setWordWrap(True)
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(body)
            self.entries.addWidget(frame)


class PositionCommentsDialog(QDialog):
    saved = Signal()

    def __init__(self, repository, spec_id, position_id, parent=None):
        super().__init__(parent)
        self.repository, self.spec_id, self.position_id = (
            repository,
            spec_id,
            position_id,
        )
        self.setWindowTitle("Комментарии к позиции")
        self.setMinimumSize(700, 500)
        screen = self.screen().availableGeometry()
        self.resize(min(980, screen.width() - 60), min(740, screen.height() - 60))
        layout = QVBoxLayout(self)
        position = self.position()
        title = WrappingLabel(position.name)
        title.setWordWrap(True)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setObjectName("heading")
        layout.addWidget(title)
        numbers = application_numbers(
            {"positions": [{"supplier_document": position.supplier_document}]}
        )
        context = WrappingLabel(
            f"Приложение №: {numbers} · Изготовитель: {position.supplier or 'не указан'}"
        )
        context.setWordWrap(True)
        context.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(context)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.feed = CommentFeed(self.visible_comments(position),
                                comment_ids=[r[0] for r in repository.comment_entries(spec_id, position_id)])
        self.feed.delete_requested.connect(self.delete_comment)
        self.feed.submitted.connect(lambda _: self.save_comment())
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_comment)
        scroll.setWidget(self.feed)
        layout.addWidget(scroll, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    def position(self):
        spec = next(s for s in self.repository.specifications() if s.id == self.spec_id)
        return next(p for p in spec.positions if p.id == self.position_id)

    def visible_comments(self, position):
        comments = display_comments(position.comments)
        note = position.working_note.strip()
        if note and all(note != message.strip() for _, message in position.comments):
            comments.append(("Ранее сохранённая заметка · дата не указана", note))
        return comments

    def refresh_comments(self):
        rows = self.repository.comment_entries(self.spec_id, self.position_id)
        self.feed.comment_ids = [r[0] for r in rows]
        self.feed.set_comments(self.visible_comments(self.position()))

    def delete_comment(self, comment_id):
        import sqlite3

        if not confirm_comment_deletion(self):
            return
        try:
            self.repository.delete_execution_comment(self.spec_id, self.position_id, comment_id)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Не удалось удалить комментарий", str(error))
            return
        self.refresh_comments()
        self.feed.show_feedback("Комментарий удалён")
        self.saved.emit()

    def save_comment(self):
        import sqlite3

        if not self.feed.edit.toPlainText().strip():
            return True
        try:
            self.repository.set_position_comment(
                self.spec_id, self.position_id, self.feed.edit.toPlainText()
            )
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.feed.show_feedback("Не сохранено. Комментарий остался в поле ввода.", error=True)
            QMessageBox.warning(self, "Не удалось добавить комментарий", str(error))
            return False
        self.feed.edit.clear()
        self.refresh_comments()
        self.feed.show_feedback("Комментарий добавлен")
        self.saved.emit()
        return True

    def reject(self):
        if self.feed.edit.toPlainText().strip():
            answer = QMessageBox.question(
                self,
                "Новый комментарий",
                "Сохранить набранный комментарий?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Save and not self.save_comment():
                return
        super().reject()


def confirm_comment_deletion(parent):
    return QMessageBox.question(
        parent, "Удалить комментарий?", "Удалить выбранный комментарий?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes
