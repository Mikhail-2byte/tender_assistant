from dataclasses import replace
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QMessageBox, QInputDialog

from tender_app.execution_editor import PositionDialog, SpecificationDialog
from tender_app.execution_models import Position, Specification
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.settings import Settings


@pytest.fixture
def execution_window(tmp_path, app):
    path = tmp_path / "Данные" / "tenders.db"
    create_demo_database(path)
    repo = ExecutionRepository(path)
    window = ExecutionWindow(repo, Settings(tmp_path / "settings.json"), tmp_path)
    window.show()
    app.processEvents()
    yield window
    window.dirty = False
    window.close()
    repo.close()
    app.processEvents()


def test_position_section_does_not_expand_into_empty_space(execution_window, app):
    window = execution_window
    window.open_specification(window.repository.specifications()[0].id)
    for height in (740, 1000):
        window.resize(1320, height)
        for _ in range(8):
            app.processEvents()
        table = window.execution.position_tables[0]
        section = table.parentWidget()
        assert table.y() < 110
        assert section.height() - table.height() < 140


def test_won_card_has_positions_and_contract_terms(execution_window, app, monkeypatch):
    from PySide6.QtWidgets import QLabel
    from tender_app.execution_positions import PositionTable
    window = execution_window
    spec = window.repository.specifications()[0]
    window.open_registry_tender(spec.tender_id)
    app.processEvents()
    assert window.identity_summary.isVisible()
    assert window.spec_section.isVisible()
    labels = "\n".join(label.text() for label in window.spec_section.findChildren(QLabel))
    assert "Позиции и спецификации" in labels
    assert spec.contract in labels and spec.number in labels
    assert "Срок поставки" in labels
    table = window.spec_section.findChildren(PositionTable)[0]
    assert table.rowCount() == len(spec.positions)
    called = []
    monkeypatch.setattr(window, "edit_position_field", lambda *args: called.append(args))
    table.cellDoubleClicked.emit(0, 8)
    assert called == [(spec.id, spec.positions[0].id, "transport")]
    window.status.setCurrentText("В работе")
    # Saved execution remains accessible when the tender result changes.
    assert window.spec_section.isVisible()


def test_application_quick_edit_is_saved_and_refreshed_in_won_card(execution_window, app, monkeypatch):
    from tender_app.execution_editor import PositionQuickEditDialog
    from tender_app.execution_positions import PositionTable
    window = execution_window
    spec = window.repository.specifications()[0]
    window.open_registry_tender(spec.tender_id)

    def enter(dialog):
        dialog.edits["supplier_document"].setText("Приложение 76")
        assert dialog.save()
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(PositionQuickEditDialog, "exec", enter)
    table = window.spec_section.findChildren(PositionTable)[0]
    table.cellDoubleClicked.emit(0, 4)
    assert window.repository.specifications()[0].positions[0].supplier_document == "Приложение 76"
    app.processEvents()
    tables = [t for t in window.spec_section.findChildren(PositionTable) if not t.isHidden()]
    assert tables[0].item(0, 4).text() == "76"


def test_unified_comment_preserves_old_note_and_shows_latest_entry(execution_window):
    from tender_app.execution_comments import PositionCommentsDialog
    from tender_app.execution_positions import PositionTable
    from tender_app.execution_window import specification_view
    window = execution_window
    spec = window.repository.specifications()[0]
    position = spec.positions[0]
    position.working_note = "Старая заметка, которую нельзя потерять"
    window.repository.save_specification(spec)
    table = PositionTable(specification_view(spec)["positions"], False)
    try:
        assert table.columnCount() == 9
        assert table.item(0, 7).text() == position.working_note
    finally:
        table.close()
    dialog = PositionCommentsDialog(window.repository, spec.id, position.id, window)
    try:
        assert dialog.visible_comments(dialog.position())[-1][1] == position.working_note
        for text in ("Подготовили документы", "АВТО 76 выехало"):
            dialog.feed.edit.setPlainText(text)
            assert dialog.save_comment()
        saved = dialog.position()
        assert saved.working_note == position.working_note
        assert [text for _, text in saved.comments[:2]] == ["АВТО 76 выехало", "Подготовили документы"]
        assert len(dialog.feed.comment_ids) == len(saved.comments)
        assert len(dialog.visible_comments(saved)) == len(saved.comments) + 1
        table = PositionTable(specification_view(window.repository.specifications()[0])["positions"], False)
        try:
            assert table.item(0, 7).text().startswith("АВТО 76 выехало")
            assert "Старая заметка" not in table.item(0, 7).text()
        finally:
            table.close()
        editor = PositionDialog(saved, window)
        try:
            assert "working_note" not in editor.edits
            assert editor.save()
            assert editor.position.working_note == position.working_note
        finally:
            editor.deleteLater()
    finally:
        dialog.deleteLater()


