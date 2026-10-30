"""Guard application shutdown and persist execution/chat view state."""
from PySide6.QtWidgets import QMessageBox

class ExecutionLifecycle:
    def closeEvent(self, event):
        if hasattr(self, "execution_registry") and not self.execution_registry.finish_edit():
            event.ignore()
            return
        job = getattr(self, 'resource_job', None)
        if job is not None:
            super().closeEvent(event)
            return
        if getattr(self, "import_busy", False):
            event.ignore()
            return
        if hasattr(self, "ai_service") and self.ai_service.busy():
            for job in self.ai_service.jobs:
                job.stop()
            QMessageBox.information(self, "Завершение запроса", "Запрос останавливается. Дождитесь завершения или сетевого таймаута, затем закройте программу. Изменения автоматически не применяются.")
            event.ignore()
            return
        if self.execution.comment_drafts:
            answer = QMessageBox.question(
                self,
                "Черновики комментариев",
                "Сохранить набранные комментарии по спецификациям?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.StandardButton.Save:
                for spec_id, text in list(self.execution.comment_drafts.items()):
                    self.add_specification_comment(spec_id, text)
                    if spec_id in self.execution.comment_drafts:
                        event.ignore()
                        return
            else:
                self.execution.comment_drafts.clear()
        if not self.agent_page.flush():
            answer = QMessageBox.question(self, "История чата не сохранена", "Не удалось сохранить историю и черновик агента. Закрыть приложение без их сохранения?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.settings.values["execution_navigation"] = self.execution.navigation_preferences()
        self.settings.values["execution_registry"] = self.execution_registry.preferences()
        if self.agent_dock.isVisible():
            self.settings.values["agent_dock_height"] = self.agent_dock.height()
        self.settings.values["execution_registry_width"] = self.execution_registry_width
        self.settings.values["execution_compact_position_widths"] = (
            self.execution.position_column_widths
        )
        self.settings.values["execution_comment_input_height"] = (
            self.execution.comment_input_height
        )
        super().closeEvent(event)
