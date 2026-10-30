"""Execution chat using the existing assistant UI and explicit local commands."""

from datetime import date

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QPushButton, QVBoxLayout, QWidget

from tender_app.assistant_panel import AssistantPanel
from tender_app.execution_models import shown
from tender_app.models import chat_search_query


class ExecutionAssistant(AssistantPanel):
    specification_requested = Signal(int)

    def __init__(self, repository, panel):
        super().__init__(QComboBox(), welcome=False)
        self.repository = repository
        self.panel = panel
        self.examples_toggle.hide()
        self.examples.hide()
        self.today_button.hide()
        self.input.setPlaceholderText("Сводка, остатки, сроки или Найди…")
        self.message(
            "Помощник · исполнение",
            "Сводка, остатки и сроки по сохранённым данным. "
            "Свободные вопросы — через «Настройки → Модели». Изменения только после подтверждения.",
        )
        self.quick_commands = QWidget()
        commands = QVBoxLayout(self.quick_commands)
        commands.setContentsMargins(0, 0, 0, 0)
        commands.setSpacing(8)
        for command, caption in (
            ("Сводка", "Краткая сводка"),
            ("Остатки", "Остатки по позициям"),
            ("Сроки", "Сроки поставки"),
        ):
            button = QPushButton(caption)
            button.clicked.connect(
                lambda checked=False, text=command: self.run_command(text)
            )
            commands.addWidget(button)
        self.layout().insertWidget(2, self.quick_commands)

        self.update_context()

    def run_command(self, text):
        # Never replace a user's unfinished question with a shortcut.
        if self.input.toPlainText().strip():
            self.input.setFocus()
            return
        self.input.setPlainText(text)
        self.send_message()

    def selected_specs(self):
        specs = self.repository.specifications()
        if self.panel.current_specification_id is not None:
            return [s for s in specs if s.id == self.panel.current_specification_id]
        group = self.panel.groups.get(self.panel.current_key)
        identifiers = {s["id"] for s in group["specs"]} if group else set()
        return [s for s in specs if s.id in identifiers]

    def update_context(self, *_):
        # Context labels use the panel's current snapshot; commands still read
        # saved records through selected_specs(). Navigation does not need SQL.
        spec = next((s for s in self.panel.specifications
                     if s["id"] == self.panel.current_specification_id), None)
        group = self.panel.groups.get(self.panel.current_key)
        if spec is not None:
            tender = next(t for t in self.panel.tenders if t.id == spec["tender_id"])
            self.context.setText(f"Спецификация {spec['number']} · тендер {tender.number}")
            self.context.setToolTip(tender.customer)
        elif group:
            self.context.setText(
                group["customer"] + "\n" + (group["contract"] or "Без договора")
            )
        else:
            self.context.setText("Исполнение · карточка не выбрана")

    def send_message(self):
        text = self.input.toPlainText().strip()
        if callable(getattr(self, "ai_check", None)) and not self.ai_check(text):
            return
        if not text or self.responding:
            return
        query = chat_search_query(text)
        if query is None and text.casefold().rstrip(".!?") not in ("сводка", "остатки", "сроки", "сроки поставки") and callable(getattr(self, "ai_submit", None)):
            self.ai_submit(text)
            return
        self.message("Вы", text)
        self.input.clear()
        if query is not None:
            self.search(query)
        elif text.casefold().rstrip(".!?") in ("сводка", "остатки"):
            specs = self.selected_specs()
            if not specs:
                self.message(
                    "Помощник",
                    "Выберите спецификацию или договор с позициями в списке слева.",
                )
            for spec in specs:
                self.message("Помощник · сохранённые данные", self.summary(spec))
        elif text.casefold().rstrip(".!?") in ("сроки", "сроки поставки"):
            self.show_deadlines()
        else:
            self.show_model_error(
                text, self.model_selector.currentData(), demonstration=False
            )

    @staticmethod
    def summary(spec):
        due = spec.delivery_due
        lines = [
            f"Спецификация {spec.number} · {spec.summary}",
            "Поставить до: "
            + (date.fromisoformat(due).strftime("%d.%m.%Y") if due else "не указано"),
            "Архив" if spec.archived else "В работе",
        ]
        for position in spec.positions:
            lines.append(
                f"{position.name}\nПриложение: {position.supplier_document or 'не указано'} · "
                f"{position.supplier or 'компания не указана'}\n"
                f"Заказано {shown(position.ordered)}, отгружено {shown(position.customer_shipped)}, "
                f"осталось {shown(position.remaining)} · {position.unit}"
            )
            if position.working_note:
                lines.append("Рабочая заметка: " + position.working_note)
            if position.comments:
                stamp, comment = position.comments[0]
                lines.append(
                    f"Последний комментарий ({stamp or 'без даты'}): {comment}"
                )
        if spec.comments:
            stamp, comment = spec.comments[0]
            lines.append(f"По спецификации ({stamp or 'без даты'}): {comment}")
        return "\n\n".join(lines)

    def results(self, title, specs):
        frame = self.message(
            "Помощник · сохранённые данные", f"{title}\nНайдено: {len(specs)}"
        )
        tenders = {t.id: t for t in self.repository.all()}
        for spec in specs:
            tender = tenders[spec.tender_id]
            due = spec.delivery_due
            label = f"{tender.customer} · тендер {tender.number}\nСпецификация {spec.number} · " f"{date.fromisoformat(due).strftime('%d.%m.%Y') if due else 'срок не указан'}" + (
                " · просрочено" if spec.overdue() else ""
            ) + (
                " · архив" if spec.archived else ""
            )
            button = QPushButton(label)
            button.setProperty("specificationId", spec.id)
            button.clicked.connect(
                lambda checked=False, identifier=spec.id: self.specification_requested.emit(
                    identifier
                )
            )
            frame.layout().addWidget(button)

    def search(self, query):
        if not query:
            self.message(
                "Помощник",
                "После «Найди» укажите номер, заказчика, приложение или позицию.",
            )
            return
        tenders = {t.id: t for t in self.repository.all()}
        specs = [
            s
            for s in self.repository.specifications()
            if query.casefold()
            in " ".join(
                [
                    s.number,
                    s.contract,
                    tenders[s.tender_id].number,
                    tenders[s.tender_id].customer,
                ]
                + [f"{p.name} {p.supplier_document} {p.supplier}" for p in s.positions]
            ).casefold()
        ]
        self.results(f"Поиск: {query} · включая архив", specs)

    def show_deadlines(self):
        specs = [
            s
            for s in self.repository.specifications()
            if not s.archived and not s.completed
        ]
        specs.sort(key=lambda s: s.delivery_due or "9999-12-31")
        self.results("Сроки поставки · все спецификации в работе", specs)
