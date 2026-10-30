"""Local tender search and chat prototype with explicit model demonstrations."""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from tender_app.widgets import WrappingLabel
from tender_app.models import chat_search_query, deadline_label


def text_label(text, name=""):
    label = WrappingLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class ChatInput(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ) and event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.ControlModifier,
        ):
            if not event.isAutoRepeat():
                self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AssistantPanel(QWidget):
    example_requested = Signal(str)
    today_requested = Signal()
    tender_requested = Signal(int)
    save_requested = Signal()
    cancel_requested = Signal()
    search_requested = Signal(str)

    def __init__(self, model_selector, welcome=True):
        super().__init__()
        self.setMinimumWidth(280)
        self.setObjectName("assistantPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.context = text_label("На экране: тендер не выбран", "chatContext")
        layout.addWidget(self.context)
        controls = QHBoxLayout()
        controls.addStretch(1)
        self.examples_toggle = QToolButton()
        self.examples_toggle.setText("Примеры")
        self.examples_toggle.setCheckable(True)
        controls.addWidget(self.examples_toggle)
        self.today_button = QPushButton("Подачи сегодня")
        controls.addWidget(self.today_button)
        layout.addLayout(controls)
        examples = QFrame()
        examples.setObjectName("chatExamples")
        examples_layout = QVBoxLayout(examples)
        examples_layout.setContentsMargins(0, 0, 0, 0)
        examples_layout.setSpacing(5)
        self.status_example = QPushButton("Пример: началась переторжка")
        self.note_example = QPushButton("Пример: отправлен запрос")
        self.failure_example = QPushButton("Пример: ошибка модели")
        self.failure_example.clicked.connect(self.simulate_model_failure)
        self.status_example.clicked.connect(
            lambda: self.example_requested.emit("status")
        )
        self.note_example.clicked.connect(lambda: self.example_requested.emit("note"))
        self.today_button.clicked.connect(self.today_requested)
        for button in (self.status_example, self.note_example, self.failure_example):
            examples_layout.addWidget(button)
        layout.addWidget(examples)
        examples.hide()
        self.examples_toggle.toggled.connect(examples.setVisible)
        self.examples = examples
        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        messages = QWidget()
        messages.setObjectName("chatMessages")
        self.messages = QVBoxLayout(messages)
        self.messages.setContentsMargins(0, 0, 2, 0)
        self.messages.setSpacing(6)
        self.messages.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(messages)
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.setInterval(60)
        scrollbar = self.scroll.verticalScrollBar()
        self.scroll_timer.timeout.connect(
            lambda: scrollbar.setValue(scrollbar.maximum())
        )
        scrollbar.rangeChanged.connect(self.settle_scroll)
        layout.addWidget(self.scroll, 1)
        self.composer = QFrame()
        self.composer.setObjectName("chatComposer")
        composer_layout = QVBoxLayout(self.composer)
        composer_layout.setContentsMargins(8, 6, 8, 6)
        composer_layout.setSpacing(4)
        self.input = ChatInput()
        self.input.setFixedHeight(62)
        self.input.setAccessibleName("Сообщение помощнику")
        self.input.setPlaceholderText("Найди 7207 или Найди Кузбассэнерго…")
        self.input.setToolTip(
            "Enter — отправить · Shift+Enter — новая строка. Модель пока не подключена."
        )
        composer_layout.addWidget(self.input)
        footer = QHBoxLayout()
        self.model_selector = model_selector
        self.model_selector.setObjectName("chatModel")
        self.model_selector.addItem("Основная · не задана", "primary")
        self.model_selector.addItem("Резервная · не задана", "fallback")
        self.model_selector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.model_selector.setAccessibleName("Модель помощника")
        self.model_selector.setToolTip(
            "Модели не подключены. Состояния и сбой можно проверить через «Примеры»."
        )
        self.model_state = text_label("Не подключена", "chatCaption")
        footer.addWidget(self.model_state)
        footer.addStretch(1)
        footer.addWidget(self.model_selector)
        self.send_button = QPushButton("↑")
        self.send_button.setObjectName("chatPrimary")
        self.send_button.setFixedSize(36, 36)
        self.send_button.setAccessibleName("Отправить сообщение")
        self.send_button.setToolTip("Отправить · Enter")
        self.send_button.setEnabled(False)
        footer.addWidget(self.send_button)
        composer_layout.addLayout(footer)
        layout.addWidget(self.composer)
        self.send_button.clicked.connect(self.send_message)
        self.input.submitted.connect(self.send_message)
        self.input.textChanged.connect(self.update_send_button)
        self.send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.input)
        self.send_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.send_shortcut.activated.connect(self.send_message)
        self.model_states = {
            "primary": ("unconfigured", False),
            "fallback": ("unconfigured", False),
        }
        self.responding = False
        self.retry_timer = QTimer(self)
        self.retry_timer.setSingleShot(True)
        self.retry_timer.timeout.connect(self.complete_demo_retry)
        self.pending_retry = None
        self.model_selector.currentIndexChanged.connect(self.render_model_state)
        self.render_model_state()
        self.proposal = None
        self.set_context(None)
        if welcome:
            self.message(
                "Помощник",
                "Найдите тендер по номеру или заказчику: «Найди …». "
                "Поиск использует сохранённые данные и работает без модели. "
                "Для свободных вопросов используйте «Настройки → Модели»; в основной версии без AI доступны локальные команды.",
            )
        self.setStyleSheet("""
            QWidget#assistantPanel { background: #191e25; border-left: 1px solid #455166; }
            QWidget#assistantPanel QLabel { background: transparent; }
            QFrame#chatExamples { background: transparent; border: none; }
            QScrollArea#chatScroll, QScrollArea#chatScroll > QWidget,
            QWidget#chatMessages { background: #191e25; border: none; }
            QFrame#chatComposer { background: #1c2027; border: 1px solid #495568; border-radius: 12px; }
            QWidget#assistantPanel QFrame#chatComposer QPlainTextEdit {
                background: transparent; border: none; padding: 2px; }
            QFrame#chatComposer QComboBox#chatModel { background: transparent;
                border: none; padding: 3px 5px; color: #a3adbd; }
            QFrame#chatComposer QComboBox#chatModel:hover { background: #293345; }
            QFrame#chatComposer QComboBox#chatModel:focus { border: 1px solid #8ba9ff; }
            QLabel#chatCaption { color: #a3adbd; font-size: 9pt; }
            QLabel#chatContext { color: #c1d2ff; font-weight: 600; }
            QFrame#chatMessage { background: #1c2027; border: 1px solid #343d4b; border-radius: 6px; }
            QFrame#chatUser { background: #252f42; border: 1px solid #3c4a65; border-radius: 6px; }
            QFrame#chatProposal { background: #1c2027; border: 1px solid #6d8fc9; border-left: 3px solid #8ba9ff; border-radius: 6px; }
            QFrame#chatMessage QLabel, QFrame#chatUser QLabel, QFrame#chatProposal QLabel { background: transparent; }
            QPushButton#chatPrimary { background: #4e6fcb; color: white; border: none;
                border-radius: 6px; padding: 0; font-size: 15pt; }
            QPushButton#chatPrimary:hover { background: #4c6cc4; }
            QPushButton#chatPrimary:focus { border: 2px solid #6d8fc9; }
            QPushButton#chatPrimary:disabled { background: #2b3750; color: #8993a3; }
        """)

    def scroll_to_bottom(self):
        self.scroll_timer.start()

    def settle_scroll(self):
        # Wrapping labels can change the range over several layout passes.
        if self.scroll_timer.isActive():
            self.scroll_timer.start()

    def message(self, author, text):
        frame = QFrame()
        frame.setObjectName("chatUser" if author == "Вы" else "chatMessage")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(5)
        layout.addWidget(text_label(author, "chatCaption"))
        layout.addWidget(text_label(text, "chatBody"))
        self.messages.addWidget(frame)
        self.scroll_to_bottom()
        return frame

    def send_message(self):
        text = self.input.toPlainText()
        if callable(getattr(self, "ai_check", None)) and not self.ai_check(text):
            return
        if not text.strip() or self.responding:
            return
        query = chat_search_query(text)
        if query is None and callable(getattr(self, "ai_submit", None)):
            self.ai_submit(text)
            return
        self.message("Вы", text)
        self.input.clear()
        if query is not None:
            self.search_requested.emit(query)
            return
        self.show_model_error(
            text, self.model_selector.currentData(), demonstration=False
        )

    def update_send_button(self):
        self.send_button.setEnabled(
            bool(self.input.toPlainText().strip()) and not self.responding
        )

    def render_model_state(self):
        state, demo = self.model_states[self.model_selector.currentData()]
        label, color = {
            "unconfigured": ("Не подключена", "#a3adbd"),
            "ready": ("Готова", "#8bdbb3"),
            "responding": ("Отвечает", "#edc184"),
            "unavailable": ("Недоступна", "#f19b9f"),
        }[state]
        self.model_state.setText(label + (" · пример" if demo else ""))
        self.model_state.setStyleSheet(f"color: {color}; font-size: 9pt;")
        self.model_state.setToolTip(
            "Демонстрация состояния, без обращения к модели"
            if demo
            else "API модели пока не подключён"
        )

    def set_model_state(self, profile, state, demonstration=False):
        self.model_states[profile] = (state, demonstration)
        self.render_model_state()

    def simulate_model_failure(self):
        if self.responding:
            return
        text = (
            self.input.toPlainText()
            if self.input.toPlainText().strip()
            else "Составь сводку по открытому тендеру"
        )
        self.model_selector.setCurrentIndex(0)
        self.message("Вы", text)
        self.input.clear()
        self.show_model_error(text, "primary", demonstration=True)

    def show_model_error(self, request, profile, demonstration):
        self.set_model_state(
            profile, "unavailable" if demonstration else "unconfigured", demonstration
        )
        description = (
            "Пример ошибки: основная модель недоступна. Можно повторить тот же запрос через резервную."
            if demonstration
            else "Модель не подключена: запрос не обработан, данные не изменены. Текст запроса сохранён в чате."
        )
        frame = self.message(
            "Помощник · пример" if demonstration else "Помощник", description
        )
        frame.setProperty("requestText", request)
        if profile == "primary":
            retry = QPushButton("Повторить через резервную")
            retry.clicked.connect(
                lambda: self.retry_request(request, retry, demonstration)
            )
            frame.layout().addWidget(retry)

    def retry_request(self, request, button, demonstration):
        if self.responding or not button.isEnabled():
            return
        button.setEnabled(False)
        self.model_selector.setCurrentIndex(1)
        if not demonstration:
            self.show_model_error(request, "fallback", demonstration=False)
            return
        self.responding = True
        self.update_send_button()
        self.set_model_state("fallback", "responding", demonstration=True)
        self.pending_retry = request
        self.retry_timer.start(700)

    def complete_demo_retry(self):
        if self.pending_retry is None:
            return
        request = self.pending_retry
        self.pending_retry = None
        self.responding = False
        self.set_model_state("fallback", "ready", demonstration=True)
        self.update_send_button()
        frame = self.message(
            "Помощник · пример",
            "Повтор через резервную завершён в демонстрационном режиме. "
            "Реального обращения к модели не было. Сохранённый запрос:\n" + request,
        )
        frame.setProperty("requestText", request)

    def show_search_results(self, query, tenders):
        from tender_app.theme import ENTITY_COLORS, style_record_link
        if not query:
            self.message(
                "Помощник",
                "Укажите номер, заказчика или предмет: например, «Найди 7207».",
            )
            return
        frame = self.message(
            "Помощник · локальный поиск",
            f"Поиск: {query}\nНайдено: {len(tenders)} · по всему реестру",
        )
        if not tenders:
            frame.layout().addWidget(
                text_label("Ничего не найдено. Уточните номер, заказчика или предмет.")
            )
        elif len(tenders) > 1:
            frame.layout().addWidget(text_label("Выберите нужную карточку:"))
        for tender in tenders:
            result = QFrame()
            result.setObjectName("chatMessage")
            content = QVBoxLayout(result)
            content.setContentsMargins(8, 6, 8, 6)
            content.setSpacing(4)
            for value, role in ((f'Тендер № {tender.number}', 'tender'), (tender.customer, 'company'),
                                (tender.title, None)):
                label = text_label(value, 'chatBody')
                if role:
                    label.setStyleSheet('color: ' + ENTITY_COLORS[role] + ';')
                content.addWidget(label)
            content.addWidget(
                text_label(f"{tender.status} · {deadline_label(tender)}", "chatCaption")
            )
            open_card = QPushButton("Открыть карточку")
            style_record_link(open_card, 'tender')
            open_card.setProperty("tenderId", tender.id)
            open_card.clicked.connect(
                lambda checked=False, tender_id=tender.id: self.tender_requested.emit(
                    tender_id
                )
            )
            content.addWidget(open_card)
            frame.layout().addWidget(result)
        self.scroll_to_bottom()

    def set_context(self, tender):
        self.context.setText(
            f"Открыт № {tender.number or 'без номера'}"
            if tender
            else "Тендер не выбран"
        )
        self.context.setToolTip(
            tender.customer if tender else "Общий чат по всему реестру"
        )
        for button in (self.status_example, self.note_example):
            button.setEnabled(
                tender is not None and tender.id is not None and self.proposal is None
            )

    def show_proposal(self, tender_id, number, summary):
        self.proposal = QFrame()
        self.proposal.setObjectName("chatProposal")
        self.proposal.setProperty("tenderId", tender_id)
        self.proposal.setProperty("number", number)
        layout = QVBoxLayout(self.proposal)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(text_label(f"Предложение · № {number}", "chatContext"))
        layout.addWidget(
            text_label("Не сохранено · действие попадёт в историю", "chatCaption")
        )
        self.proposal_summary = text_label(summary, "chatBody")
        layout.addWidget(self.proposal_summary)
        buttons = QHBoxLayout()
        self.confirm_button = QPushButton("Сохранить")
        self.confirm_button.setObjectName("chatPrimary")
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.setToolTip(
            "Отменить черновик предложения и все правки в нём"
        )
        self.confirm_button.clicked.connect(self.save_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        buttons.addWidget(self.confirm_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)
        self.messages.addWidget(self.proposal)
        self.status_example.setEnabled(False)
        self.note_example.setEnabled(False)
        self.scroll_to_bottom()

    def finish_proposal(self, outcome):
        frame = self.proposal
        tender_id, number = frame.property("tenderId"), frame.property("number")
        summary = self.proposal_summary.text()
        layout = frame.layout()
        for button in (self.confirm_button, self.cancel_button):
            button.setEnabled(False)
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    button = item.layout().takeAt(0).widget()
                    button.hide()
                    button.deleteLater()
        saved = outcome.startswith("Сохранено")
        action = (
            summary.splitlines()[0]
            if summary.startswith("Статус:")
            else "Изменения сохранены"
        )
        headline = f"✓ № {number} · {action}" if saved else f"№ {number} · Отменено"
        layout.addWidget(text_label(headline, "chatContext"))
        details = text_label(summary, "chatBody")
        details.hide()
        buttons = QHBoxLayout()
        expand = QToolButton()
        expand.setText("Подробнее")
        expand.setCheckable(True)
        expand.toggled.connect(details.setVisible)
        buttons.addWidget(expand)
        open_card = QToolButton()
        open_card.setText("Открыть карточку")
        open_card.clicked.connect(lambda: self.tender_requested.emit(tender_id))
        buttons.addWidget(open_card)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addWidget(details)
        self.proposal = None
        self.scroll_to_bottom()

    def show_results(self, tenders, day):
        frame = self.message(
            "Помощник",
            f"Подачи на {day:%d.%m.%Y} · МСК\n"
            "Статусы: В работе, Податься, Переторжка. По всему реестру.",
        )
        if not tenders:
            frame.layout().addWidget(text_label("Подходящих тендеров нет."))
        for tender, description in tenders:
            frame.layout().addWidget(text_label(description))
            button = QPushButton(f"Открыть № {tender.number}")
            button.clicked.connect(
                lambda checked=False, tender_id=tender.id: self.tender_requested.emit(
                    tender_id
                )
            )
            frame.layout().addWidget(button)
        self.scroll_to_bottom()
