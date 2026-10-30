from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPushButton

from tender_app.models import Tender
from tender_app.registry_editor import RegistryCellDialog
from tender_app.tender_registry import REGISTRY_FIELDS, latest_comment
from tests.test_tender_registry import registry_window


@pytest.mark.parametrize("field,value", [
    ("number", "УЧЕБНЫЙ-НОВЫЙ"), ("customer", "Учебный заказчик"),
    ("title", "Новый предмет\nВторая строка"), ("platform", "Учебная площадка"),
    ("action", "Подготовить предложение"), ("status", "Податься"),
    ("comment", "Исправленный комментарий"), ("new_comment", "Новая запись"),
])
def test_edit_text_and_status_saves_only_selected_field(registry_window, monkeypatch, field, value):
    window = registry_window
    tender = window.repository.save(Tender(
        number="УЧЕБНЫЙ-1", comment="Старая заметка\n\n[27.10.2026 12:00 МСК]\nПоследний",
        status="В работе", deadline_date="2027-01-01", deadline_time="15:00",
    ))
    window.repository.connection.execute("UPDATE tenders SET is_unseen=1 WHERE id=?", (tender.id,))
    window.repository.connection.commit()
    window.load_tender(tender)

    def edit(dialog):
        if field == "status":
            dialog.editor.setCurrentText(value)
        elif hasattr(dialog.editor, "setPlainText"):
            dialog.editor.setPlainText(value)
        else:
            dialog.editor.setText(value)
        dialog.save_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        return dialog.result()

    monkeypatch.setattr(RegistryCellDialog, "exec", edit)
    window.edit_registry_cell(tender.id, field)
    saved = next(t for t in window.repository.all() if t.id == tender.id)
    assert window.current == saved
    changed_field = "comment" if field == "new_comment" else field
    assert (latest_comment(saved.comment) if changed_field == "comment" else getattr(saved, field)) == value
    for name in tender.values():
        if name not in (changed_field, "updated_at"):
            assert getattr(saved, name) == getattr(tender, name)
    if changed_field == "comment":
        assert saved.comment.startswith("Старая заметка\n\n[27.10.2026 12:00 МСК]\n")
        if field == "new_comment":
            assert "\nПоследний\n\n[" in saved.comment
    assert tender.id in window.repository.unseen_tender_ids()
    assert window.pages.currentIndex() == 0
    assert window.registry_pages.currentWidget() == window.registry


def test_deadline_validation_and_clearing(registry_window, monkeypatch):
    window = registry_window
    tender = window.repository.all()[0]

    def edit(dialog):
        dialog.date.setText("2027-01-12")
        dialog.time.setText("25:00")
        dialog.save_button.click()
        assert "Не сохранено" in dialog.error_label.text()
        assert dialog.time.text() == "25:00"
        dialog.time.setText("09:30")
        dialog.date.clear()
        dialog.save_button.click()
        assert dialog.result() != QDialog.DialogCode.Accepted
        dialog.date.setText("2027-02-31")
        dialog.save_button.click()
        assert dialog.result() != QDialog.DialogCode.Accepted
        dialog.date.setText("2027-01-12")
        dialog.save_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted
        return dialog.result()

    monkeypatch.setattr(RegistryCellDialog, "exec", edit)
    window.edit_registry_cell(tender.id, "deadline")
    saved = next(t for t in window.repository.all() if t.id == tender.id)
    assert (saved.deadline_date, saved.deadline_time, saved.deadline_timezone) == ("2027-01-12", "09:30", "Europe/Moscow")
    dialog = RegistryCellDialog(saved, "deadline", window)
    dialog.time.clear()
    assert dialog.changes()["deadline_time"] is None
    dialog.date.clear()
    assert dialog.changes()["deadline_date"] is None
    assert dialog.changes()["deadline_timezone"] is None


