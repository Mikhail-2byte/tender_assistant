from tender_app.execution_lifecycle import ExecutionLifecycle
from tender_app.execution_navigation import ExecutionNavigation
from dataclasses import asdict, replace
from datetime import datetime
import os
import sqlite3

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QFrame,
    QInputDialog,
    QLabel,
    QPushButton,
    QTabWidget,
    QStackedWidget,
    QVBoxLayout,
    QMessageBox,
    QHBoxLayout,
    QWidget,
)

from tender_app.execution_editor import SpecificationDialog, PositionQuickEditDialog
from tender_app.execution_models import Specification
from tender_app.execution_header import specification_facts
from tender_app.execution_positions import PositionTable
from tender_app.execution_registry import ExecutionRegistry
from tender_app.agent_chat import AgentPage
from tender_app.execution_panel import ExecutionPanel
from tender_app.execution_comments import PositionCommentsDialog, display_comments, confirm_comment_deletion
from tender_app.execution_assistant import ExecutionAssistant
from tender_app.models import Tender, now_moscow, resolve_folder, valid_web_url
from tender_app.window import Window


def specification_view(spec):
    data = asdict(spec)
    data["stage"] = spec.summary
    data["updated"] = (
        datetime.fromisoformat(spec.updated).strftime("%d.%m.%Y %H:%M МСК")
        if spec.updated
        else ""
    )
    data["history"] = [
        (datetime.fromisoformat(stamp).strftime("%d.%m.%Y %H:%M МСК"), message)
        for stamp, message in spec.history
    ]
    data["comments"] = display_comments(spec.comments)
    for position in data["positions"]:
        position["comments"] = display_comments(position["comments"])
    return data


