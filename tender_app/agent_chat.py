"""Standalone, full-page agent chat. Local read-only tools; no model/network claims."""
import json
import sqlite3
from datetime import date
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from tender_app.assistant_panel import AssistantPanel, text_label
from tender_app.models import chat_search_query, deadline_label, now_moscow, search_tenders
from tender_app.agent_queries import submission_day, submissions
from tender_app.execution_demo import date_label
from tender_app.ui_icons import icon
from tender_app.record_search import search_records
from tender_app.search_presentation import link_html
from tender_app.theme import style_record_link


class AgentHistory:
    """Bounded UI history stored separately from the business database/settings."""
    MAX_BYTES = 3_000_000
    MAX_CHATS = 10
    MAX_MESSAGES = 200

    def __init__(self, path):
        self.path = Path(path)
        self.chats = []
        self.active_id = None
        self.warning = ""
        try:
            if self.path.exists():
                if self.path.stat().st_size > self.MAX_BYTES:
                    raise ValueError("Файл истории слишком большой")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("chats"), list):
                    raise ValueError("Неизвестный формат истории")
                if len(data["chats"]) > self.MAX_CHATS:
                    raise ValueError("Превышен лимит диалогов")
                for chat in data["chats"]:
                    if not isinstance(chat, dict) or not isinstance(chat.get("id"), str):
                        continue
                    if not isinstance(chat.get("messages", []), list) or len(chat.get("messages", [])) > self.MAX_MESSAGES:
                        raise ValueError("Неизвестный формат сообщений")
                    messages = []
                    for message in chat.get("messages", []):
                        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant") or not isinstance(message.get("text"), str):
                            continue
                        links = [link for link in message.get("links", [])[:20]
                                 if isinstance(link, dict) and link.get("kind") in ("tender", "spec")
                                 and type(link.get("id")) is int and isinstance(link.get("label"), str)]
                        messages.append(dict(role=message["role"], text=message["text"][:12000], links=links))
                    self.chats.append(dict(id=chat["id"][:80], title=str(chat.get("title", "Диалог"))[:60],
                        messages=messages, draft=str(chat.get("draft", ""))))
                self.active_id = data.get("active")
        except (OSError, ValueError, TypeError, AttributeError) as error:
            # Never overwrite a malformed/unknown file automatically.
            self.warning = f"История не загружена: {error}. Исходный файл не изменён."
            self.chats = []
        if not self.chats:
            self.create()
        if self.active_id not in [chat["id"] for chat in self.chats]:
            self.active_id = self.chats[-1]["id"]

    @property
    def active(self):
        return next(chat for chat in self.chats if chat["id"] == self.active_id)

    def create(self):
        chat = dict(id=uuid4().hex, title="Без названия", messages=[], draft="")
        self.chats.append(chat)
        self.active_id = chat["id"]
        return chat

    def save(self):
        if self.warning:
            raise ValueError(self.warning)
        if len(self.chats) > self.MAX_CHATS or any(len(chat["messages"]) > self.MAX_MESSAGES for chat in self.chats):
            raise ValueError("Достигнут лимит локальной истории. Удалите ненужный диалог.")
        body = json.dumps(dict(version=1, active=self.active_id, chats=self.chats), ensure_ascii=False, indent=2).encode("utf-8")
        if len(body) > self.MAX_BYTES:
            raise ValueError("История слишком большая. Удалите ненужный диалог; существующие сообщения не удалялись.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_suffix(".tmp")
        try:
            staging.write_bytes(body)
            staging.replace(self.path)
        finally:
            staging.unlink(missing_ok=True)


class AgentConversation(AssistantPanel):
    def __init__(self, page):
        self.page = page
        super().__init__(QComboBox(), welcome=False)
        self.setObjectName("agentConversation")
        self.setMinimumWidth(0)
        self.context.hide()
        self.examples_toggle.hide()
        self.today_button.hide()
        self.model_selector.hide()
        self.model_state.setText("Локальные команды · модель не подключена")
        self.model_state.setWordWrap(False)
        self.model_state.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.model_state.setToolTip("Поиск и сводки работают локально. Произвольные вопросы требуют подключения AI-модели.")
        self.input.setAccessibleName("Сообщение агенту")
        self.input.setPlaceholderText("Напишите агенту… Например: Найди ДЕМО-101")
        self.input.setToolTip("Enter — отправить · Shift+Enter — новая строка. Доступны локальные команды; Выберите модель у поля сообщения.")
        self.input.setFixedHeight(92)
        self.send_button.setFixedSize(40, 40)
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(8)
        self.setStyleSheet(self.styleSheet() + """
            QWidget#agentConversation { background: #16191e; border: none; }
            QScrollArea#chatScroll, QScrollArea#chatScroll > QWidget,
            QWidget#chatMessages { background: #16191e; border: none; }
            QFrame#chatMessage { background: #1c2027; border: 1px solid #343d4b; border-radius: 8px; }
            QFrame#chatUser { background: #25314a; border: 1px solid #3c4a65; border-radius: 8px; }
            QFrame#chatComposer { background: #20262f; border: 1px solid #495568; border-radius: 10px; }
            QFrame#chatComposer QPlainTextEdit { background: transparent; border: none; padding: 4px; }
            QFrame#agentWelcome { background: transparent; border: none; }
            QLabel#agentTitle { color: #ecf0f6; font-size: 22pt; font-weight: 600; }
            QLabel#agentIntro { color: #a3adbd; font-size: 11pt; }
            QPushButton#agentQuick { text-align: left; background: #1c2027; color: #d9e4ff;
                border: 1px solid #3c4658; border-radius: 8px; padding: 16px; }
            QPushButton#agentQuick:hover { background: #252f3f; border-color: #6d8fc9; }
            QPushButton#agentQuick:focus { border: 1px solid #8ba9ff; }
        """)

    def send_message(self):
        self.page.send_message()


class AgentPage(QWidget):
    tender_requested = Signal(int)
    specification_requested = Signal(int)
    position_requested = Signal(int, int)

    def __init__(self, repository, history_path):
        super().__init__()
        self.repository = repository
        self.history = AgentHistory(history_path)
        self.loading = False
        self.history_dirty = False
        self.compact = False
        self.setObjectName("agentPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 12, 24, 12)
        outer.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("Агент")
        title.setObjectName("heading")
        header.addWidget(title)
        header.addStretch(1)
        history_label = QLabel("История чатов")
        header.addWidget(history_label)
        self.chat_selector = QComboBox()
        self.chat_selector.setMinimumWidth(140)
        self.chat_selector.setMaximumWidth(340)
        self.chat_selector.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.chat_selector.setAccessibleName("История чатов")
        self.chat_selector.setToolTip("Локальная история: до 10 диалогов и 200 сообщений в каждом. Автоматического удаления нет.")
        header.addWidget(self.chat_selector, 1)
        self.new_button = QPushButton("Новый чат")
        self.new_button.setIcon(icon("plus"))
        self.new_button.clicked.connect(self.new_chat)
        header.addWidget(self.new_button)
        self.delete_button = QToolButton()
        self.delete_button.setIcon(icon("trash"))
        self.delete_button.setAccessibleName("Удалить текущий диалог")
        self.delete_button.setToolTip("Удалить текущий диалог с этого компьютера")
        self.delete_button.clicked.connect(self.delete_chat)
        header.addWidget(self.delete_button)
        outer.addLayout(header)
        self.warning = text_label("", "warning")
        self.warning.hide()
        outer.addWidget(self.warning)
        center = QHBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.addStretch(1)
        self.column = QWidget()
        self.column.setMaximumWidth(940)
        self.column.setMinimumWidth(0)
        column = QVBoxLayout(self.column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        self.scope = text_label("Вся рабочая область · тендеры и исполнение", "fieldLabel")
        column.addWidget(self.scope)
        self.chat = AgentConversation(self)
        column.addWidget(self.chat, 1)
        self.history_note = text_label("История на этом компьютере · команды не изменяют базу", "fieldLabel")
        column.addWidget(self.history_note)
        center.addWidget(self.column, 1)
        center.addStretch(1)
        # Equal expanding side spacers keep a readable width without shrinking the column.
        center.setStretch(0, 0)
        center.setStretch(2, 0)
        outer.addLayout(center, 1)
        self.draft_timer = QTimer(self)
        self.draft_timer.setSingleShot(True)
        self.draft_timer.setInterval(350)
        self.draft_timer.timeout.connect(self.persist)
        self.chat.input.textChanged.connect(self.draft_changed)
        self.chat_selector.currentIndexChanged.connect(self.select_chat)
        self.refresh_selector()
        self.render_chat()
        if self.history.warning:
            self.show_warning(self.history.warning)

    def show_warning(self, text):
        self.warning.setText(text)
        self.warning.setVisible(bool(text))

    def set_compact(self, compact):
        self.compact = compact
        self.layout().setContentsMargins(*( (12, 4, 12, 4) if compact else (24, 12, 24, 12) ))
        self.scope.setVisible(not compact)
        self.history_note.setVisible(not compact)
        self.column.setMaximumWidth(16777215 if compact else 940)
        self.chat.input.setFixedHeight(54 if compact else 92)
        self.chat.layout().setSpacing(4 if compact else 8)
        self.render_chat()

    def persist(self):
        try:
            self.history.save()
        except (OSError, ValueError) as error:
            self.show_warning("Не удалось сохранить историю чата. " + str(error))
            return False
        self.history_dirty = False
        self.show_warning("")
        return True

    def flush(self):
        self.draft_timer.stop()
        self.history.active["draft"] = getattr(self, "ai_scrub", lambda text: text)(self.chat.input.toPlainText())
        return self.persist() if self.history_dirty else True

    def draft_changed(self):
        if not self.loading:
            self.history_dirty = True
            self.history.active["draft"] = getattr(self, "ai_scrub", lambda text: text)(self.chat.input.toPlainText())
            self.draft_timer.start()

    def refresh_selector(self):
        self.chat_selector.blockSignals(True)
        self.chat_selector.clear()
        for chat in reversed(self.history.chats):
            title = chat["title"] if chat["messages"] else "Без названия"
            self.chat_selector.addItem(title, chat["id"])
        self.chat_selector.setCurrentIndex(max(0, self.chat_selector.findData(self.history.active_id)))
        self.chat_selector.blockSignals(False)

    def select_chat(self, *_):
        identifier = self.chat_selector.currentData()
        if identifier and identifier != self.history.active_id:
            if not self.flush():
                self.refresh_selector()
                return
            self.history.active_id = identifier
            self.history_dirty = True
            self.render_chat()
            self.persist()

    def new_chat(self):
        if len(self.history.chats) >= self.history.MAX_CHATS:
            self.show_warning("Достигнут лимит 10 диалогов. Удалите ненужный диалог, чтобы создать новый.")
            return
        if not self.flush():
            return
        self.history.create()
        self.history_dirty = True
        self.persist()
        self.refresh_selector()
        self.render_chat()
        self.chat.input.setFocus()

    def delete_chat(self):
        answer = QMessageBox.question(self, "Удалить диалог?", "Сообщения и черновик этого диалога будут удалены с компьютера. Данные тендеров и исполнения не изменятся.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.draft_timer.stop()
        self.history.chats.remove(self.history.active)
        if self.history.chats:
            self.history.active_id = self.history.chats[-1]["id"]
        else:
            self.history.create()
        self.history_dirty = True
        self.persist()
        self.refresh_selector()
        self.render_chat()

    def render_chat(self):
        self.loading = True
        self.chat.scroll_timer.stop()
        while self.chat.messages.count():
            widget = self.chat.messages.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        if not self.history.active["messages"] and self.compact:
            self.chat.messages.addWidget(text_label("Напишите вопрос или команду: Найди…, Подачи сегодня, Сводка исполнения.", "fieldLabel"))
        elif not self.history.active["messages"]:
            welcome = QFrame()
            welcome.setObjectName("agentWelcome")
            content = QVBoxLayout(welcome)
            content.setContentsMargins(8, 44, 8, 24)
            content.setSpacing(16)
            content.addWidget(text_label("Чем помочь?", "agentTitle"))
            content.addWidget(text_label("Отдельный чат по всей рабочей области.\nНачните с поиска, сводки или проверки сроков.", "agentIntro"))
            commands = QGridLayout()
            commands.setSpacing(10)
            for index, (caption, command) in enumerate((("Найти тендер или спецификацию", "Найди "), ("Подачи сегодня", "Подачи сегодня"), ("Сводка исполнения", "Сводка исполнения"), ("Просроченные поставки", "Просроченные поставки"))):
                button = QPushButton(caption)
                button.setObjectName("agentQuick")
                button.setMinimumHeight(56)
                button.clicked.connect(lambda checked=False, text=command: self.run_quick(text))
                commands.addWidget(button, index // 2, index % 2)
            content.addLayout(commands)
            content.addWidget(text_label("Поиск и сводки работают локально. Для свободного диалога подключите модель в «Настройки → Модели».", "fieldLabel"))
            self.chat.messages.addWidget(welcome)
        else:
            for message in self.history.active["messages"]:
                self.render_message(message)
        self.chat.input.setPlainText(self.history.active["draft"])
        self.loading = False
        if self.history.active["messages"]:
            self.chat.scroll_to_bottom()

    def render_message(self, message):
        author = "Вы" if message["role"] == "user" else "Агент"
        text = message["text"]
        if text.startswith('Поиск:') and any('match' in link for link in message.get('links', [])):
            text = '\n\n'.join(text.split('\n\n')[:3])
        frame = self.chat.message(author, text)
        for link in message.get("links", []):
            html = link_html(link)
            if html:
                label = QLabel(html)
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setWordWrap(True)
                label.setMinimumWidth(0)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                frame.layout().addWidget(label)
            button = QPushButton(link["label"])
            style_record_link(button, link['kind'])
            button.setProperty("recordKind", link["kind"])
            button.setProperty("recordId", link["id"])
            button.setToolTip("Открыть сохранённую карточку. Запрос и переписка останутся в чате.")
            button.clicked.connect(lambda checked=False, item=link: self.open_record(item))
            frame.layout().addWidget(button)

    def open_record(self, link):
        try:
            records = self.repository.all() if link["kind"] == "tender" else self.repository.specifications()
            if not any(record.id == link["id"] for record in records):
                self.show_warning("Запись больше не найдена. Повторите поиск по сохранённым данным.")
                return
            position_id = link.get('position_id')
            if link['kind'] == 'spec' and type(position_id) is int and position_id > 0:
                spec = next(record for record in records if record.id == link['id'])
                if not any(p.id == position_id for p in spec.positions):
                    self.show_warning('Позиция больше не найдена. Повторите поиск.')
                    return
                self.position_requested.emit(link['id'], position_id)
            else:
                (self.tender_requested if link["kind"] == "tender" else self.specification_requested).emit(link["id"])
        except (OSError, ValueError, sqlite3.Error) as error:
            self.show_warning("Не удалось открыть запись: " + str(error))

    def run_quick(self, text):
        if self.chat.input.toPlainText().strip():
            self.chat.input.setFocus()
            return
        self.chat.input.setPlainText(text)
        self.chat.input.setFocus()
        if text != "Найди ":
            self.send_message()

    def submit_search(self, query):
        if self.chat.input.toPlainText().strip():
            self.show_warning("В чате есть черновик. Отправьте или очистите его перед поиском из верхней панели.")
            self.chat.input.setFocus()
            return
        self.run_quick("Найди " + query.strip())

    def send_message(self):
        text = self.chat.input.toPlainText().strip()
        if callable(getattr(self.chat, "ai_check", None)) and not self.chat.ai_check(text):
            return
        if self.chat.responding:
            return
        if not text:
            return
        if len(text) > 8000:
            self.show_warning("Сообщение слишком длинное. Максимум — 8000 символов; текст оставлен в поле.")
            return
        if len(self.history.active["messages"]) + 2 > self.history.MAX_MESSAGES:
            self.show_warning("В диалоге уже 200 сообщений. Создайте новый; прежняя история останется доступной.")
            return
        command = " ".join(text.casefold().rstrip(".!?").split())
        local = submission_day(text) is not None or chat_search_query(text) is not None or command in ("сводка", "сводка исполнения", "просроченные поставки", "просрочки", "сроки", "сроки поставки")
        if not local and callable(getattr(self, "ai_submit", None)):
            self.ai_submit(text)
            return
        self.history_dirty = True
        first = not self.history.active["messages"]
        self.history.active["messages"].append(dict(role="user", text=text, links=[]))
        if first:
            self.history.active["title"] = " ".join(text.split())[:48]
        self.loading = True
        self.chat.input.clear()
        self.loading = False
        self.history.active["draft"] = ""
        try:
            reply, links = self.local_reply(text)
        except (OSError, ValueError, sqlite3.Error) as error:
            reply, links = "Не удалось прочитать сохранённые данные: " + str(error), []
        if len(reply) > 12000:
            reply = reply[:11700] + "\n\nСводка сокращена. Полные сведения доступны по кнопкам карточек ниже."
        self.history.active["messages"].append(dict(role="assistant", text=reply, links=links))
        self.persist()
        self.refresh_selector()
        self.render_chat()
        self.chat.input.setFocus()

    def local_reply(self, text):
        query = chat_search_query(text)
        tenders = self.repository.all()
        specs = self.repository.specifications()
        by_id = {t.id: t for t in tenders}
        command = " ".join(text.casefold().rstrip(".!?").split())
        if query is not None:
            if not query.strip():
                return "После «Найди» укажите номер, заказчика, предмет, позицию или приложение.", []
            results = search_records(tenders, specs, query)
            shown = []
            counts = {}
            for hit in results:
                if counts.get(hit.kind, 0) < 10 and len(shown) < 20:
                    shown.append(hit)
                    counts[hit.kind] = counts.get(hit.kind, 0) + 1
            lines = [f'Поиск: {query}', f'Найдено: {len(results)} · Показано: {len(shown)}',
                     'По всей базе, включая архив. До 10 результатов каждого типа.']
            links = []
            for hit in shown:
                lines.append(f'{hit.kind} · {hit.title}\n{hit.context}\n{hit.field}: {hit.snippet}')
                links.append(hit.link(query))
            if not links:
                lines.append('Совпадений нет. Уточните номер или часть названия.')
            return '\n\n'.join(lines), links
        if submission_day(text):
            target, today = submissions(tenders, submission_day(text))
            lines = [f"Подачи на {date.fromisoformat(target):%d.%m.%Y} · МСК", "Статусы: В работе, Податься, Переторжка. По всему реестру.", f"Найдено: {len(today)}"]
            links = []
            for t in today[:20]:
                lines.append(f"№ {t.number} · {t.customer}\n{deadline_label(t)} · {t.status}")
                links.append(dict(kind="tender", id=t.id, label=f"Открыть № {t.number}"))
            if len(today) > 20:
                lines.append("Показаны первые 20. Остальные доступны в реестре.")
            return "\n\n".join(lines), links
        active = [s for s in specs if not s.archived and not s.completed]
        if command in ("сводка", "сводка исполнения"):
            lines = ["Исполнение · сохранённые данные по всей базе", f"В работе: {len(active)} · в архиве: {sum(s.archived for s in specs)}", f"Просрочено: {sum(s.overdue() for s in active)} · с указанным риском: {sum(bool(s.risk.strip()) for s in active)}", f"Без срока: {sum(not (s.delivery_due) for s in active)}", "Для списка поставок напишите «Сроки», для поиска — «Найди …»."]
            return "\n\n".join(lines), []
        if command in ("просроченные поставки", "просрочки", "сроки", "сроки поставки"):
            selected = [s for s in active if s.overdue()] if command in ("просроченные поставки", "просрочки") else active
            selected.sort(key=lambda s: s.delivery_due or "9999-12-31")
            lines = ["Просроченные поставки" if command in ("просроченные поставки", "просрочки") else "Сроки поставки · в работе", f"Найдено: {len(selected)} · данные на {now_moscow():%d.%m.%Y} МСК"]
            links = []
            for s in selected[:20]:
                lines.append(f"{s.number} · {by_id[s.tender_id].customer}\n{s.summary} · До {date_label(s.delivery_due)}")
                links.append(dict(kind="spec", id=s.id, label=f"Открыть {s.number}"))
            if len(selected) > 20:
                lines.append("Показаны первые 20. Полный список — в разделе «Исполнение».")
            return "\n\n".join(lines), links
        return ("AI-модель ещё не подключена, поэтому произвольный вопрос не обработан. Сообщение сохранено в этом диалоге; база не изменена.\n\n"
                "Сейчас доступны локальные команды:\n• Найди <номер, заказчик, позиция или приложение>\n• Подачи сегодня\n• Сводка исполнения\n• Просроченные поставки\n• Сроки", [])
