from dataclasses import replace

from PySide6.QtWidgets import QDialog, QToolButton, QMessageBox
from tender_app.registry_editor import RegistryCellDialog
from tests.test_execution_window import execution_window


def test_contacts_open_large_editor_and_save_only_contacts(execution_window, app, monkeypatch):
    win = execution_window
    spec = win.repository.specifications()[0]
    before = next(t for t in win.repository.all() if t.id == spec.tender_id)
    win.open_specification(spec.id)
    text = "\n\n".join(f"Учебный контакт {n}\ncontact-{n}@example.invalid" for n in range(50))

    def edit(dialog):
        assert dialog.field == "contacts"
        assert dialog.width() >= min(920, dialog.screen().availableGeometry().width() - 60)
        assert dialog.height() >= 500
        assert dialog.editor.toPlainText() == before.contacts
        dialog.editor.setPlainText(text)
        dialog.save_button.click()
        assert not dialog.error_label.text()
        assert dialog.result() == QDialog.DialogCode.Accepted
        return dialog.result()

    monkeypatch.setattr(RegistryCellDialog, "exec", edit)
    button = next(b for b in win.execution.spec_header.findChildren(QToolButton)
                  if b.accessibleName() == "Открыть и изменить контакты")
    button.click()
    app.processEvents()
    after = next(t for t in win.repository.all() if t.id == spec.tender_id)
    assert replace(after, contacts=before.contacts, updated_at=before.updated_at) == before
    assert after.contacts == text
    assert win.execution.customer_contacts.toPlainText() == text
    assert win.execution.current_specification_id == spec.id


def test_contact_editor_cancel_and_write_failure_preserve_data(execution_window, monkeypatch):
    win = execution_window
    spec = win.repository.specifications()[0]
    before = win.repository.all()
    monkeypatch.setattr(RegistryCellDialog, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    win.edit_registry_cell(spec.tender_id, "contacts")
    assert win.repository.all() == before

    def fail_save(*args):
        raise OSError("Учебная ошибка записи")

    def edit(dialog):
        dialog.editor.setPlainText("Новый контакт")
        dialog.save_button.click()
        assert "Не сохранено" in dialog.error_label.text()
        assert dialog.editor.toPlainText() == "Новый контакт"
        assert dialog.result() != QDialog.DialogCode.Accepted
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(win.repository, "save", fail_save)
    monkeypatch.setattr(RegistryCellDialog, "exec", edit)
    win.edit_registry_cell(spec.tender_id, "contacts")
    assert win.repository.all() == before


def test_contact_dialog_closing_protects_unsaved_changes(execution_window, monkeypatch):
    dialog = RegistryCellDialog(execution_window.repository.all()[0], "contacts", execution_window)
    try:
        dialog.editor.setPlainText("Несохранённый контакт")
        rejected = []
        dialog.rejected.connect(lambda: rejected.append(True))
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
        dialog.reject()
        assert not rejected and dialog.editor.toPlainText() == "Несохранённый контакт"
        saved = []
        dialog.save_button.clicked.connect(lambda: saved.append(True))
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
        dialog.reject()
        assert saved and not rejected
        monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
        dialog.reject()
        assert rejected
    finally:
        dialog.deleteLater()
