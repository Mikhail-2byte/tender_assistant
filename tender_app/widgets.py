from PySide6.QtCore import QDate, Qt, QLocale
from PySide6.QtWidgets import QLabel, QLineEdit, QDialog, QVBoxLayout, QCalendarWidget, QDialogButtonBox, QToolButton


class WrappingLabel(QLabel):
    """Keep all wrapped text visible inside a resizable scroll area."""

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.heightForWidth(event.size().width())
        if height > 0 and height != self.minimumHeight():
            self.setMinimumHeight(height)


class OptionalDateEdit(QLineEdit):
    """ISO date input with a calendar; blank remains unknown, never today's date."""
    def __init__(self, value="", parent=None, display_format="yyyy-MM-dd"):
        super().__init__(parent)
        self.display_format = display_format
        initial = QDate.fromString(str(value or ""), "yyyy-MM-dd")
        self.setText(initial.toString(display_format) if initial.isValid() else str(value or ""))
        self.setPlaceholderText("Дата не указана" if display_format == "dd.MM.yyyy"
                                else "ГГГГ-ММ-ДД · дата не указана")
        self.setClearButtonEnabled(True)
        self.setToolTip("Введите дату " + ("ДД.ММ.ГГГГ" if display_format == "dd.MM.yyyy" else "ГГГГ-ММ-ДД")
                        + " или выберите в календаре. Пусто — дата неизвестна.")
        from tender_app.ui_icons import icon
        action = self.addAction(icon("calendar"), QLineEdit.ActionPosition.TrailingPosition)
        action.setText("Выбрать дату в календаре")
        action.triggered.connect(self.open_calendar)
        self.calendar_popup = None

    def date(self):
        text = self.text().strip()
        result = QDate.fromString(text, self.display_format)
        if not result.isValid():
            result = QDate.fromString(text, "yyyy-MM-dd")
        return result

    def setDate(self, value):
        self.setText(value.toString(self.display_format))

    def open_calendar(self):
        from tender_app.theme import style_popup
        if self.calendar_popup is not None:
            popup = self.calendar_popup
            calendar = popup.findChild(QCalendarWidget)
            current = self.date()
            calendar.setSelectedDate(current if current.isValid() else QDate.currentDate())
            popup.open()
            return
        popup = QDialog(self)
        popup.setWindowTitle("Выбрать дату")
        popup.setModal(True)
        style_popup(popup)
        layout = QVBoxLayout(popup)
        calendar = QCalendarWidget()
        calendar.setLocale(QLocale(QLocale.Language.Russian, QLocale.Country.Russia))
        calendar.setMinimumSize(340, 240)
        calendar.setStyleSheet("""
            QCalendarWidget, QCalendarWidget QWidget { background: #20262f; color: #ecf0f6; }
            QCalendarWidget QAbstractItemView { background: #20262f; color: #ecf0f6;
                selection-background-color: #4e6fcb; selection-color: white; alternate-background-color: #293344; }
            QCalendarWidget QToolButton { background: #293344; color: #ecf0f6; border: none; padding: 6px; }
            QCalendarWidget QMenu { background: #20262f; color: #ecf0f6; }
            QCalendarWidget QMenu::item:selected { background: #4e6fcb; }
            QCalendarWidget QSpinBox { background: #293344; color: #ecf0f6; }
        """)
        from PySide6.QtGui import QColor, QTextCharFormat
        weekend = QTextCharFormat()
        weekend.setForeground(QColor("#f19b9f"))
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            calendar.setWeekdayTextFormat(day, weekend)
        from tender_app.ui_icons import icon
        for object_name, name in (("qt_calendar_prevmonth", "chevron_left"), ("qt_calendar_nextmonth", "chevron_right")):
            button = calendar.findChild(QToolButton, object_name)
            if button:
                button.setIcon(icon(name))
        calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        current = self.date()
        calendar.setSelectedDate(current if current.isValid() else QDate.currentDate())
        layout.addWidget(calendar)
        buttons = QDialogButtonBox()
        buttons.addButton("Выбрать", QDialogButtonBox.ButtonRole.AcceptRole)
        clear = buttons.addButton("Не указан", QDialogButtonBox.ButtonRole.ResetRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        def accept_date():
            self.setDate(calendar.selectedDate())
            popup.accept()
        buttons.accepted.connect(accept_date)
        calendar.activated.connect(lambda *_: accept_date())
        buttons.rejected.connect(popup.reject)
        clear.clicked.connect(lambda: (self.clear(), popup.accept()))
        layout.addWidget(buttons)
        self.calendar_popup = popup
        popup.open()


def submission_fields(date_edit, time_edit):
    """Validate explicitly entered values; empty fields never imply today or midnight."""
    from PySide6.QtCore import QTime
    day = date_edit.date().toString("yyyy-MM-dd") if date_edit.text().strip() else None
    clock = time_edit.text().strip() or None
    if day == "":
        raise ValueError("Введите корректную дату подачи в формате ДД.ММ.ГГГГ.")
    if clock and (len(clock) != 5 or not QTime.fromString(clock, "HH:mm").isValid()):
        raise ValueError("Введите время по Москве в формате ЧЧ:ММ (00:00–23:59).")
    if clock and not day:
        raise ValueError("Для времени подачи укажите дату.")
    return dict(deadline_date=day, deadline_time=clock,
                deadline_timezone="Europe/Moscow" if day else None, deadline_needs_review=0)