def test_execution_assistant_reopen_keeps_width_and_draft(execution_window, app):
    window = execution_window
    window.pages.setCurrentIndex(1)
    window.resize(1500, 900)
    window.execution_assistant_toggle.setChecked(True)
    app.processEvents()
    window.execution.splitter.setSizes([330, 600, 460])
    app.processEvents()
    original = window.execution.splitter.sizes()
    window.execution_assistant.input.setPlainText("Несохранённый вопрос")
    window.execution_assistant_toggle.setChecked(False)
    app.processEvents()
    window.execution_assistant_toggle.setChecked(True)
    app.processEvents()
    assert all(abs(a - b) <= 2 for a, b in zip(original, window.execution.splitter.sizes()))
    assert window.execution_assistant.input.toPlainText() == "Несохранённый вопрос"


def test_execution_chat_hides_deadline_button_and_sends_with_enter(execution_window, app):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel
    window = execution_window
    spec = window.repository.specifications()[0]
    window.open_specification(spec.id)
    window.execution_assistant_toggle.setChecked(True)
    panel = window.execution_assistant
    app.processEvents()
    assert panel.today_button.isHidden()
    original = window.repository.specifications()
    panel.input.setPlainText("Сводка")
    panel.input.setFocus()
    QTest.keyClick(panel.input, Qt.Key.Key_Return)
    assert panel.input.toPlainText() == ""
    assert any("Заказано" in label.text() for label in panel.findChildren(QLabel))
    assert window.repository.specifications() == original


def test_winner_without_specs_then_two_specs_and_duplicate_numbers(execution_window):
    window = execution_window
    window.open_execution_tender(3)
    assert window.current.id == 3
    assert window.spec_section.isVisible()
    assert len(window.repository.specifications()) == 4
    dialog = SpecificationDialog(window.repository, Specification(tender_id=3), window)
    dialog.edits["number"].setText("СП-05")
    dialog.specification.positions.append(Position(name="Барабан", ordered=Decimal("2"), placed=Decimal("2"), customer_shipped=Decimal("0")))
    assert dialog.save()
    saved = dialog.specification
    window.refresh_execution()
    assert window.execution.without_specifications == 0
    window.open_specification(saved.id)
    assert window.pages.currentIndex() == 1
    window.execution.open_tender(3)
    assert window.current.id == 3
    assert window.pages.currentIndex() == 0
    # Same printed tender number must keep links to the other tender separate.
    assert len([s for s in window.repository.specifications() if s.tender_id == 2]) == 2


def test_execution_assistant_uses_selected_spec_and_keeps_chat(execution_window, app):
    from PySide6.QtWidgets import QLabel, QPushButton
    window = execution_window
    original = window.repository.specifications()
    spec = next(s for s in original if not s.archived)
    window.open_specification(spec.id)
    window.execution_assistant_toggle.click()
    assistant = window.execution_assistant
    assert assistant.isVisible()
    assert spec.number in assistant.context.text()
    assistant.input.setPlainText("Остатки")
    assistant.send_button.click()
    bodies = [label.text() for label in assistant.findChildren(QLabel)]
    assert any(spec.positions[0].name in text and "отгружено" in text for text in bodies)
    count = assistant.messages.count()
    assistant.input.setPlainText("Черновик вопроса")
    window.pages.setCurrentIndex(0)
    window.pages.setCurrentIndex(1)
    assert assistant.isVisible()
    assert assistant.messages.count() == count
    assert assistant.input.toPlainText() == "Черновик вопроса"
    assistant.input.setPlainText("Найди " + spec.number)
    assistant.send_button.click()
    button = next(b for b in assistant.findChildren(QPushButton) if b.property("specificationId") == spec.id)
    button.click()
    assert window.execution.current_specification_id == spec.id
    assert window.repository.specifications() == original
    assistant.input.clear()
    app.processEvents()


