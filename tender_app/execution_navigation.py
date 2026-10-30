"""Page transitions and ownership of the shared tender, execution and agent widgets."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

class ExecutionNavigation:
    def open_search_position(self, spec_id, position_id):
        if self.open_specification(spec_id, position_id=position_id) is False:
            return
        QTimer.singleShot(0, self.execution, lambda: self.reveal_search_position(spec_id, position_id))

    def reveal_search_position(self, spec_id, position_id):
        panel = self.execution
        if panel.current_specification_id != spec_id:
            return
        for table in panel.position_tables:
            for row, position in enumerate(table.positions):
                if position['id'] == position_id:
                    if table.isRowHidden(row):
                        panel.position_filter.setCurrentIndex(0)
                    table.setCurrentCell(row, 0)
                    table.selectRow(row)
                    point = table.viewport().mapTo(panel.card, table.visualItemRect(table.item(row, 0)).center())
                    panel.scroll.ensureVisible(0, point.y())
                    return

    def toggle_agent_dock(self):
        if self.agent_dock.isVisible():
            self.agent_dock.hide()
            return
        if self.pages.currentIndex() == 2:
            self.pages.setCurrentIndex(self.agent_return_page)
            if self.pages.currentIndex() == 2:
                return
        self.agent_dock_focus = QApplication.focusWidget()
        self.agent_dock.setWidget(self.agent_page)
        self.agent_page.set_compact(True)
        self.agent_dock.show()
        self.agent_page.show()
        height = min(max(260, self.settings.get("agent_dock_height", 340)), max(260, self.height() // 2))
        self.resizeDocks([self.agent_dock], [height], Qt.Orientation.Vertical)
        self.agent_page.chat.input.setFocus()

    def agent_dock_visibility_changed(self, visible):
        # Parent minimization/hiding also emits False; only an explicitly hidden
        # dock should return the shared chat to its full-page home.
        if visible or not self.agent_dock.isHidden() or self.agent_page.parentWidget() != self.agent_dock:
            return
        self.settings.values["agent_dock_height"] = self.agent_dock.height()
        self.agent_home_layout.addWidget(self.agent_page)
        self.agent_page.set_compact(False)
        self.agent_page.show()
        try:
            if self.agent_dock_focus is not None and self.agent_dock_focus.isVisible():
                self.agent_dock_focus.setFocus()
        except RuntimeError:
            pass

    def change_page(self, index):
        if hasattr(self, "execution_registry") and not self.execution_registry.finish_edit():
            self.pages.blockSignals(True)
            self.pages.setCurrentIndex(self.last_page)
            self.pages.blockSignals(False)
            return
        if not self.guard_changes():
            self.pages.blockSignals(True)
            self.pages.setCurrentIndex(self.last_page)
            self.pages.blockSignals(False)
            return
        previous = self.last_page
        if index == 2 and hasattr(self, "agent_dock"):
            self.agent_dock.hide()
        elif index != 2:
            self.agent_return_page = index
        if (index == 0 or previous == 0) and self.dirty and self.current:
            saved = next((t for t in self.repository.all() if t.id == self.current.id), None)
            if saved:
                self.load_tender(saved)
            else:
                self.current = None
                self.dirty = False
                self.note.clear()
                self.card.setEnabled(False)
        self.last_page = index
        self.save_action.setEnabled(index == 1 or (index == 0 and self.registry_pages.currentWidget() == self.registry_card_page))
        self.assistant_dock.hide()
        if index == 0:
            self.refresh_registry()
        elif index == 1:
            self.refresh_execution()
            self.update_execution_save_state()
            QTimer.singleShot(0, self, self.adapt_execution_panels)
        elif index == 2:
            QTimer.singleShot(0, self, self.agent_page.chat.input.setFocus)
        elif index == 3:
            self.refresh_execution()
            self.save_action.setEnabled(True)
            self.save_indicator.setText("Изменения сохраняются по Enter")
        elif hasattr(self, "save_indicator"):
            self.save_indicator.setText("Есть несохранённые изменения" if self.dirty else "Без изменений")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "execution_assistant"):
            QTimer.singleShot(0, self, self.adapt_execution_panels)

    def adapt_execution_panels(self):
        if not hasattr(self, "execution_assistant_toggle"):
            return
        if self.pages.currentIndex() != 1:
            return
        registry = self.execution.splitter.widget(0)
        needs_space = (
            self.execution_assistant_toggle.isChecked()
            and self.execution.splitter.width() < 1280
        )
        # Without the classic tender page the window can now become narrower.
        # Give the execution card room before it needs horizontal scrolling.
        minimum = (registry.minimumWidth() + self.execution.card.minimumSizeHint().width()
                   + self.execution.splitter.handleWidth()
                   + self.execution.scroll.verticalScrollBar().sizeHint().width()
                   + 2 * self.execution.scroll.frameWidth())
        needs_space = needs_space or self.execution.splitter.width() < minimum
        hidden_by_layout = getattr(self, "execution_registry_auto_hidden", False)
        if needs_space and not hidden_by_layout:
            self.execution_registry_auto_hidden = True
            registry.hide()
            self.execution.splitter.setSizes([0, max(400, self.width() - 500), 300])
        elif not needs_space and hidden_by_layout:
            self.execution_registry_auto_hidden = False
            registry.show()
            self.execution.splitter.setSizes(
                self.execution_assistant_sizes or [self.execution_registry_width, 650, 320]
            )
        button = getattr(self, "shell_list_button", None)
        if button is not None and self.pages.currentIndex() == 1:
            button.setChecked(not registry.isHidden())

    def toggle_execution_assistant(self, visible):
        if not visible:
            self.execution_assistant_sizes = self.execution.splitter.sizes()
        # Make room before showing the helper; otherwise Qt widens the window
        # and the narrow-window check sees the already expanded geometry.
        self.adapt_execution_panels()
        self.execution_assistant.setVisible(visible)
        if visible:
            self.execution.splitter.setSizes(
                self.execution_assistant_sizes or [self.execution_registry_width, 650, 360]
            )
            self.execution_assistant.update_context()
        self.adapt_execution_panels()

    def open_execution_tender(self, tender_id):
        return self.open_registry_tender(tender_id)

    def open_registry_tender(self, tender_id):
        if not self.guard_changes():
            return False
        tender = next((t for t in self.repository.all() if t.id == tender_id), None)
        if tender is None:
            self.refresh_registry()
            return False
        self.load_tender(tender)
        self.pages.setCurrentIndex(0)
        self.show_registry_card()
        self.mark_opened_tender_seen(tender_id)
        self.refresh_list()
        return True

    def show_registry_card(self):
        self.registry_pages.setCurrentWidget(self.registry_card_page)
        self.registry_back.show()
        self.card.show()
        self.save_action.setEnabled(True)
        self.sync_shell_page()
        self.scroll.verticalScrollBar().setValue(0)

    def return_to_registry(self):
        if not self.guard_changes():
            return
        if self.dirty and self.current:
            saved = next((t for t in self.repository.all() if t.id == self.current.id), None)
            if saved:
                self.load_tender(saved)
            else:
                self.current = None
                self.dirty = False
                self.note.clear()
                self.unsaved.hide()
                self.card.setEnabled(False)
                self.update_save_state()
        self.registry_pages.setCurrentWidget(self.registry)
        self.registry_back.hide()
        self.refresh_registry()
        self.save_action.setEnabled(False)
        self.sync_shell_page()

    def open_specification(self, spec_id, position_id=None):
        if not self.guard_changes():
            return False
        if self.pages.currentIndex() == 3 and position_id is None:
            self.refresh_execution()
            self.execution_registry.reveal_specification(spec_id)
            return
        self.pages.setCurrentIndex(1)
        self.refresh_execution()
        self.execution.open_specification(spec_id)
        self.execution_assistant.update_context()
        self.update_execution_save_state()