class ExecutionWindow(ExecutionLifecycle, ExecutionNavigation, Window):
    def __init__(self, repository, settings, documents_root):
        super().__init__(repository, settings, documents_root)
        self.setWindowTitle("Тендеры и исполнение")
        self.pages = QTabWidget()
        self.legacy_tender_body = self.takeCentralWidget()
        self.legacy_tender_body.setParent(self)
        self.legacy_tender_body.hide()
        self.pages.addTab(QWidget(), "Реестр")
        self.execution = ExecutionPanel(
            self.tenders, [], today=now_moscow().date(), read_only=False
        )
        self.execution.tender_requested.connect(self.open_execution_tender)
        self.execution.edit_requested.connect(self.edit_execution_specification)
        self.execution.new_requested.connect(self.choose_new_specification)
        self.execution.create_requested.connect(self.create_contract_specification)
        self.execution.resource_requested.connect(self.open_execution_resource)
        self.execution.contacts_requested.connect(lambda tender_id: self.edit_registry_cell(tender_id, "contacts"))
        self.execution.archive_requested.connect(self.archive_specification)
        self.execution.comment_requested.connect(self.edit_position_comment)
        self.execution.position_edit_requested.connect(self.edit_position_field)
        self.execution.shipped_requested.connect(self.edit_position_shipped)
        self.execution.shipment_complete_requested.connect(self.complete_position_shipment)
        self.execution.position_column_widths = self.settings.get(
            "execution_compact_position_widths", []
        )
        self.execution.filters_toggle.setChecked(self.settings.get("execution_filters_expanded", False))
        self.execution.filters_toggle.toggled.connect(lambda expanded: self.settings.values.__setitem__("execution_filters_expanded", expanded))
        self.execution.comment_input_height = self.settings.get(
            "execution_comment_input_height", 64
        )
        self.execution.spec_comment_requested.connect(self.add_specification_comment)
        self.execution.spec_comment_delete_requested.connect(self.delete_specification_comment)
        self.execution.draft_changed.connect(self.update_execution_save_state)
        self.execution.tree.currentItemChanged.connect(self.update_execution_save_state)
        self.execution_assistant = ExecutionAssistant(repository, self.execution)
        self.execution_assistant.specification_requested.connect(
            self.open_specification
        )
        self.execution.splitter.addWidget(self.execution_assistant)
        self.execution_assistant.hide()
        self.execution_assistant_sizes = None
        self.execution_assistant_toggle = QPushButton("Помощник")
        self.execution_assistant_toggle.setObjectName("assistantToggle")
        self.execution_assistant_toggle.setCheckable(True)
        self.execution_assistant_toggle.toggled.connect(self.toggle_execution_assistant)
        self.execution.navigation_header.addWidget(self.execution_assistant_toggle)
        self.execution.tree.currentItemChanged.connect(
            self.execution_assistant.update_context
        )
        self.execution_home = QWidget()
        self.execution_home_layout = QVBoxLayout(self.execution_home)
        self.execution_home_layout.setContentsMargins(0, 0, 0, 0)
        self.execution_home_layout.addWidget(self.execution)
        self.pages.addTab(self.execution_home, "Исполнение")
        self.agent_page = AgentPage(repository, self.settings.path.parent / "agent-chat.json")
        self.agent_page.tender_requested.connect(self.open_execution_tender)
        self.agent_page.specification_requested.connect(self.open_specification)
        self.agent_page.position_requested.connect(self.open_search_position)
        self.agent_home = QWidget()
        self.agent_home_layout = QVBoxLayout(self.agent_home)
        self.agent_home_layout.setContentsMargins(0, 0, 0, 0)
        self.agent_home_layout.addWidget(self.agent_page)
        self.pages.addTab(self.agent_home, "Агент")
        self.execution_registry = ExecutionRegistry(repository, self.settings.get("execution_registry", {}))
        self.execution_registry.new_requested.connect(self.choose_new_specification)
        self.execution_registry.create_requested.connect(self.create_contract_specification)
        self.execution_registry.edit_requested.connect(self.edit_execution_specification)
        self.execution_registry.archive_requested.connect(self.archive_specification)
        self.execution_registry.tender_requested.connect(self.open_execution_tender)
        self.execution_registry.resource_requested.connect(self.open_execution_resource)
        self.execution_registry.contacts_requested.connect(lambda tid: self.edit_registry_cell(tid, "contacts"))
        self.execution_registry.complete_requested.connect(self.complete_position_shipment)
        self.execution_registry.position_requested.connect(self.edit_registry_position)
        self.execution_registry.saved.connect(self.refresh_execution)
        self.pages.addTab(self.execution_registry, "Реестр исполнения")
        self.agent_return_page = 0
        from tender_app.tender_registry import TenderRegistry
        self.registry = TenderRegistry()
        self.registry.tender_requested.connect(self.open_registry_tender)
        self.registry.edit_requested.connect(self.edit_registry_cell)
        self.registry.new_requested.connect(self.new_tender)
        self.registry.import_requested.connect(self.open_import_1c)
        self.registry_pages = QStackedWidget()
        self.registry_pages.addWidget(self.registry)
        self.registry_card_page = QWidget()
        self.registry_card_layout = QVBoxLayout(self.registry_card_page)
        self.registry_card_layout.setContentsMargins(0, 0, 0, 0)
        self.registry_pages.addWidget(self.registry_card_page)
        placeholder = self.pages.widget(0)
        self.pages.removeTab(0)
        self.pages.insertTab(0, self.registry_pages, "Реестр")
        self.pages.setCurrentIndex(0)
        placeholder.deleteLater()
        self.registry_back = QPushButton("← Назад в реестр")
        self.registry_back.clicked.connect(self.return_to_registry)
        self.registry_back.hide()
        self.registry_card_layout.addWidget(self.registry_back)
        self.registry_card_layout.addWidget(self.card, 1)
        self.setCentralWidget(self.pages)
        self.spec_section = QFrame()
        self.spec_section.setObjectName("section")
        self.spec_layout = QVBoxLayout(self.spec_section)
        self.scroll.widget().layout().insertWidget(1, self.spec_section)
        self.status.currentTextChanged.connect(self.refresh_tender_specs)
        self.last_page = 0
        self.assistant_on_tenders = self.assistant_toggle.isChecked()
        self.pages.currentChanged.connect(self.change_page)
        menu = self.menuBar().addMenu("Исполнение")
        menu.addAction(
            "Полный экспорт тендеров и исполнения в JSON…", self.export_execution
        )
        for top_action in self.menuBar().actions():
            if top_action.menu():
                for action in top_action.menu().actions():
                    if "CSV" in action.text():
                        action.setText("Экспорт только тендеров в CSV…")
        self.refresh_execution()
        self.clock_timer.timeout.disconnect(self.refresh_list)
        self.clock_timer.timeout.connect(self.refresh_clock)
        self.execution.search.setText(self.search.text())
        self.execution.restore_navigation_preferences(self.settings.get("execution_navigation", {}))
        width = self.settings.get("execution_registry_width", 300)
        self.execution_registry_width = min(600, max(260, width))
        self.execution.splitter.setSizes([self.execution_registry_width, max(400, self.width()-self.execution_registry_width-60), 0])
        def remember_registry_width(*_):
            size = self.execution.splitter.sizes()[0]
            if self.execution.splitter.widget(0).isVisible() and 200 <= size <= 800:
                self.execution_registry_width = size
        self.execution.splitter.splitterMoved.connect(remember_registry_width)
        from tender_app.app_shell import install_shell

        install_shell(self, self.pages)
        self.save_action.setEnabled(False)
        from tender_app.ai_runtime import AIService
        self.ai_service = AIService(self)
        from PySide6.QtGui import QAction
        settings_action = QAction("Настройки…", self)
        settings_action.triggered.connect(self.ai_service.open_settings)
        self.application_menu.insertAction(self.application_menu.actions()[0], settings_action)
        self.application_menu_button.setToolTip("Меню и настройки")
        self.ai_settings_action = settings_action
        self.agent_dock = QDockWidget("Агент · Ctrl+Ё", self)
        self.agent_dock.setObjectName("bottomAgentDock")
        self.agent_dock.setAllowedAreas(Qt.DockWidgetArea.BottomDockWidgetArea)
        self.agent_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.agent_dock)
        self.agent_dock.hide()
        self.agent_dock.visibilityChanged.connect(self.agent_dock_visibility_changed)
        self.agent_dock_focus = None
        self.agent_dock_shortcuts = []
        for sequence in ("Ctrl+`", "Ctrl+Ё"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self.toggle_agent_dock)
            shortcut.activatedAmbiguously.connect(self.toggle_agent_dock)
            self.agent_dock_shortcuts.append(shortcut)
        self.application_menu.addAction("Агент снизу · Ctrl+Ё", self.toggle_agent_dock)


    def load_tender(self, tender):
        super().load_tender(tender)
        if hasattr(self, "spec_section"):
            self.refresh_tender_specs()

    def guard_changes(self):
        if hasattr(self, "execution_registry") and not self.execution_registry.finish_edit():
            return False
        return super().guard_changes()

    def refresh_execution(self):
        connection = self.repository.connection
        # Detect both writes on this connection and commits by other connections.
        # In-place restores need an explicit generation: SQLite backup does not
        # advance either counter on the destination connection.
        token = (connection, connection.total_changes,
                 connection.execute("PRAGMA data_version").fetchone()[0],
                 getattr(self.repository, "_restore_generation", 0))
        if token != getattr(self, "_execution_data_token", None):
            self.tenders = self.repository.all()
            specs = self.repository.specifications()
            self.execution.tenders = self.tenders
            self.execution.specifications = [specification_view(s) for s in specs]
            comment_ids = {}
            for row in connection.execute(
                    "SELECT spec_id, id FROM execution_comments WHERE position_id IS NULL ORDER BY id DESC"):
                comment_ids.setdefault(row[0], []).append(row[1])
            for spec in self.execution.specifications:
                spec["comment_ids"] = comment_ids.get(spec["id"], [])
            self._execution_specs = specs
            self._execution_data_token = token
        specs = self._execution_specs
        self.execution.today = now_moscow().date()
        self.execution.refresh()
        self.execution_assistant.update_context()
        self.refresh_tender_specs(specs=specs)

        self.registry.set_tenders(self.tenders, self.repository.unseen_tender_ids())
        self.execution_registry.refresh()


    def refresh_clock(self):
        """Clock ticks change deadlines, not data; leave hidden views untouched."""
        if self.isMinimized():
            return
        index = self.pages.currentIndex()
        if index == 0 and self.registry_pages.currentWidget() == self.registry:
            self.registry.refresh()
        elif index == 0:
            self.refresh_tender_specs(specs=self._execution_specs)
        elif index == 1:
            self.execution.today = now_moscow().date()
            self.execution.refresh()
        elif index == 3:
            self.execution_registry.refresh()


    def changeEvent(self, event):
        super().changeEvent(event)
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "execution"):
            self.refresh_clock()


    def refresh_registry(self):
        self.registry.set_tenders(self.repository.all(), self.repository.unseen_tender_ids())

    def refresh_list(self):
        super().refresh_list()
        if hasattr(self, "registry"):
            self.refresh_registry()

    def open_assistant_tender(self, tender_id):
        return self.open_registry_tender(tender_id)

    def refresh_tender_specs(self, *_, specs=None):
        if not hasattr(self, "spec_section"):
            return
        specs = [s for s in (self.repository.specifications() if specs is None else specs)
                 if self.current and s.tender_id == self.current.id]
        won = self.current and self.status.currentText() == "Победа"
        signature = (bool(won), now_moscow().date(), repr(specs))
        if signature == getattr(self, "_tender_specs_signature", None):
            return
        self._tender_specs_signature = signature
        while self.spec_layout.count():
            widget = self.spec_layout.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        self.spec_section.setVisible(bool(won or specs))
        label = QLabel(
            "Позиции и спецификации"
            if won
            else "Исполнение сохранено · результат тендера отличается от «Победа»"
        )
        label.setWordWrap(True)
        label.setObjectName("sectionHeading")
        self.spec_layout.addWidget(label)
        for spec in specs:
            entry = QWidget()
            line = QHBoxLayout(entry)
            line.setContentsMargins(0, 4, 0, 4)
            line.addWidget(specification_facts(specification_view(spec), now_moscow().date()), 1)
            button = QPushButton("Открыть →")
            button.setAccessibleName(f"Открыть спецификацию {spec.number}")
            button.clicked.connect(
                lambda checked=False, identifier=spec.id: self.open_specification(
                    identifier
                )
            )
            line.addWidget(button)
            self.spec_layout.addWidget(entry)
            table = PositionTable(specification_view(spec)["positions"], False,
                fallback_due=spec.revised or spec.due, today=now_moscow().date(),
                inactive=spec.completed or spec.archived)
            table.comment_requested.connect(lambda pid, sid=spec.id: self.edit_position_comment(sid, pid))
            table.edit_requested.connect(lambda pid, field, sid=spec.id: self.edit_position_field(sid, pid, field))
            table.shipped_requested.connect(lambda pid, sid=spec.id: self.edit_position_shipped(sid, pid))
            table.complete_requested.connect(lambda pid, sid=spec.id: self.complete_position_shipment(sid, pid))
            self.spec_layout.addWidget(table)
        if won:
            button = QPushButton("+ Спецификация к этому тендеру")
            button.clicked.connect(lambda: self.create_specification(self.current.id))
            self.spec_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)


    def new_tender(self):
        if not hasattr(self, "pages"):
            return super().new_tender()
        if not self.guard_changes():
            return
        self.load_tender(Tender())
        self.pages.setCurrentIndex(0)
        self.mark_dirty()
        self.refresh_list()
        self.show_registry_card()
        self.details_toggle.setChecked(True)
        self.edits["number"].setFocus()


    def edit_registry_cell(self, tender_id, field):
        from tender_app.registry_editor import RegistryCellDialog
        if not self.guard_changes():
            return
        records = self.repository.all()
        if self.dirty and self.current:
            previous = next((t for t in records if t.id == self.current.id), None)
            if previous:
                self.load_tender(previous)
        tender = next((t for t in records if t.id == tender_id), None)
        if tender is None:
            self.refresh_registry()
            return
        dialog = RegistryCellDialog(tender, field, self)

        def save_cell():
            try:
                changes = dialog.changes()
                if not changes:
                    dialog.accept()
                    return
                current = next((t for t in self.repository.all() if t.id == tender_id), None)
                if current is None or any(getattr(current, key) != getattr(tender, key) for key in changes):
                    raise ValueError("Ячейка уже изменилась. Отмените правку и откройте её заново. Введённый текст остаётся здесь.")
                candidate = replace(current, **changes)
                if "number" in changes and candidate.number.strip():
                    duplicates = self.repository.duplicates(candidate.number, candidate.id)
                    if duplicates and QMessageBox.question(
                        dialog, "Возможный дубль", "Такой номер уже есть в базе. Сохранить?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    ) != QMessageBox.StandardButton.Yes:
                        return
                saved = self.repository.save(candidate)
            except (OSError, ValueError, sqlite3.Error) as error:
                dialog.error_label.setText(f"Не сохранено: {error}")
                return
            self.refresh_execution()
            if self.current and self.current.id == saved.id:
                self.load_tender(saved)
            self.refresh_list()
            self.refresh_registry()
            self.statusBar().showMessage("Ячейка сохранена", 5000)
            dialog.accept()

        dialog.save_button.clicked.connect(save_cell)
        try:
            dialog.exec()

        finally:
            dialog.deleteLater()


    def choose_new_specification(self):
        tenders = [t for t in self.repository.all() if t.status == "Победа"]
        if not tenders:
            self.statusBar().showMessage(
                "Сначала сохраните тендер с результатом «Победа».", 5000
            )
            return
        labels = [f"{t.number} · {t.customer} · {t.title} [ID {t.id}]" for t in tenders]
        chosen, ok = QInputDialog.getItem(
            self, "Новая спецификация", "Выберите выигранный тендер", labels, 0, False
        )
        if ok:
            self.create_specification(tenders[labels.index(chosen)].id)

    def create_specification(self, tender_id, contract=""):
        if not self.guard_changes():
            return
        if tender_id is None and self.current:
            tender_id = self.current.id
        tender = next((t for t in self.repository.all() if t.id == tender_id), None)
        if tender is None or tender.status != "Победа":
            self.statusBar().showMessage(
                "Сначала сохраните тендер с результатом «Победа».", 5000
            )
            return
        self.edit_specification(Specification(tender_id=tender_id, contract=contract))

    def create_contract_specification(self, tender_ids, contract):
        tenders = [
            t
            for t in self.repository.all()
            if t.id in tender_ids and t.status == "Победа"
        ]
        if not tenders:
            self.statusBar().showMessage(
                "Для новой спецификации нужен тендер с результатом «Победа».", 5000
            )
            return
        if len(tenders) == 1:
            self.create_specification(tenders[0].id, contract)
            return
        labels = [f"{t.number} · {t.customer} [ID {t.id}]" for t in tenders]
        chosen, ok = QInputDialog.getItem(
            self,
            "Спецификация к договору",
            "К какому тендеру относится спецификация?",
            labels,
            0,
            False,
        )
        if ok:
            self.create_specification(tenders[labels.index(chosen)].id, contract)

    def open_execution_resource(self, tender_id, resource):
        tender = next(t for t in self.repository.all() if t.id == tender_id)
        try:
            if resource == "folder":
                path = resolve_folder(tender.folder_path, self.documents_root)
                if not path.is_dir():
                    raise ValueError(
                        "Папка не найдена. Её путь можно изменить в исходной карточке тендера."
                    )
                os.startfile(path)
            elif valid_web_url(tender.url):
                QDesktopServices.openUrl(QUrl(tender.url))
            else:
                raise ValueError(
                    "У тендера нет корректной ссылки ЭТП. Измените её в исходной карточке."
                )
        except (OSError, ValueError) as error:
            self.error(error)

    def edit_selected_specification(self):
        identifier = self.execution.current_specification_id
        if identifier is not None:
            self.edit_execution_specification(identifier)

    def edit_execution_specification(self, identifier):
        self.edit_specification(
            next(s for s in self.repository.specifications() if s.id == identifier)
        )

    def archive_specification(self, identifier, archived):
        if not self.guard_changes():
            return
        try:
            self.repository.set_archived(identifier, archived)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.error(error)
            return
        self.refresh_execution()
        self.statusBar().showMessage(
            (
                ("Спецификация перенесена в «Поставлены»." if self.pages.currentIndex() == 3
                 else "Спецификация перенесена в архив.")
                if archived
                else "Спецификация возвращена в работу."
            ),
            5000,
        )

    def edit_position_field(self, spec_id, position_id, field):
        if not self.guard_changes():
            return
        spec = next(s for s in self.repository.specifications() if s.id == spec_id)
        position = next(p for p in spec.positions if p.id == position_id)
        dialog = PositionQuickEditDialog(position, field, spec.revised or spec.due, self)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                changed = self.repository.update_position_field(
                    spec_id, position_id, field, getattr(dialog.position, field))
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                self.error(error)
                return
            if changed:
                self.refresh_execution()
                self.statusBar().showMessage("Изменение позиции сохранено", 5000)

        finally:
            dialog.deleteLater()

    def edit_position_comment(self, spec_id, position_id):
        if not self.guard_changes():
            return
        dialog = PositionCommentsDialog(self.repository, spec_id, position_id, self)
        dialog.saved.connect(self.refresh_execution)
        dialog.saved.connect(lambda: self.statusBar().showMessage("Комментарии обновлены", 12000))
        try:
            dialog.exec()

        finally:
            dialog.deleteLater()

    def save_current(self):
        if hasattr(self, "execution_registry") and self.pages.currentIndex() == 3:
            return self.execution_registry.finish_edit()
        if hasattr(self, "pages") and self.pages.currentIndex() == 1:
            spec_id = self.execution.current_specification_id
            comment = self.execution.comment_drafts.get(spec_id, "")
            if spec_id is not None and comment.strip():
                return self.add_specification_comment(spec_id, comment)
            self.statusBar().showMessage("В выбранной спецификации нет несохранённого комментария", 5000)
            return True
        if hasattr(self, "pages") and (self.pages.currentIndex() == 2 or
                (self.pages.currentIndex() == 0 and self.registry_pages.currentWidget() == self.registry)):
            return True
        return super().save_current()

    def update_execution_save_state(self, *_):
        if not hasattr(self, "save_indicator") or self.pages.currentIndex() != 1:
            return
        spec_id = self.execution.current_specification_id
        draft = self.execution.comment_drafts.get(spec_id, "")
        self.save_indicator.setText("Есть несохранённый комментарий" if draft.strip() else "Без изменений")

    def mark_dirty(self, *args):
        super().mark_dirty(*args)
        if hasattr(self, "execution"):
            self.update_execution_save_state()

    def delete_specification_comment(self, spec_id, comment_id):
        if not confirm_comment_deletion(self):
            return
        try:
            self.repository.delete_execution_comment(spec_id, None, comment_id)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.error(error)
            return
        self.refresh_execution()
        self.execution.spec_comment_feed.show_feedback("Комментарий удалён")
        self.statusBar().showMessage("Комментарий удалён", 12000)

    def add_specification_comment(self, spec_id, comment):
        if not comment.strip():
            return True
        try:
            self.repository.add_specification_comment(spec_id, comment)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            if self.execution.current_specification_id == spec_id:
                self.execution.spec_comment_feed.show_feedback(
                    "Не сохранено. Комментарий остался в поле ввода.", error=True)
            self.error(error)
            return False
        self.execution.comment_drafts.pop(spec_id, None)
        self.refresh_execution()
        if self.execution.current_specification_id == spec_id:
            self.execution.spec_comment_feed.show_feedback("Комментарий добавлен")
            self.save_indicator.setText(f"Сохранено в {now_moscow():%H:%M} МСК")
        self.statusBar().showMessage("Комментарий добавлен", 12000)
        return True

    def complete_position_shipment(self, spec_id, position_id):
        if not self.guard_changes():
            return
        spec = next(s for s in self.repository.specifications() if s.id == spec_id)
        position = next(p for p in spec.positions if p.id == position_id)
        try:
            self.repository.set_position_shipped(spec_id, position_id, str(position.ordered))
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self.error(error)
            return
        self.refresh_execution()
        self.statusBar().showMessage("Всё отгружено заказчику · остаток 0", 4000)

    def edit_position_shipped(self, spec_id, position_id):
        if not self.guard_changes():
            return
        spec = next(s for s in self.repository.specifications() if s.id == spec_id)
        position = next(p for p in spec.positions if p.id == position_id)
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Отгрузка заказчику")
        dialog.setLabelText(
            f"{position.name}\nЗаказано: {position.ordered}\n\n"
            "Всего отгружено заказчику, включая предыдущие партии.\n"
            "Пусто — неизвестно; 0 — ещё не отгружали."
        )
        dialog.setTextValue(
            str(position.customer_shipped).replace(".", ",")
            if position.customer_shipped is not None
            else ""
        )
        dialog.setOkButtonText("Сохранить")
        dialog.setCancelButtonText("Отмена")
        dialog.setMinimumWidth(620)
        dialog.resize(720, 340)
        try:
            while dialog.exec() == QDialog.DialogCode.Accepted:
                try:
                    self.repository.set_position_shipped(
                        spec_id, position_id, dialog.textValue()
                    )
                except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                    self.error(error)
                    continue
                self.refresh_execution()
                self.statusBar().showMessage("Отгрузка сохранена · остаток пересчитан", 4000)
                return

        finally:
            dialog.deleteLater()


    def edit_specification(self, spec):
        registry_active = self.pages.currentIndex() == 3
        dialog = SpecificationDialog(self.repository, spec, self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.refresh_execution()
                if registry_active:
                    self.execution_registry.reveal_specification(dialog.specification.id)
                else:
                    self.open_specification(dialog.specification.id)
                self.statusBar().showMessage("Спецификация сохранена", 4000)

        finally:
            dialog.deleteLater()

    def edit_registry_position(self, spec_id, position_id):
        from tender_app.execution_editor import PositionDialog
        spec = next(s for s in self.repository.specifications() if s.id == spec_id)
        position = next(p for p in spec.positions if p.id == position_id)
        dialog = PositionDialog(position, self)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                spec.positions = [dialog.position if p.id == position_id else p for p in spec.positions]
                try:
                    self.repository.save_specification(spec)
                except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                    self.error(error)
                    return
                self.refresh_execution()
        finally:
            dialog.deleteLater()

    def export_execution(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Полный экспорт исполнения", "исполнение.json", "JSON (*.json)"
        )
        if path:
            try:
                self.repository.export_execution(path)
            except (OSError, ValueError) as error:
                self.error(error)

    def save_tender(self, *args, **kwargs):
        result = super().save_tender(*args, **kwargs)
        if result and hasattr(self, "execution"):
            self.refresh_execution()
        return result

    def restore_backup(self):
        super().restore_backup()
        self.refresh_execution()
