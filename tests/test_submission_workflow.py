from pathlib import Path
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tender_app.models import Tender
from tender_app.registry_editor import RegistryCellDialog
from tender_app.tender_registry import DeadlineDelegate, TenderRegistry
from tests.test_tender_registry import registry_window


@pytest.mark.parametrize(('day', 'clock', 'expected'), [
    ('28.10.2026', '15:00', '28.10.2026 15:00'),
    ('28.10.2026', '00:00', '28.10.2026 00:00'),
    ('28.10.2026', '', '28.10.2026'),
    ('', '', ''), ('31.02.2026', '15:00', ''),
    ('28.10.2026', '25:00', ''), ('', '15:00', ''),
])
def test_card_copies_current_fields_without_saving(registry_window, monkeypatch, day, clock, expected):
    window = registry_window
    window.open_registry_tender(window.repository.all()[0].id)
    original = window.current
    copied = []
    class Clipboard:
        def setText(self, text):
            copied.append(text)
    monkeypatch.setattr(QApplication, 'clipboard', lambda: Clipboard())
    window.date.setText(day)
    window.time.setText(clock)
    assert window.deadline_copy.isEnabled() == bool(expected)
    window.deadline_copy.click()
    assert copied == ([expected] if expected else [])
    assert next(t for t in window.repository.all() if t.id == original.id) == original
    assert window.current == original


def test_registry_click_edits_but_copy_never_opens_editor(app, monkeypatch):
    page = TenderRegistry()
    page.set_tenders([Tender(id=1, number='TEST', deadline_date='2026-10-28', deadline_time='15:00'),
                      Tender(id=2, number='EMPTY')])
    page.resize(1200, 450)
    page.show()
    edited, opened, copied = [], [], []
    page.edit_requested.connect(lambda *args: edited.append(args))
    page.tender_requested.connect(opened.append)
    class Clipboard:
        def setText(self, text):
            copied.append(text)
    monkeypatch.setattr(QApplication, 'clipboard', lambda: Clipboard())
    try:
        app.processEvents()
        item = page.table.item(0, 3)
        rect = page.table.visualItemRect(item)
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=DeadlineDelegate.copy_rect(rect).center())
        assert copied == ['28.10.2026 15:00'] and not edited and not opened
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        app.processEvents()
        assert edited == [(1, 'deadline')] and not opened
        empty = page.table.item(1, 3)
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=DeadlineDelegate.copy_rect(page.table.visualItemRect(empty)).center())
        assert copied == ['28.10.2026 15:00'] and edited == [(1, 'deadline')]
        # Sorting and filtering keep the copy data attached to the correct procedure.
        page.search.setText('TEST')
        item = page.table.item(0, 3)
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=DeadlineDelegate.copy_rect(page.table.visualItemRect(item)).center())
        assert copied == ['28.10.2026 15:00'] * 2
    finally:
        page.close()


def test_registry_deadline_cancel_preserves_source(registry_window, monkeypatch):
    window = registry_window
    original = window.repository.all()[0]
    def cancel(dialog):
        assert dialog.date.text() == dialog.date.date().toString('dd.MM.yyyy')
        dialog.date.setText('28.10.2026')
        dialog.time.setText('15:00')
        dialog.reject()
        return dialog.result()
    monkeypatch.setattr(RegistryCellDialog, 'exec', cancel)
    window.edit_registry_cell(original.id, 'deadline')
    assert next(t for t in window.repository.all() if t.id == original.id) == original


def test_registry_click_saves_and_rebuilds_table_after_mouse_event(registry_window, monkeypatch, app):
    window = registry_window
    page = window.registry
    page.status_filter.setCurrentIndex(1)
    app.processEvents()
    original_id = page.table.item(0, 3).data(Qt.ItemDataRole.UserRole)
    def save(dialog):
        dialog.date.setText('28.10.2026')
        dialog.time.setText('15:00')
        dialog.save_button.click()
        return dialog.result()
    monkeypatch.setattr(RegistryCellDialog, 'exec', save)
    rect = page.table.visualItemRect(page.table.item(0, 3))
    QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    app.processEvents()
    saved = next(t for t in window.repository.all() if t.id == original_id)
    assert (saved.deadline_date, saved.deadline_time) == ('2026-10-28', '15:00')
    assert window.registry_pages.currentWidget() == page


def test_manual_creation_prepares_kit_once_and_renumber_keeps_folder(registry_window):
    window = registry_window
    window.new_tender()
    window.edits['number'].setText('MANUAL/001')
    assert window.save_current()
    folder = Path(window.current.folder_path)
    assert folder == window.documents_root / 'MANUAL_001'
    assert (folder / 'С ЭТП').is_dir()
    request = folder / 'Запрос_MANUAL_001.xlsx'
    assert request.is_file()
    request.write_bytes(b'user-edited workbook')
    window.edits['number'].setText('RENAMED')
    assert window.save_current()
    assert Path(window.current.folder_path) == folder
    assert request.read_bytes() == b'user-edited workbook'
    assert not (window.documents_root / 'RENAMED').exists()


@pytest.mark.parametrize('custom', [False, True])
def test_manual_save_failure_removes_only_its_kit(registry_window, monkeypatch, tmp_path, custom):
    window = registry_window
    window.new_tender()
    window.edits['number'].setText('ROLLBACK')
    if custom:
        folder = tmp_path / 'chosen'
        folder.mkdir()
        preserved = folder / 'user.txt'
        preserved.write_text('keep', encoding='utf-8')
        window.edits['folder_path'].setText(str(folder))
    else:
        folder = window.documents_root / 'ROLLBACK'
    before = window.repository.all()
    def fail(_):
        raise sqlite3.OperationalError('test write failure')
    monkeypatch.setattr(window.repository, 'save', fail)
    monkeypatch.setattr(window, 'error', lambda _: None)
    assert not window.save_current()
    assert window.repository.all() == before
    assert window.current.id is None and window.edits['number'].text() == 'ROLLBACK'
    assert not (folder / 'Запрос_ROLLBACK.xlsx').exists()
    assert not (folder / 'С ЭТП').exists()
    assert folder.exists() == custom
    if custom:
        assert preserved.read_text(encoding='utf-8') == 'keep'


def test_manual_chosen_folder_preserves_existing_request(registry_window, tmp_path):
    window = registry_window
    folder = tmp_path / 'chosen'
    folder.mkdir()
    request = folder / 'Запрос_EXISTING.xlsx'
    request.write_bytes(b'user workbook')
    window.new_tender()
    window.edits['number'].setText('EXISTING')
    window.edits['folder_path'].setText(str(folder))
    assert window.save_current()
    assert Path(window.current.folder_path) == folder
    assert (folder / 'С ЭТП').is_dir()
    assert request.read_bytes() == b'user workbook'


def test_invalid_date_and_time_without_date_keep_draft(registry_window, monkeypatch):
    window = registry_window
    window.new_tender()
    window.edits['number'].setText('INVALID')
    window.date.setText('31.02.2026')
    window.time.setText('12:00')
    monkeypatch.setattr(window, 'error', lambda _: None)
    assert not window.save_current()
    window.date.clear()
    assert not window.save_current()
    assert window.time.text() == '12:00'
    assert not (window.documents_root / 'INVALID').exists()