def test_execution_assistant_tracks_selection_and_reports_unknown_quantities(execution_window):
    from tender_app.execution_assistant import ExecutionAssistant
    window = execution_window
    specs = window.repository.specifications()
    for spec in specs:
        window.open_specification(spec.id)
        assert spec.number in window.execution_assistant.context.text()
    spec = Specification(number="Проверка", positions=[Position(name="Вал", customer_shipped=None)])
    summary = ExecutionAssistant.summary(spec)
    assert "отгружено не указано" in summary
    assert "осталось не указано" in summary
    assert "Поставить до: не указано" in summary


def test_archive_action_and_return_use_separate_lists(execution_window, app):
    window = execution_window
    spec = next(s for s in window.repository.specifications() if not s.archived)
    window.open_specification(spec.id)
    window.execution.archive_button.click()
    app.processEvents()
    assert window.execution.state.currentText() == "В работе"
    assert spec.id not in {s["id"] for s in window.execution.visible_specifications}
    window.execution.state.setCurrentText("Архив")
    assert spec.id in {s["id"] for s in window.execution.visible_specifications}
    window.open_specification(spec.id)
    assert window.execution.archive_button.text() == "Вернуть в работу"
    window.execution.archive_button.click()
    app.processEvents()
    assert spec.id not in {s["id"] for s in window.execution.visible_specifications}
    window.execution.state.setCurrentText("В работе")
    assert spec.id in {s["id"] for s in window.execution.visible_specifications}
    returned = next(s for s in window.repository.specifications() if s.id == spec.id)
    assert returned.positions == spec.positions and returned.stage == spec.stage


def test_position_comment_dialog_appends_history_and_cancel_keeps_existing(execution_window, monkeypatch):
    from tender_app.execution_comments import PositionCommentsDialog
    window = execution_window
    spec = next(s for s in window.repository.specifications() if len(s.positions) == 3)
    window.open_specification(spec.id)
    position = spec.positions[1]
    def post(dialog):
        assert dialog.width() >= 700 and dialog.height() >= 500
        dialog.feed.edit.setPlainText("Ждём машину\nДокументы готовы")
        assert dialog.save_comment()
        dialog.feed.edit.setPlainText("Машина выехала")
        assert dialog.save_comment()
        return 0
    monkeypatch.setattr(PositionCommentsDialog, "exec", post)
    window.execution.position_tables[0].comment_requested.emit(position.id)
    assert "Машина выехала" in window.execution.position_tables[0].item(1, 7).text()
    saved = next(s for s in window.repository.specifications() if s.id == spec.id)
    assert saved.positions[1].note == position.note
    assert [text for _, text in saved.positions[1].comments] == ["Машина выехала", "Ждём машину\nДокументы готовы"]
    monkeypatch.setattr(PositionCommentsDialog, "exec", lambda self: 0)
    window.edit_position_comment(spec.id, position.id)
    assert next(s for s in window.repository.specifications() if s.id == spec.id) == saved
    window.open_execution_tender(2)
    assert window.current.id == 2


