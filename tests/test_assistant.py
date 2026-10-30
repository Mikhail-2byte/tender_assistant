import sqlite3
from dataclasses import replace
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton

from tender_app.models import Tender


def test_chat_enter_sends_shift_enter_adds_line_and_controls_are_inside_composer(window, app):
    panel = window.assistant
    window.assistant_toggle.setChecked(True)
    app.processEvents()
    requests = []
    panel.search_requested.connect(requests.append)
    panel.input.setPlainText("Найди 7207")
    panel.input.moveCursor(panel.input.textCursor().MoveOperation.End)
    panel.input.setFocus()
    QTest.keyClick(panel.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert panel.input.toPlainText() == "Найди 7207\n"
    assert not requests
    QTest.keyClick(panel.input, Qt.Key.Key_Return)
    assert requests == ["7207"]
    assert panel.input.toPlainText() == ""
    QTest.keyClick(panel.input, Qt.Key.Key_Return)
    assert requests == ["7207"]
    assert panel.composer.isAncestorOf(panel.send_button)
    assert panel.composer.isAncestorOf(panel.model_selector)


def test_new_messages_scroll_after_wrapped_layout_settles(window):
    panel = window.assistant
    window.assistant_toggle.setChecked(True)
    panel.show_search_results("7207", [Tender(id=i, number="7207", customer="Заказчик " * 15,
                                             title="Длинное описание предмета " * 20) for i in range(4)])
    QTest.qWait(250)
    panel.show_model_error("Текст запроса " * 30, "primary", demonstration=True)
    QTest.qWait(250)
    scrollbar = panel.scroll.verticalScrollBar()
    assert scrollbar.maximum() > 0
    assert scrollbar.value() == scrollbar.maximum()
    scrollbar.setValue(0)
    QTest.qWait(100)
    assert scrollbar.value() == 0


def test_panel_toggle_and_current_tender_context(window, app):
    assert not window.assistant_dock.isVisible()
    window.assistant_toggle.setChecked(True)
    app.processEvents()
    assert window.assistant_dock.isVisible()
    assert window.current.number in window.assistant.context.text()
    window.load_tender(window.tenders[-1])
    assert window.current.number in window.assistant.context.text()
    window.assistant_dock.close()
    assert not window.assistant_toggle.isChecked()


def test_unconnected_chat_never_changes_data(window):
    before = window.repository.all()
    window.assistant.input.setPlainText("По 7207 началась переторжка <test>")
    window.assistant.send_button.click()
    assert window.assistant.input.toPlainText() == ""
    assert window.repository.all() == before
    assert window.pending_assistant is None and not window.dirty
    body = window.assistant.messages.itemAt(1).widget().findChild(QLabel, "chatBody")
    assert body.text().endswith("<test>") and body.textFormat() == Qt.TextFormat.PlainText
    assert "не обработан" in window.assistant.messages.itemAt(2).widget().findChild(QLabel, "chatBody").text()


def test_proposal_opens_exact_card_without_saving_and_requires_chat_confirmation(window):
    target = next(t for t in window.tenders if t.id != window.current.id and t.status != "Переторжка")
    window.search.setText("Нет такого тендера")
    window.refresh_list()
    assert window.propose_assistant_change(target.id, status="Переторжка")
    assert window.current == target and window.status.currentText() == "Переторжка"
    assert window.outside.isVisible()
    assert window.repository.all() == window.tenders
    assert not window.save_button.isEnabled()
    assert not window.save_tender()
    assert not window.open_assistant_tender(window.tenders[-1].id)
    window.assistant.confirm_button.click()
    saved = next(t for t in window.repository.all() if t.id == target.id)
    assert saved == replace(target, status="Переторжка", comment=saved.comment, updated_at=saved.updated_at)
    assert saved.comment.startswith(target.comment)
    assert "Подтверждено в чате" in saved.comment
    assert f"Статус: {target.status} → Переторжка" in saved.comment
    assert window.pending_assistant is None and not window.dirty
    assert not window.save_button.isEnabled()


def test_cancel_restores_card_and_does_not_write_history(window):
    original = window.current
    assert window.propose_assistant_change(original.id, note="Отправил запрос")
    assert window.note.toPlainText() == "Отправил запрос"
    assert next(t for t in window.repository.all() if t.id == original.id) == original
    window.assistant.cancel_button.click()
    assert window.candidate() == original and not window.note.toPlainText()
    assert window.pending_assistant is None and not window.dirty
    assert next(t for t in window.repository.all() if t.id == original.id) == original


def test_chat_save_error_retains_proposal_and_retry_does_not_duplicate(window, monkeypatch):
    original = window.current
    save = window.repository.save
    assert window.propose_assistant_change(original.id, note="Письмо отправлено")
    errors = []
    monkeypatch.setattr(window, "error", lambda error: errors.append(str(error)))
    def fail(tender):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(window.repository, "save", fail)
    assert not window.confirm_assistant_change()
    assert errors and window.pending_assistant == original
    assert window.note.toPlainText() == "Письмо отправлено"
    assert not window.assistant_saving
    monkeypatch.setattr(window.repository, "save", save)
    assert window.confirm_assistant_change()
    assert not window.confirm_assistant_change()
    saved = next(t for t in window.repository.all() if t.id == original.id)
    assert saved.comment.count("Письмо отправлено") == 1
    assert saved.comment.startswith(original.comment)


def test_proposal_does_not_replace_existing_user_draft(window):
    window.edits["contacts"].setPlainText("Мой черновик")
    current_id = window.current.id
    assert not window.propose_assistant_change(window.tenders[-1].id, note="Запрос")
    assert window.pending_assistant is None
    assert window.current.id == current_id
    assert window.edits["contacts"].toPlainText() == "Мой черновик"


def test_manual_edits_are_in_confirmation_summary(window):
    assert window.propose_assistant_change(window.current.id, note="Запрос")
    window.edits["contacts"].setPlainText("Новый контакт")
    window.note.setPlainText("Уточнённый текст запроса")
    assert "Новый контакт" in window.assistant.proposal_summary.text()
    assert "Уточнённый текст запроса" in window.assistant.proposal_summary.text()
    assert window.confirm_assistant_change()
    assert window.current.contacts == "Новый контакт"
    assert window.current.comment.endswith("Уточнённый текст запроса")


def test_changed_persisted_card_is_not_overwritten(window):
    original = window.current
    assert window.propose_assistant_change(original.id, note="Запрос")
    changed = window.repository.save(replace(original, contacts="Изменено другим способом"))
    assert not window.confirm_assistant_change()
    assert next(t for t in window.repository.all() if t.id == original.id) == changed
    window.cancel_assistant_change()
    assert window.current == changed


def test_today_results_use_moscow_day_and_ignore_registry_filters(window, monkeypatch):
    today = datetime(2026, 11, 5, 12, 0)
    monkeypatch.setattr("tender_app.models.now_moscow", lambda: today)
    monkeypatch.setattr("tender_app.window.now_moscow", lambda: today)
    window.repository.connection.execute("DELETE FROM tenders")
    window.repository.connection.commit()
    active = window.repository.save(Tender(number="ТЕСТ-СЕГОДНЯ", status="Податься", deadline_date="2026-11-05"))
    closed = window.repository.save(Tender(number="НЕ-ПОДАВАТЬ", status="Победа", deadline_date="2026-11-05"))
    window.tenders = window.repository.all()
    window.search.setText("Нет результатов")
    window.status_filter.setCurrentText("Проиграли")
    window.assistant_today()
    result = window.assistant.messages.itemAt(window.assistant.messages.count() - 1).widget()
    buttons = result.findChildren(QPushButton)
    assert [button.text() for button in buttons] == [f"Открыть № {active.number}"]
    buttons[0].click()
    assert window.current.id == active.id
    assert window.outside.isVisible()
    assert window.search.text() == "Нет результатов"
    assert window.status_filter.currentText() == "Проиграли"


def test_chat_shortcut_does_not_send_history_draft(window, app):
    window.assistant_toggle.setChecked(True)
    window.activateWindow()
    window.note.setPlainText("Не отправлять из чата")
    original = window.current
    window.assistant.input.setPlainText("Привет")
    window.assistant.input.setFocus()
    app.processEvents()
    QTest.keyClick(window.assistant.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert not window.assistant.input.toPlainText()
    assert window.note.toPlainText() == "Не отправлять из чата"
    assert next(t for t in window.repository.all() if t.id == original.id) == original
    window.note.setFocus()
    app.processEvents()
    QTest.keyClick(window.note, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.note.toPlainText() == ""
    assert window.current.comment.endswith("Не отправлять из чата")


def test_hidden_panel_preserves_proposal_and_blocks_navigation(window):
    assert window.propose_assistant_change(window.current.id, note="Запрос")
    proposal = window.assistant.proposal
    window.assistant_toggle.setChecked(False)
    assert not window.guard_changes()
    assert window.assistant_toggle.isChecked()
    assert window.assistant.proposal is proposal
    window.cancel_assistant_change()


def test_no_change_and_invalid_status_do_not_create_proposal(window):
    original = window.current
    assert not window.propose_assistant_change(original.id, status=original.status)
    assert not window.propose_assistant_change(original.id, status="Неизвестный статус")
    assert window.pending_assistant is None and not window.dirty


def test_invalid_manual_deadline_cannot_be_confirmed(window, monkeypatch):
    original = window.current
    errors = []
    monkeypatch.setattr(window, "error", lambda error: errors.append(str(error)))
    assert window.propose_assistant_change(original.id, note="Запрос")
    window.time.setText("25:60")
    assert not window.assistant.confirm_button.isEnabled()
    assert not window.confirm_assistant_change()
    assert errors and window.pending_assistant == original
    assert next(t for t in window.repository.all() if t.id == original.id) == original
    window.cancel_assistant_change()


def test_cancel_read_error_keeps_pending_draft(window, monkeypatch):
    assert window.propose_assistant_change(window.current.id, note="Запрос")
    errors = []
    read = window.repository.all
    def fail():
        raise sqlite3.OperationalError("read failed")
    monkeypatch.setattr(window, "error", lambda error: errors.append(str(error)))
    monkeypatch.setattr(window.repository, "all", fail)
    window.cancel_assistant_change()
    assert errors and window.pending_assistant is not None
    assert window.note.toPlainText() == "Запрос"
    monkeypatch.setattr(window.repository, "all", read)
    window.cancel_assistant_change()
    assert window.pending_assistant is None


def test_long_tender_number_fits_header_with_chat_open(window, app):
    window.load_tender(replace(window.current, number="32616452039-АБ-2026"))
    window.assistant_toggle.setChecked(True)
    window.resize(1240, 850)
    window.splitter.setSizes([330, 530])
    QTest.qWait(80)
    assert window.caption.width() >= window.caption.fontMetrics().horizontalAdvance(window.caption.text())
    assert window.details_toggle.width() >= window.details_toggle.fontMetrics().horizontalAdvance(window.details_toggle.text())
    assert window.assistant_toggle.width() >= window.assistant_toggle.fontMetrics().horizontalAdvance("Помощник")
    assert window.scroll.horizontalScrollBar().maximum() == 0


def test_chat_columns_give_card_priority_and_list_can_be_hidden(window, app):
    window.resize(1640, 900)
    window.assistant_toggle.setChecked(True)
    QTest.qWait(100)
    assert window.card.width() > window.width() * .50
    assert window.registry_panel.width() < window.width() * .25
    assert window.assistant_dock.width() < window.width() * .30
    before = window.candidate()
    card_width = window.card.width()
    window.list_toggle.setChecked(False)
    QTest.qWait(80)
    assert not window.registry_panel.isVisible()
    assert window.card.width() > card_width
    assert window.candidate() == before and not window.dirty
    assert window.subject_heading.height() >= window.subject_heading.heightForWidth(window.subject_heading.width())
    assert window.date.isVisible() and window.time.isVisible()
    assert window.deadline_view.width() >= window.date.width() + window.time.width()
    window.list_toggle.setChecked(True)
    QTest.qWait(80)
    assert window.registry_panel.isVisible() and window.registry_panel.width() > 0
    assert window.candidate() == before


def test_examples_are_collapsed_and_context_is_compact(window):
    assert window.assistant.examples.isHidden()
    assert "\n" not in window.assistant.context.text()
    window.assistant.examples_toggle.setChecked(True)
    assert not window.assistant.examples.isHidden()
    window.assistant.examples_toggle.setChecked(False)
    assert window.assistant.examples.isHidden()


def test_finished_proposal_is_compact_and_opens_exact_card(window, app):
    original = window.current
    status = "Податься" if original.status == "Переторжка" else "Переторжка"
    assert window.propose_assistant_change(original.id, status=status)
    frame = window.assistant.proposal
    QTest.qWait(60)
    height_before = frame.height()
    assert window.confirm_assistant_change()
    QTest.qWait(80)
    text = "\n".join(label.text() for label in frame.findChildren(QLabel) if label.isVisible())
    assert "Не сохранено" not in text and "✓" in text
    assert frame.height() < height_before
    from PySide6.QtWidgets import QToolButton
    details = next(b for b in frame.findChildren(QToolButton) if b.text() == "Подробнее")
    details.setChecked(True)
    assert any(label.isVisible() and "Статус:" in label.text() for label in frame.findChildren(QLabel))
    target_id = original.id
    window.load_tender(next(t for t in window.tenders if t.id != target_id))
    next(b for b in frame.findChildren(QToolButton) if b.text() == "Открыть карточку").click()
    assert window.current.id == target_id


def test_status_audit_is_atomic_on_failure_and_retry(window, monkeypatch):
    original = window.current
    status = "Податься" if original.status == "Переторжка" else "Переторжка"
    assert window.propose_assistant_change(original.id, status=status)
    save = window.repository.save
    def fail(tender):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(window, "error", lambda error: None)
    monkeypatch.setattr(window.repository, "save", fail)
    assert not window.confirm_assistant_change()
    assert next(t for t in window.repository.all() if t.id == original.id).comment == original.comment
    monkeypatch.setattr(window.repository, "save", save)
    assert window.confirm_assistant_change()
    assert window.current.comment.count(f"Статус: {original.status} → {status}") == 1
    assert window.current.comment.count("Подтверждено в чате") == 1


def test_model_switch_preserves_proposal_chat_draft_and_database(window, app):
    original = window.repository.all()
    assert window.propose_assistant_change(window.current.id, note="Черновик записи")
    window.assistant.input.setPlainText("Черновик сообщения")
    messages = window.assistant.messages.count()
    selector = window.assistant.model_selector
    assert selector.currentData() == "primary"
    selector.setCurrentIndex(1)
    app.processEvents()
    assert selector.currentData() == "fallback"
    assert window.assistant.input.toPlainText() == "Черновик сообщения"
    assert window.note.toPlainText() == "Черновик записи"
    assert window.assistant.messages.count() == messages
    assert window.pending_assistant is not None
    assert window.repository.all() == original
    assert selector.width() >= selector.fontMetrics().horizontalAdvance(selector.currentText())
    window.cancel_assistant_change()


def test_model_choice_is_restored_between_launches(repo, app, tmp_path):
    from tender_app.settings import Settings
    from tender_app.window import Window
    path = tmp_path / "model-settings.json"
    first = Window(repo, Settings(path), tmp_path)
    first.assistant.model_selector.setCurrentIndex(1)
    first.close()
    second = Window(repo, Settings(path), tmp_path)
    assert second.assistant.model_selector.currentData() == "fallback"
    second.close()


def test_duplicate_search_shows_details_and_opens_chosen_id(window):
    first = window.repository.save(Tender(number="424345", customer="Первый заказчик", title="Валы"))
    second = window.repository.save(Tender(number="424345", customer="Второй заказчик", title="Шестерни"))
    window.tenders = window.repository.all()
    original_id = window.current.id
    window.search.setText("Ничего не показывать")
    window.status_filter.setCurrentText("Победа")
    window.assistant.input.setPlainText("Найди тендер 424345")
    window.assistant.send_button.click()
    assert window.current.id == original_id
    result = window.assistant.messages.itemAt(window.assistant.messages.count() - 1).widget()
    text = "\n".join(label.text() for label in result.findChildren(QLabel))
    assert all(value in text for value in ("424345", "Первый заказчик", "Второй заказчик", "Валы", "Шестерни", "Выберите"))
    buttons = result.findChildren(QPushButton)
    assert [button.property("tenderId") for button in buttons] == [first.id, second.id]
    buttons[1].click()
    assert window.current.id == second.id and window.outside.isVisible()
    assert window.search.text() == "Ничего не показывать"


def test_empty_and_unmatched_search_do_not_mutate_registry(window):
    before = window.repository.all()
    for query, expected in (("Найди", "Укажите номер"), ("Найди НЕСУЩЕСТВУЮЩИЙ-123", "Ничего не найдено")):
        window.assistant.input.setPlainText(query)
        window.assistant.send_button.click()
        result = window.assistant.messages.itemAt(window.assistant.messages.count() - 1).widget()
        assert expected in "\n".join(label.text() for label in result.findChildren(QLabel))
    assert window.repository.all() == before and not window.dirty


def test_search_result_cannot_discard_pending_proposal(window):
    current_id = window.current.id
    target = next(t for t in window.tenders if t.id != current_id)
    assert window.propose_assistant_change(current_id, note="Не терять предложение")
    window.assistant_search(target.number)
    result = window.assistant.messages.itemAt(window.assistant.messages.count() - 1).widget()
    result.findChildren(QPushButton)[0].click()
    assert window.current.id == current_id
    assert window.note.toPlainText() == "Не терять предложение"
    window.cancel_assistant_change()


def test_demo_error_retry_keeps_exact_request_and_new_draft(window, app):
    panel = window.assistant
    before = window.repository.all()
    request = "  Сделай сводку\nВторая строка <test>  "
    panel.input.setPlainText(request)
    panel.simulate_model_failure()
    error = panel.messages.itemAt(panel.messages.count() - 1).widget()
    assert error.property("requestText") == request
    assert "Недоступна" in panel.model_state.text() and "пример" in panel.model_state.text()
    retry = next(b for b in error.findChildren(QPushButton) if "Повторить" in b.text())
    panel.input.setPlainText("Новый черновик")
    retry.click()
    assert panel.model_selector.currentData() == "fallback"
    assert "Отвечает" in panel.model_state.text()
    assert not panel.send_button.isEnabled()
    retry.click()
    panel.model_selector.setCurrentIndex(0)
    QTest.qWait(800)
    assert "Недоступна" in panel.model_state.text()
    panel.model_selector.setCurrentIndex(1)
    assert "Готова" in panel.model_state.text() and "пример" in panel.model_state.text()
    assert panel.input.toPlainText() == "Новый черновик" and panel.send_button.isEnabled()
    result = panel.messages.itemAt(panel.messages.count() - 1).widget()
    assert result.property("requestText") == request
    assert sum(panel.messages.itemAt(i).widget().objectName() == "chatUser" for i in range(panel.messages.count())) == 1
    assert window.repository.all() == before


def test_unconfigured_retry_does_not_claim_model_is_ready(window):
    panel = window.assistant
    panel.input.setPlainText("Сделай сводку")
    panel.send_message()
    result = panel.messages.itemAt(panel.messages.count() - 1).widget()
    result.findChildren(QPushButton)[0].click()
    assert panel.model_selector.currentData() == "fallback"
    assert panel.model_state.text() == "Не подключена"
    assert not panel.responding
    result = panel.messages.itemAt(panel.messages.count() - 1).widget()
    assert result.property("requestText") == "Сделай сводку"


def test_local_search_works_when_demo_model_is_unavailable(window):
    panel = window.assistant
    panel.simulate_model_failure()
    target = next(tender for tender in window.tenders if tender.number)
    panel.input.setPlainText(f"Найди {target.number}")
    panel.send_message()
    assert "Недоступна" in panel.model_state.text()
    result = panel.messages.itemAt(panel.messages.count() - 1).widget()
    assert result.findChildren(QPushButton)
    assert any(target.number in label.text() for label in result.findChildren(QLabel))