def test_cancel_and_noop_do_not_write(registry_window, monkeypatch):
    window = registry_window
    tender = window.repository.all()[0]
    writes = []
    monkeypatch.setattr(window.repository, "save", lambda *_: writes.append(True))
    monkeypatch.setattr(RegistryCellDialog, "exec", lambda dialog: dialog.reject())
    window.edit_registry_cell(tender.id, "customer")
    monkeypatch.setattr(RegistryCellDialog, "exec", lambda dialog: dialog.save_button.click())
    window.edit_registry_cell(tender.id, "customer")
    assert not writes


def test_failed_save_keeps_input(registry_window, monkeypatch):
    window = registry_window

    def fail(*_):
        raise OSError("Тестовая ошибка записи")

    def edit(dialog):
        dialog.editor.setText("Несохранённый заказчик")
        dialog.save_button.click()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.editor.text() == "Несохранённый заказчик"
        assert "Тестовая ошибка" in dialog.error_label.text()
        dialog.reject()

    monkeypatch.setattr(window.repository, "save", fail)
    monkeypatch.setattr(RegistryCellDialog, "exec", edit)
    window.edit_registry_cell(window.repository.all()[0].id, "customer")


def test_concurrent_edits_preserve_other_fields_and_reject_stale_cell(registry_window, monkeypatch):
    window = registry_window
    tender = window.repository.all()[0]

    def edit(dialog):
        window.repository.save(replace(tender, action="Параллельная правка"))
        dialog.editor.setText("Новый заказчик")
        dialog.save_button.click()
        assert dialog.result() == QDialog.DialogCode.Accepted

    monkeypatch.setattr(RegistryCellDialog, "exec", edit)
    window.edit_registry_cell(tender.id, "customer")
    saved = next(t for t in window.repository.all() if t.id == tender.id)
    assert saved.action == "Параллельная правка"

    def stale(dialog):
        window.repository.save(replace(saved, customer="Другой заказчик"))
        dialog.editor.setText("Устаревшая правка")
        dialog.save_button.click()
        assert "уже изменилась" in dialog.error_label.text()
        assert dialog.result() != QDialog.DialogCode.Accepted
        dialog.reject()

    monkeypatch.setattr(RegistryCellDialog, "exec", stale)
    window.edit_registry_cell(tender.id, "customer")
    assert next(t for t in window.repository.all() if t.id == tender.id).customer == "Другой заказчик"


def test_right_click_edit_targets_cell(registry_window, app, monkeypatch):
    page = registry_window.registry
    page.status_filter.setCurrentIndex(1)
    received = []
    page.edit_requested.disconnect()
    page.edit_requested.connect(lambda key, field: received.append((key, field)))
    app.processEvents()
    for column, field in enumerate(REGISTRY_FIELDS):
        page.table.scrollToItem(page.table.item(0, column))
        item = page.table.item(0, column)
        page.table.customContextMenuRequested.emit(page.table.visualItemRect(item).center())
        if column == 0:
            assert not received
        else:
            assert received[-1] == (item.data(Qt.ItemDataRole.UserRole), field)
    assert len(received) == len(REGISTRY_FIELDS) - 1
    assert registry_window.registry_pages.currentWidget() == page


def test_unchanged_multiline_and_seconds_do_not_rewrite_source(app):
    tender = Tender(number="УЧЕБНЫЙ", title="Первая\r\nВторая", deadline_date="2027-01-01",
                    deadline_time="12:00:30")
    text = RegistryCellDialog(tender, "title")
    assert text.changes() == {}
    clock = RegistryCellDialog(tender, "deadline")
    assert clock.changes() == {}


def test_new_comment_is_available_inside_direct_editor(app):
    tender = Tender(number="УЧЕБНЫЙ", comment="Прежний комментарий")
    dialog = RegistryCellDialog(tender, "comment")
    add = next(button for button in dialog.findChildren(QPushButton)
               if button.text() == "Добавить новый комментарий")
    add.click()
    dialog.editor.setPlainText("Новая запись")
    assert dialog.field == "new_comment"
    assert dialog.changes()["comment"].startswith("Прежний комментарий\n\n[")
    assert dialog.changes()["comment"].endswith("\nНовая запись")