def test_specification_comment_draft_survives_navigation_and_posts_with_time(execution_window, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    window = execution_window
    spec = next(s for s in window.repository.specifications() if not s.archived)
    window.open_specification(spec.id)
    original = window.repository.specifications()
    window.execution.spec_comment_feed.edit.setPlainText("Согласовали новую машину")
    window.refresh_execution()
    window.open_specification(next(s.id for s in original if s.id != spec.id))
    window.open_specification(spec.id)
    assert window.execution.spec_comment_feed.edit.toPlainText() == "Согласовали новую машину"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window.repository.specifications() == original
    window.execution.spec_comment_feed.add_button.click()
    updated = next(s for s in window.repository.specifications() if s.id == spec.id)
    assert updated.comments[0][1] == "Согласовали новую машину"
    assert "+03:00" in updated.comments[0][0]
    assert updated.positions == spec.positions
    assert not window.execution.comment_drafts
    assert window.execution.spec_comment_feed.edit.toPlainText() == ""


def test_position_comment_draft_close_cancel_keeps_text(execution_window, monkeypatch):
    from tender_app.execution_comments import PositionCommentsDialog
    window = execution_window
    spec = next(s for s in window.repository.specifications() if s.positions)
    dialog = PositionCommentsDialog(window.repository, spec.id, spec.positions[0].id, window)
    before = window.repository.specifications()
    dialog.feed.edit.setPlainText("Черновик")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    dialog.reject()
    assert dialog.feed.edit.toPlainText() == "Черновик"
    assert window.repository.specifications() == before
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    dialog.reject()
    assert dialog.feed.edit.toPlainText() == ""
    assert next(s for s in window.repository.specifications() if s.id == spec.id).positions[0].comments[0][1] == "Черновик"


def test_compact_column_width_survives_refresh(execution_window, app):
    window = execution_window
    spec = next(s for s in window.repository.specifications() if len(s.positions) == 3)
    window.settings.values["execution_position_widths"] = [999] * 7
    window.open_specification(spec.id)
    app.processEvents()
    table = window.execution.position_tables[0]
    assert table.columnWidth(0) != 999
    table.setColumnWidth(3, 250)
    window.resize(1100, 800)
    app.processEvents()
    assert table.saved_widths[3] == 250
    before = window.repository.specifications()
    table.cellClicked.emit(0, 3)
    assert window.repository.specifications() == before
    window.close()
    assert window.settings.get("execution_compact_position_widths", [])[3] == 250
    reopened = ExecutionWindow(window.repository, window.settings, window.documents_root)
    reopened.open_specification(spec.id)
    reopened.show()
    app.processEvents()
    reopened_table = reopened.execution.position_tables[0]
    assert reopened_table.saved_widths[3] == 250
    assert reopened_table.columnWidth(3) <= 250
    assert reopened_table.horizontalScrollBar().maximum() == 0
    reopened.close()


def test_comment_dialog_keeps_composer_and_entries_together_when_resized(execution_window, app):
    from tender_app.execution_comments import PositionCommentsDialog
    from PySide6.QtCore import QPoint
    window = execution_window
    spec = window.repository.specifications()[0]
    pid = spec.positions[0].id
    window.repository.set_position_comment(spec.id, pid, "Первая запись")
    window.repository.set_position_comment(spec.id, pid, "Вторая запись")
    dialog = PositionCommentsDialog(window.repository, spec.id, pid, window)
    dialog.show()
    for height in (540, 740):
        dialog.resize(980, height)
        app.processEvents()
        feed = dialog.feed
        button_top = feed.add_button.mapTo(feed, QPoint()).y()
        first_top = feed.entries.itemAt(0).widget().mapTo(feed, QPoint()).y()
        assert 0 <= button_top - feed.edit.geometry().bottom() <= 12
        assert 0 <= first_top - (button_top + feed.add_button.height()) <= 12
    dialog.close()


def test_specification_comment_input_is_compact_resizable_and_remembers_height(execution_window, app):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt
    window = execution_window
    spec = window.repository.specifications()[0]
    window.open_specification(spec.id)
    app.processEvents()
    feed = window.execution.spec_comment_feed
    assert feed.edit.height() == 64
    handle = feed.resize_handle
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(3, 3), QPointF(100, 100),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(3, 83), QPointF(100, 180),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(handle, press)
    app.sendEvent(handle, move)
    app.processEvents()
    assert feed.edit.height() == 144
    assert window.execution.comment_input_height == 144
    window.add_specification_comment(spec.id, "Проверка сохранения высоты")
    app.processEvents()
    assert window.execution.spec_comment_feed.edit.height() == 144
    window.close()
    assert window.settings.get("execution_comment_input_height", 64) == 144


def test_specification_contacts_are_always_visible_and_fit(execution_window, app):
    from PySide6.QtTest import QTest
    window = execution_window
    spec = window.repository.specifications()[0]
    tender = next(t for t in window.repository.all() if t.id == spec.tender_id)
    tender.contacts = "Учебный отдел снабжения\ndemo@example.invalid\nКонтакт для приёмки\nВремя работы: по согласованию"
    window.repository.save(tender)
    window.open_specification(spec.id)
    for width in (1320, 918, 1320):
        window.resize(width, 900)
        QTest.qWait(30)
        assert not hasattr(window.execution, "spec_details_toggle")
        assert window.execution.customer_contacts.isVisible()
        assert window.execution.customer_contacts.toPlainText() == tender.contacts
        assert window.execution.customer_contacts.height() < 160
        assert window.execution.scroll.horizontalScrollBar().maximum() == 0


def test_position_editor_rejects_invalid_then_preserves_decimal_and_logistics(execution_window, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    dialog = PositionDialog(Position(name="Материал"), execution_window)
    dialog.edits["ordered"].setText("2,5")
    dialog.edits["customer_shipped"].setText("3")
    assert not dialog.save()
    assert errors and dialog.edits["customer_shipped"].text() == "3"
    dialog.edits["customer_shipped"].setText("1,25")
    dialog.edits["unit"].setText("т")
    dialog.edits["eta"].setText("2026-11-05")
    dialog.edits["transport"].setText("Автомобиль ДЕМО")
    assert dialog.save()
    assert dialog.position.remaining == Decimal("1.25")
    assert dialog.position.placed is None
    assert dialog.position.eta == "2026-11-05"
    assert dialog.position.transport == "Автомобиль ДЕМО"


def test_partial_shipment_extension_history_then_manual_completion(execution_window, monkeypatch):
    repo = execution_window.repository
    spec = next(s for s in repo.specifications() if s.tender_id == 1)
    dialog = SpecificationDialog(repo, spec, execution_window)
    dialog.specification.positions[0].customer_shipped = Decimal("0.5")
    dialog.edits["revised"].setText("2026-11-10")
    dialog.edits["history_note"].setPlainText("Перенос согласован")
    assert dialog.save()
    saved = dialog.specification
    assert saved.due == spec.due
    assert saved.revised == "2026-11-10"
    assert saved.summary == "Частично отгружено"
    assert not saved.completed
    assert len(saved.history) == len(spec.history) + 1
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    finish = SpecificationDialog(repo, saved, execution_window)
    finish.completed.setChecked(True)
    assert not finish.save()
    assert errors
    finish.specification.positions[0].customer_shipped = Decimal("1")
    assert finish.save()
    execution_window.refresh_execution()
    execution_window.execution.state.setCurrentText("Завершённые")
    assert execution_window.execution.tree.topLevelItemCount() == 2


def test_save_failure_keeps_draft_and_retry_does_not_duplicate_history(execution_window, monkeypatch):
    repo = execution_window.repository
    spec = repo.specifications()[0]
    dialog = SpecificationDialog(repo, spec, execution_window)
    dialog.edits["history_note"].setPlainText("Не терять")
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    with repo.connection:
        repo.connection.execute("""CREATE TRIGGER fail_history BEFORE INSERT ON execution_history
                                  BEGIN SELECT RAISE(ABORT, 'locked for test'); END""")
    assert not dialog.save()
    assert dialog.modified
    assert dialog.text("history_note") == "Не терять"
    assert len(repo.specifications()[0].history) == len(spec.history)
    with repo.connection:
        repo.connection.execute("DROP TRIGGER fail_history")
    assert dialog.save()
    assert len(repo.specifications()[0].history) == len(spec.history) + 1


def test_unsaved_spec_and_position_cancel_and_discard(execution_window, monkeypatch):
    repo = execution_window.repository
    spec = repo.specifications()[0]
    dialog = SpecificationDialog(repo, spec, execution_window)
    dialog.show()
    dialog.edits["title"].setPlainText("Черновик")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    dialog.reject()
    assert dialog.isVisible() and dialog.modified
    assert repo.specifications()[0].title == spec.title
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    dialog.reject()
    assert not dialog.isVisible()
    position = PositionDialog(spec.positions[0], execution_window)
    position.show()
    position.edits["ordered"].setText("50")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    position.close()
    assert position.isVisible()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    position.reject()


def test_section_switch_protects_tender_draft_and_keeps_agent(execution_window, monkeypatch):
    window = execution_window
    window.edits["customer"].setPlainText("Несохранённый заказчик")
    assert window.dirty
    with monkeypatch.context() as patch:
        patch.setattr(window, "guard_changes", lambda: False)
        window.pages.setCurrentIndex(1)
    assert window.pages.currentIndex() == 0
    assert window.edits["customer"].toPlainText() == "Несохранённый заказчик"
    window.dirty = False
    window.toggle_agent_dock()
    window.pages.setCurrentIndex(1)
    assert window.agent_dock.isVisible()
    window.pages.setCurrentIndex(0)
    assert window.agent_dock.isVisible()
    assert not window.assistant_dock.isVisible()


def test_changed_tender_status_preserves_execution_and_shows_warning(execution_window):
    window = execution_window
    window.open_execution_tender(2)
    window.status.setCurrentText("Проиграли")
    assert "отличается" in window.spec_layout.itemAt(0).widget().text()
    assert window.save_tender()
    assert len(window.repository.specifications()) == 4
    window.pages.setCurrentIndex(1)
    assert any("Результат тендера изменён" in window.execution.tree.topLevelItem(i).text(0)
               for i in range(window.execution.tree.topLevelItemCount()))


def test_new_winner_is_visible_without_creating_specification(execution_window):
    window = execution_window
    winner = replace(window.repository.all()[0], id=None, number="ДЕМО-НОВЫЙ", status="Победа")
    saved = window.repository.save(winner)
    window.refresh_execution()
    assert window.execution.without_specifications == 2
    assert not any(s.tender_id == saved.id for s in window.repository.specifications())


def test_unsaved_new_winner_cannot_create_orphan_spec(execution_window, monkeypatch):
    window = execution_window
    window.new_tender()
    window.status.setCurrentText("Победа")
    monkeypatch.setattr(window, "guard_changes", lambda: True)  # User chose to discard the new tender.
    window.create_specification(None)
    assert "Сначала сохраните" in window.statusBar().currentMessage()
    assert len(window.repository.specifications()) == 4


def test_same_customer_and_contract_group_specs_from_different_tenders(execution_window):
    window = execution_window
    tenders = {t.id:t for t in window.repository.all()}
    window.repository.save(replace(tenders[1],customer=tenders[2].customer))
    window.refresh_execution()
    assert window.execution.visible_contracts == 1
    parent = next(window.execution.tree.topLevelItem(i) for i in range(window.execution.tree.topLevelItemCount())
                  if window.execution.tree.topLevelItem(i).childCount() == 3)
    window.execution.tree.setCurrentItem(parent)
    assert len(window.execution.spec_links) == 3
    linked_tenders = {s.tender_id for s in window.repository.specifications() if s.id in window.execution.spec_links}
    assert linked_tenders == {1,2}


def test_unknown_contracts_stay_separate_and_creation_inherits_contract(execution_window,monkeypatch):
    window = execution_window
    spec = window.repository.specifications()[0]
    captured = []
    monkeypatch.setattr(window,"edit_specification",lambda candidate:captured.append(candidate))
    window.create_contract_specification((spec.tender_id,),spec.contract)
    assert captured[0].tender_id == spec.tender_id and captured[0].contract == spec.contract
    for item in window.repository.specifications():
        item.contract = ""
        window.repository.save_specification(item)
    window.refresh_execution()
    assert window.execution.visible_contracts == 0
    assert window.execution.tree.topLevelItemCount() == 3  # Two tender groups plus winner without specs.


def test_position_due_editor_and_header(execution_window, app):
    from PySide6.QtWidgets import QLabel
    window = execution_window
    spec = next(s for s in window.repository.specifications() if len(s.positions) == 3)
    spec.date = "2026-10-01"
    spec.due = "2027-02-10"
    spec.revised = "2027-03-10"
    dialog = PositionDialog(spec.positions[0], window)
    dialog.edits["due"].setText("2026-10-31")
    assert dialog.save()
    spec.positions[0] = dialog.position
    window.repository.save_specification(spec)
    window.refresh_execution()
    window.open_specification(spec.id)
    app.processEvents()
    text = "\n".join(label.text() for label in window.execution.spec_header.findChildren(QLabel))
    for value in ("Договор", "Спецификация", spec.contract, "01.10.2026", "31.10.2026", "30 кал. дн."):
        assert value in text
    table = window.execution.position_tables[0]
    assert table.item(0, 3).text() == "31.10.2026"
    assert table.item(1, 3).text() == "10.03.2027"


def test_quick_edits_save_cancel_and_do_not_replace_working_note(execution_window, monkeypatch):
    from tender_app.execution_editor import PositionQuickEditDialog
    window = execution_window
    spec = window.repository.specifications()[0]
    pid = spec.positions[0].id
    window.open_specification(spec.id)
    for column, field, value in ((2, "due", "2027-02-10"), (3, "supplier", "Завод А"),
                                 (5, "transport", "Авто"), (6, "working_note", "Отгружено 1 из 2")):
        def enter(dialog):
            assert dialog.field == field
            editor = dialog.edits[field]
            if field == "working_note":
                editor.setPlainText(value)
            else:
                editor.setText(value)
            assert dialog.save()
            return dialog.DialogCode.Accepted
        monkeypatch.setattr(PositionQuickEditDialog, "exec", enter)
        window.execution.position_tables[0].edit_requested.emit(pid, field)
        assert getattr(window.repository.specifications()[0].positions[0], field) == value
    before = window.repository.specifications()
    monkeypatch.setattr(PositionQuickEditDialog, "exec", lambda self: self.DialogCode.Rejected)
    window.execution.position_tables[0].edit_requested.emit(pid, "working_note")
    assert window.repository.specifications() == before
    window.repository.set_position_comment(spec.id, pid, "Ещё одно событие")
    window.refresh_execution()
    assert "Ещё одно событие" in window.execution.position_tables[0].item(0, 7).text()
    assert window.repository.specifications()[0].positions[0].working_note == "Отгружено 1 из 2"


def test_table_shipment_updates_latest_comment_and_preserves_filter(execution_window, app, monkeypatch):
    window = execution_window
    spec = window.repository.specifications()[0]
    position = spec.positions[0]
    window.open_specification(spec.id)
    app.processEvents()
    window.execution.position_filter.setCurrentIndex(1)

    def enter(dialog):
        dialog.setTextValue("0,5")
        return dialog.DialogCode.Accepted

    monkeypatch.setattr(QInputDialog, "exec", enter)
    window.execution.position_tables[0].cellDoubleClicked.emit(0, 1)
    table = window.execution.position_tables[0]
    assert table.item(0, 1).text() == "0,5 / 1 шт"
    assert table.item(0, 2).text() == "0,5"
    assert "0,5" in table.item(0, 7).text()
    assert window.repository.specifications()[0].positions[0].working_note == position.working_note
    assert window.execution.position_filter.currentIndex() == 1
    table.complete_requested.emit(position.id)
    table = window.execution.position_tables[0]
    assert table.isRowHidden(0)
    saved = window.repository.specifications()[0].positions[0]
    assert saved.customer_shipped == saved.ordered and saved.remaining == 0
    assert len(saved.comments) == len(position.comments) + 2
    window.execution.position_filter.setCurrentIndex(0)
    assert table.item(0, 2).text() == "✓ Всё"
    assert not table.isRowHidden(0)
    # Quantity remains editable: an incorrect full-shipment mark can be corrected.
    table.cellDoubleClicked.emit(0, 2)
    assert window.execution.position_tables[0].item(0, 2).text() == "0,5"
