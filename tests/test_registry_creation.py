from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QListWidget

from tender_app.import_dialog import Import1CDialog
from tests.test_tender_registry import registry_window
from tests.test_import_1c_ui import owner, show, wait, yes
from tests.test_import_1c import line


def test_new_tender_button_opens_and_saves_full_card(registry_window, app):
    window = registry_window
    before = len(window.repository.all())
    window.registry.new_button.click()
    app.processEvents()
    assert window.pages.currentIndex() == 0
    assert window.registry_pages.currentWidget() == window.registry_card_page
    assert window.current.id is None
    assert window.dirty and window.identity_editor.isVisible()
    assert window.edits["number"].hasFocus()
    assert not window.registry_panel.isVisible()
    window.edits["number"].setText("УЧЕБНЫЙ-РУЧНОЙ")
    assert window.save_current()
    window.registry_back.click()
    assert len(window.repository.all()) == before + 1
    assert window.registry_pages.currentWidget() == window.registry
    assert any(window.registry.table.item(r, 0).text() == "УЧЕБНЫЙ-РУЧНОЙ"
               for r in range(window.registry.table.rowCount()))


def test_cancel_creation_and_discard_draft(registry_window, monkeypatch):
    window = registry_window
    before = window.repository.all()
    monkeypatch.setattr(window, "guard_changes", lambda: False)
    window.registry.new_button.click()
    assert window.registry_pages.currentWidget() == window.registry
    monkeypatch.setattr(window, "guard_changes", lambda: True)
    window.registry.new_button.click()
    window.registry_back.click()
    assert not window.dirty
    assert window.current is None
    assert window.repository.all() == before


def test_import_button_uses_existing_import_dialog(registry_window, monkeypatch):
    seen = []
    monkeypatch.setattr(Import1CDialog, "read_clipboard", lambda dialog: seen.append(dialog.owner))
    monkeypatch.setattr(Import1CDialog, "exec", lambda dialog: dialog.reject())
    registry_window.registry.import_button.click()
    assert seen == [registry_window]
    assert registry_window.pages.currentIndex() == 0


def test_import_updates_registry_and_opens_added_card_here(owner, app, monkeypatch):
    owner.pages.setCurrentIndex(0)
    dialog = show(owner, line("УЧЕБНЫЙ-ИМПОРТ"), app)
    yes(monkeypatch)
    dialog.confirm_import()
    wait(dialog, app)
    assert len(dialog.added_ids) == 1
    identifier = dialog.added_ids[0]
    imported_item = next(owner.registry.table.item(r, 0) for r in range(owner.registry.table.rowCount())
                         if owner.registry.table.item(r, 0).data(Qt.ItemDataRole.UserRole) == identifier)
    assert imported_item.data(Qt.ItemDataRole.UserRole + 3)
    assert any(owner.registry.table.item(r, 0).text() == "УЧЕБНЫЙ-ИМПОРТ"
               for r in range(owner.registry.table.rowCount()))

    def choose_added():
        picker = QApplication.activeModalWidget()
        picker.findChild(QListWidget).setCurrentRow(0)
        picker.accept()

    QTimer.singleShot(0, choose_added)
    dialog.open_results()
    assert owner.pages.currentIndex() == 0
    assert owner.registry_pages.currentWidget() == owner.registry_card_page
    assert owner.current.id == dialog.added_ids[0]
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_create_from_execution_uses_registry_and_guards_once(registry_window, monkeypatch):
    window = registry_window
    window.pages.setCurrentIndex(1)
    window.note.setPlainText("Черновик для отмены")
    calls = []
    def discard():
        if window.dirty:
            calls.append(True)
        return True
    monkeypatch.setattr(window, 'guard_changes', discard)
    window.new_tender()
    assert calls == [True]
    assert window.pages.currentIndex() == 0
    assert window.registry_pages.currentWidget() == window.registry_card_page
    assert window.current.id is None and window.dirty
