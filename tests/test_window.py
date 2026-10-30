import sqlite3
from dataclasses import replace

from PySide6.QtCore import QDate, QTimer, Qt
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCalendarWidget, QDialogButtonBox, QInputDialog, QLabel

from tender_app.models import Tender, deadline_label
from tender_app.settings import Settings
from tender_app.window import Window


def answer_dialog(text):
    def answer():
        dialog = QApplication.activeModalWidget()
        assert dialog is not None
        button = next(b for b in dialog.buttons() if b.text().replace("&", "") == text)
        button.click()

    QTimer.singleShot(0, answer)


def test_search_and_selection(window, app):
    window.search.setText("ЦЕМРОС")
    QTest.qWait(300)
    assert window.list.count() > 0
    window.list.setCurrentRow(0)
    assert "цемрос" in window.current.customer.casefold()
    assert window.edits["comment"].toPlainText() == window.current.comment
    assert window.edits["folder_path"].text() == window.current.folder_path


def test_every_imported_card_round_trips_without_text_changes(window):
    for tender in window.tenders:
        window.load_tender(tender)
        assert window.candidate() == tender, f"Изменение при отображении ID {tender.id}"


def test_status_change_preserves_text_and_card_outside_filter(window):
    window.load_tender(next(t for t in window.tenders if t.status == "В работе"))
    original = window.current
    window.status_filter.setCurrentText(original.status)
    window.status.setCurrentText("Победа")
    assert window.dirty
    assert window.save_tender()
    assert window.current.id == original.id
    assert window.current.comment == original.comment
    assert window.current.folder_path == original.folder_path
    assert window.outside.text()
    assert not window.dirty
    assert not any(
        window.list.item(i).data(Qt.ItemDataRole.UserRole) == original.id
        for i in range(window.list.count())
    )


def test_unsaved_cancel_switch_close_and_discard(window):
    original = window.current
    window.edits["comment"].setPlainText("Несохранённый текст")
    answer_dialog("Остаться")
    window.list.setCurrentRow(1)
    assert window.current.id == original.id
    assert window.edits["comment"].toPlainText() == "Несохранённый текст"
    answer_dialog("Остаться")
    assert not window.close()
    assert window.isVisible() and window.dirty
    answer_dialog("Не сохранять")
    window.list.setCurrentRow(1)
    assert window.current.id != original.id
    assert (
        next(t for t in window.repository.all() if t.id == original.id).comment
        == original.comment
    )


def test_unsaved_save_then_switch(window):
    original_id = window.current.id
    window.edits["contacts"].setPlainText("Новый контакт заказчика")
    answer_dialog("Сохранить")
    window.list.setCurrentRow(1)
    assert window.current.id != original_id
    assert (
        next(t for t in window.repository.all() if t.id == original_id).contacts
        == "Новый контакт заказчика"
    )


def test_failed_write_keeps_all_input(window, monkeypatch):
    errors = []
    monkeypatch.setattr(window, "error", lambda error: errors.append(str(error)))

    def fail(tender):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(window.repository, "save", fail)
    window.edits["comment"].setPlainText("Нельзя терять\n" * 3000)
    window.note.setPlainText("Новая заметка")
    assert not window.save_tender()
    assert "Нельзя терять\n" * 3000 in window.edits["comment"].toPlainText()
    assert window.note.toPlainText() == "Новая заметка"
    assert "Новая заметка" not in window.edits["comment"].toPlainText()
    assert window.dirty and errors == ["database is locked"]


def test_new_tender_note_and_shortcut(window, app):
    window.new_tender()
    window.edits["number"].setText("0001-АБ-2026")
    window.edits["comment"].setPlainText("Старая история без даты")
    window.note.setPlainText("Связались с заказчиком")
    window.add_note()
    assert window.note.toPlainText() == ""
    assert (
        window.edits["comment"].toPlainText().startswith("Старая история без даты\n\n[")
    )
    assert "МСК]" in window.edits["comment"].toPlainText()
    window.activateWindow()
    app.processEvents()
    QTest.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert not window.dirty
    assert window.current.id is not None and window.current.number == "0001-АБ-2026"


def test_duplicate_warning(window):
    existing = window.tenders[0]
    window.new_tender()
    window.edits["number"].setText(existing.number)
    count = len(window.repository.all())
    answer_dialog("No")
    assert not window.save_tender()
    assert len(window.repository.all()) == count
    answer_dialog("Yes")
    assert window.save_tender()
    assert len(window.repository.all()) == count + 1


def test_optional_date_clear_and_import_metadata(window):
    original = window.repository.save(Tender(number="ТЕСТ-ДАТА", deadline_needs_review=1,
                                             deadline_raw="неверная дата"))
    window.load_tender(original)
    window.edits["contacts"].setPlainText("Уточнить дату у заказчика")
    assert window.save_tender()
    assert window.current.deadline_raw == original.deadline_raw
    assert window.current.deadline_needs_review == original.deadline_needs_review
    window.date.setDate(QDate(2026, 11, 5))
    window.time.clear()
    assert window.save_tender()
    assert window.current.deadline_date and window.current.deadline_time is None
    assert window.current.deadline_timezone == "Europe/Moscow"
    window.clear_deadline()
    assert window.save_tender()
    assert window.current.deadline_date is None and window.current.deadline_time is None
    assert window.current.deadline_raw == original.deadline_raw


def test_external_open_boundary(window, monkeypatch, tmp_path):
    paths, urls, errors = [], [], []
    monkeypatch.setattr("os.startfile", lambda value: paths.append(value))
    monkeypatch.setattr(
        QDesktopServices, "openUrl", lambda value: urls.append(value.toString()) or True
    )
    monkeypatch.setattr(window, "error", lambda error: errors.append(str(error)))
    expected = (window.documents_root / window.current.folder_path).resolve()
    assert expected.is_dir()
    window.open_folder()
    assert paths == [str(expected)]
    window.edits["url"].setText("https://example.org/tender/123")
    window.open_url()
    assert urls == ["https://example.org/tender/123"]
    window.edits["url"].setText("file:///C:/Windows")
    window.open_url()
    assert len(urls) == 1 and errors
    missing = tmp_path / "Новая папка с пробелами"
    window.edits["folder_path"].setText(str(missing))
    window.edits["number"].setText("001/А-26")
    answer_dialog("Отмена")
    window.open_folder()
    assert not missing.exists()
    answer_dialog("Создать эту папку")
    window.open_folder()
    assert missing.is_dir() and paths[-1] == str(missing)
    request = missing / "Запрос_001_А-26.xlsx"
    assert request.is_file()
    request.write_bytes(b"user request")
    window.open_folder()
    assert request.read_bytes() == b"user request"


def test_settings_restart(window, app, tmp_path):
    selected_id = window.current.id
    window.status_filter.setCurrentText("Податься")
    window.sort.setCurrentText("Заказчик")
    window.resize(1300, 850)
    window.close()
    second = Window(
        window.repository, Settings(tmp_path / "settings.json"), window.documents_root
    )
    assert second.current.id == selected_id
    assert second.status_filter.currentText() == "Податься"
    assert second.sort.currentText() == "Заказчик"
    assert second.width() == 1300 and second.height() == 850
    second.close()


def test_history_send_is_persisted_and_keeps_old_text(window, app):
    original = window.current
    window.note.setPlainText("Позвонил заказчику\nЖдём ответ")
    assert "action" not in window.edits
    window.edits["contacts"].setPlainText("Новый контакт")
    QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
    saved = next(t for t in window.repository.all() if t.id == original.id)
    assert saved.comment.startswith(
        original.comment + ("\n\n[" if original.comment else "[")
    )
    assert "МСК]\nПозвонил заказчику\nЖдём ответ" in saved.comment
    assert saved.contacts == "Новый контакт"
    assert saved.action == original.action
    assert not window.dirty and not window.note.toPlainText()
    assert window.edits["comment"].isReadOnly()
    before = saved.comment
    assert not window.add_note()
    assert (
        next(t for t in window.repository.all() if t.id == original.id).comment
        == before
    )


def test_history_failed_send_keeps_draft_and_does_not_duplicate(window, monkeypatch):
    original = window.current
    save = window.repository.save
    errors = []

    def fail(tender):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(window.repository, "save", fail)
    monkeypatch.setattr(window, "error", lambda error: errors.append(str(error)))
    window.note.setPlainText("Единственная запись")
    assert not window.add_note()
    assert window.note.toPlainText() == "Единственная запись"
    assert (
        next(t for t in window.repository.all() if t.id == original.id).comment
        == original.comment
    )
    monkeypatch.setattr(window.repository, "save", save)
    assert window.add_note()
    saved = next(t for t in window.repository.all() if t.id == original.id)
    assert saved.comment.count("Единственная запись") == 1
    assert not window.dirty


def test_multiline_fields_grow_and_shrink_without_inner_scrolling(window, app):
    window.details_toggle.setChecked(True)
    QTest.qWait(100)
    for name in ("customer", "title", "contacts"):
        edit = window.edits[name]
        edit.setPlainText("Короткий текст")
        app.processEvents()
        small = edit.height()
        edit.setPlainText("Очень длинная строка с переносом по ширине поля. " * 50)
        for _ in range(50):
            QTest.qWait(20)
            if edit.verticalScrollBar().maximum() == 0:
                break
        assert edit.height() > small * 2, name
        assert edit.verticalScrollBar().maximum() == 0, name
        edit.setPlainText("Снова короткий")
        QTest.qWait(100)
        assert edit.height() <= small + 2, name


def test_long_previous_notes_expand_without_changing_source(window, app):
    from PySide6.QtWidgets import QToolButton

    source = "\n".join(f"Исходная строка {i}" for i in range(12))
    window.edits["comment"].setPlainText(source)
    app.processEvents()
    entry = window.history_entries_layout.itemAt(0).widget()
    body = entry.findChild(QLabel, "historyBody")
    toggle = entry.findChild(QToolButton, "previousNotesToggle")
    assert body.isHidden() and body.text() == source
    toggle.click()
    assert body.isVisible()
    window.update_history()
    app.processEvents()
    assert (
        window.history_entries_layout.itemAt(0)
        .widget()
        .findChild(QLabel, "historyBody")
        .isVisible()
    )
    assert window.edits["comment"].toPlainText() == source


def test_history_entries_show_newest_first_without_changing_source(window, app):
    text = (
        "Старый комментарий\nБез даты и <b>разметки</b>\n\n"
        "[18.10.2026 10:00 МСК]\nПозвонил\nЖдём ответ\n\n"
        "[19.10.2026 11:20 МСК]\nПолучил ответ <test>"
    )
    window.load_tender(replace(window.current, comment=text))
    app.processEvents()
    entries = [window.history_entries_layout.itemAt(i).widget() for i in range(3)]
    assert entries[0].findChild(QLabel, "historyDate").text() == "19.10.2026 11:20 МСК"
    assert entries[0].findChild(QLabel, "historyBody").text() == "Получил ответ <test>"
    assert entries[1].findChild(QLabel, "historyBody").text() == "Позвонил\nЖдём ответ"
    assert (
        entries[2].findChild(QLabel, "historyDate").text()
        == "Прежние заметки · без даты"
    )
    assert (
        entries[2].findChild(QLabel, "historyBody").text()
        == "Старый комментарий\nБез даты и <b>разметки</b>"
    )
    assert (
        entries[2].findChild(QLabel, "historyBody").textFormat()
        == Qt.TextFormat.PlainText
    )
    assert window.candidate().comment == text and not window.dirty


def test_history_empty_state_and_composer(window, app):
    window.load_tender(replace(window.current, comment=""))
    assert window.history_entries_layout.count() == 1
    assert "Записей пока нет" in window.history_entries_layout.itemAt(0).widget().text()
    assert not window.send_button.isEnabled()
    window.note.setPlainText("   ")
    assert not window.send_button.isEnabled()
    window.note.setPlainText("Новая запись")
    assert window.send_button.isEnabled()
    assert window.add_note()
    assert "Записей: 1" in window.history_count.text()
    assert (
        window.history_entries_layout.itemAt(0)
        .widget()
        .findChild(QLabel, "historyBody")
        .text()
        == "Новая запись"
    )
    assert not window.send_button.isEnabled()


def test_history_long_entry_wraps_in_narrow_card(window, app):
    window.load_tender(
        replace(window.current, comment="Длинная история с переносами. " * 300)
    )
    from PySide6.QtWidgets import QToolButton

    window.history_entries.findChild(QToolButton, "previousNotesToggle").click()
    body = (
        window.history_entries_layout.itemAt(0)
        .widget()
        .findChild(QLabel, "historyBody")
    )
    for width in (1240, 900, 820):
        window.resize(width, 700)
        window.splitter.setSizes([275, width - 275])
        QTest.qWait(80)
        assert body.wordWrap()
        assert body.height() >= body.heightForWidth(body.width())
        assert window.scroll.horizontalScrollBar().maximum() == 0


def test_compact_card_resizes_without_clipping_fields(window):
    window.load_tender(window.repository.save(Tender(number="ТЕСТ-КАРТОЧКА", customer="Учебный завод",
                                                     title="Учебная поставка", comment="Учебный комментарий")))
    window.edits["contacts"].setPlainText(
        "Многострочный контакт с переносом по ширине. " * 20
    )
    for width in (900, 820):
        window.resize(width, 700)
        window.splitter.setSizes([275, width - 275])
        QTest.qWait(50)
        assert window.scroll.horizontalScrollBar().maximum() == 0
        for name in ("title", "customer", "contacts", "comment"):
            assert window.edits[name].verticalScrollBar().maximum() == 0, name


def test_single_status_filter_has_no_group_restrictions(window):
    assert not hasattr(window, "group")
    assert window.status_filter.currentText() == "Все статусы"
    assert window.status_filter.currentData() == "Все"
    assert window.list.count() == len(window.tenders)
    window.status_filter.setCurrentText("Победа")
    assert window.list.count() > 0
    visible_ids = {
        window.list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(window.list.count())
    }
    assert all(t.status == "Победа" for t in window.tenders if t.id in visible_ids)
    window.reset_filters()
    assert window.status_filter.currentData() == "Все"
    assert window.list.count() == len(window.tenders)


def test_old_group_setting_does_not_hide_tenders(repo, app, tmp_path):
    settings = Settings(tmp_path / "legacy-settings.json")
    settings.save(group="Закрытые", status="Все")
    win = Window(repo, settings, tmp_path)
    assert win.list.count() == len(repo.all())
    assert not hasattr(win, "group")
    win.close()


def test_card_focuses_identity_deadline_and_resource_buttons(window):
    assert window.identity_summary.isVisible()
    assert window.identity_editor.isHidden()
    assert window.date.isVisible() and window.time.isVisible()
    assert window.deadline_copy.isVisible()
    assert window.edits["url"].isHidden()
    assert window.edits["folder_path"].isHidden()
    assert window.etp_button.isVisible() and window.folder_button.isVisible()
    window.details_toggle.setChecked(True)
    assert window.identity_editor.isVisible() and window.identity_summary.isHidden()
    window.details_toggle.setChecked(False)
    assert not window.dirty


def test_deadline_edits_in_the_same_cell(window, app):
    before = window.candidate()
    assert window.scroll.isAncestorOf(window.deadline_view)
    assert window.date.parentWidget() == window.deadline_view
    assert window.time.parentWidget() == window.deadline_view
    assert not hasattr(window, "deadline_mode")
    assert not hasattr(window, "deadline_toggle")
    assert window.candidate() == before and not window.dirty


def test_deadline_controls_align_with_status(window, app):
    for width in (1240, 900):
        window.resize(width, 850)
        app.processEvents()
        controls = (window.status, window.date, window.time, window.deadline_clear, window.deadline_copy)
        tops = [control.mapTo(window.card, control.rect().topLeft()).y() for control in controls]
        assert max(tops) - min(tops) <= 1
        assert len({control.height() for control in controls}) == 1
        assert window.scroll.horizontalScrollBar().maximum() == 0


def test_contacts_move_right_on_wide_card_and_back_on_narrow_card(window, app):
    contacts = window.edits["contacts"]
    contacts.setPlainText("Иван Иванов\nТелефон и почта заказчика")
    expected = window.candidate()
    for width, side_by_side in ((1920, True), (900, False), (1920, True), (820, False)):
        window.resize(width, 850)
        window.splitter.setSizes([350, width - 350])
        QTest.qWait(80)
        information = window.details_panel.information
        contact_field = window.details_panel.contacts_column
        if side_by_side:
            assert contact_field.x() > information.geometry().right()
            assert abs(contact_field.y() - information.y()) <= 1
        else:
            assert contact_field.y() > information.geometry().bottom()
        assert contacts.verticalScrollBar().maximum() == 0
        assert window.scroll.horizontalScrollBar().maximum() == 0
        assert window.candidate() == expected
    window.dirty = False


def test_contacts_always_visible_empty_after_save_and_selection(window, app):
    window.load_tender(replace(window.current, contacts=""))
    panel = window.details_panel
    assert panel.contacts_field.isVisible()
    assert not hasattr(panel, "contacts_toggle")
    assert window.edits["contacts"].placeholderText()
    window.edits["contacts"].setPlainText("Контакт заказчика")
    assert window.save_tender()
    assert window.current.contacts == "Контакт заказчика"
    assert panel.contacts_field.isVisible()
    window.load_tender(window.current)
    assert panel.contacts_field.isVisible()
    assert window.edits["contacts"].toPlainText() == "Контакт заказчика"
    window.load_tender(replace(window.current, contacts=""))
    assert panel.contacts_field.isVisible()
    assert not window.dirty


def test_history_composer_is_ready_for_typing(window, app):
    assert window.note_composer.isVisible()
    assert window.note_toggle.isHidden()
    window.note.setFocus()
    window.note.setPlainText("Мой черновик")
    assert window.add_note()
    assert window.note_composer.isVisible()
    assert window.current.comment.endswith("Мой черновик")
    assert not window.note.toPlainText()


def test_tender_click_preserves_list_items_and_scroll(window, app):
    from PySide6.QtTest import QTest
    items = [window.list.item(i) for i in range(window.list.count())]
    target = items[1]
    window.list.scrollToItem(target)
    app.processEvents()
    point = window.list.visualItemRect(target).center()
    scroll = window.list.verticalScrollBar().value()
    for _ in range(3):
        QTest.mouseClick(window.list.viewport(), Qt.MouseButton.LeftButton, pos=point)
        app.processEvents()
        assert window.current.id == target.data(Qt.ItemDataRole.UserRole)
        assert window.list.verticalScrollBar().value() == scroll
        assert all(window.list.item(i) is item for i, item in enumerate(items))
    assert window.caption.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse


def test_link_dialog_validates_and_applies_without_losing_other_fields(window):
    before = window.current
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    window.edit_url()
    assert window.edits["url"].text() == before.url and not window.dirty

    def edit():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QInputDialog)
        ok = dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok
        )
        dialog.setTextValue("file:///C:/Windows")
        assert not ok.isEnabled()
        dialog.setTextValue("https://example.org/procedure/123")
        assert ok.isEnabled()
        ok.click()

    QTimer.singleShot(0, edit)
    window.edit_url()
    assert window.dirty
    assert window.edits["url"].text() == "https://example.org/procedure/123"
    assert window.etp_button.text() == "Открыть ЭТП ↗"
    assert window.save_tender()
    assert window.current.url == "https://example.org/procedure/123"
    assert (
        window.current.comment == before.comment
        and window.current.folder_path == before.folder_path
    )


def test_empty_resource_buttons_add_and_select_instead_of_opening(window, monkeypatch):
    calls = []
    window.edits["url"].clear()
    window.edits["folder_path"].clear()
    monkeypatch.setattr(window, "edit_url", lambda: calls.append("url"))
    monkeypatch.setattr(window, "choose_folder", lambda: calls.append("folder"))
    assert window.etp_button.text() == "Добавить ссылку"
    assert window.folder_button.text() == "Выбрать папку"
    window.open_url()
    window.open_folder()
    assert calls == ["url", "folder"]


def test_selected_row_accent_and_dark_status_badges(window):
    from tender_app.window import STATUS_COLORS

    for status in ("В работе", "Победа"):
        window.status_filter.setCurrentText(status)
        window.list.setCurrentRow(0)
        QTest.qWait(100)
        item = window.list.item(0)
        rect = window.list.visualItemRect(item)
        image = window.list.viewport().grab().toImage()
        ratio = image.devicePixelRatio()
        assert (
            image.pixelColor(round(1 * ratio), round((rect.top() + 10) * ratio)).name()
            == "#8ba9ff"
        )
        assert item.data(Qt.ItemDataRole.UserRole + 1) == status
        foreground, background = STATUS_COLORS[status]
        assert QColor(foreground).lightness() > QColor(background).lightness()


def test_deadline_fields_do_not_invent_time(window, monkeypatch):
    errors = []
    monkeypatch.setattr(window, "error", lambda error: errors.append(str(error)))
    window.new_tender()
    window.edits["number"].setText("СРОК-001")
    assert not window.date.text() and not window.time.text()
    assert window.date.isVisible() and window.time.isVisible()
    window.date.setDate(QDate(2026, 11, 5))
    assert window.save_tender()
    assert window.current.deadline_time is None
    window.note.setPlainText("Не потерять черновик")
    before = window.current
    window.time.setText("25:60")
    assert not window.save_tender()
    assert window.current == before and window.note.toPlainText() == "Не потерять черновик"
    assert errors
    window.time.setText("00:00")
    assert window.save_tender()
    assert window.current.deadline_date == "2026-11-05"
    assert window.current.deadline_time == "00:00"
    assert window.current.deadline_timezone == "Europe/Moscow"
    window.time.clear()
    assert window.save_tender()
    assert window.current.deadline_time is None
    window.clear_deadline()
    assert window.save_tender()
    assert window.current.deadline_date is None and window.current.deadline_time is None


def test_deadline_date_can_be_typed_with_keyboard(window):
    window.date.setFocus()
    window.date.selectAll()
    QTest.keyClicks(window.date, "21.10.2026")
    window.time.clear()
    QTest.keyClick(window.date, Qt.Key.Key_Tab)
    assert window.save_tender()
    assert window.current.deadline_date == "2026-10-21"
    assert window.current.deadline_time is None


def test_explicit_clear_of_invalid_import_preserves_raw_without_false_warning(window):
    original = window.repository.save(Tender(number="ТЕСТ-ДАТА", deadline_needs_review=1,
                                             deadline_raw="неверная дата"))
    window.load_tender(original)
    QTest.mouseClick(window.deadline_clear, Qt.MouseButton.LeftButton)
    assert window.save_tender()
    assert window.current.deadline_raw == original.deadline_raw
    assert not window.current.deadline_needs_review
    assert deadline_label(window.current) == "Срок не указан"
    assert not any(
        "Проверьте срок" in label.text() for label in window.findChildren(QLabel)
    )


def test_splitter_extremes_keep_card_fields_visible_and_list_rows_wrap(window):
    window.tenders.append(
        Tender(
            id=9999,
            number="ДЛИННЫЙ-НОМЕР-2026",
            customer="Очень длинное название заказчика " * 4,
            title="Подробное наименование закупки с длинным описанием " * 8,
        )
    )
    window.refresh_list()
    window.resize(1400, 900)
    window.splitter.setSizes([750, 650])
    QTest.qWait(80)
    item = next(
        window.list.item(i)
        for i in range(window.list.count())
        if window.list.item(i).data(Qt.ItemDataRole.UserRole) == 9999
    )
    wide_height = window.list.visualItemRect(item).height()
    window.splitter.setSizes([0, 10000])
    QTest.qWait(100)
    narrow_height = window.list.visualItemRect(item).height()
    assert narrow_height > wide_height
    assert window.list.textElideMode() == Qt.TextElideMode.ElideNone
    assert window.list.horizontalScrollBar().maximum() == 0
    window.splitter.setSizes([10000, 0])
    QTest.qWait(100)
    assert window.scroll.horizontalScrollBar().maximum() == 0
    number = window.edits["number"]
    assert number.width() >= number.fontMetrics().horizontalAdvance(number.text()) + 16


def test_review_message_not_shown_for_imported_deadline(window):
    original = window.repository.save(Tender(number="ТЕСТ-ДАТА", deadline_needs_review=1,
                                             deadline_raw="неверная дата"))
    window.load_tender(original)
    window.refresh_list()
    assert "проверить срок" not in deadline_label(original).casefold()
    assert "проверьте срок" not in "\n".join(
        label.text().casefold() for label in window.findChildren(QLabel)
    )
    assert window.candidate() == original


def test_popup_remains_readable_with_dark_system_palette(window, app):
    original = app.palette()
    dark = QPalette(original)
    for role in (
        QPalette.ColorRole.Base,
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Button,
    ):
        dark.setColor(role, QColor("#101010"))
    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.ButtonText,
    ):
        dark.setColor(role, QColor("#eeeeee"))
    app.setPalette(dark)
    try:
        for combo in (window.status, window.status_filter):
            combo.showPopup()
            app.processEvents()
            palette = combo.view().palette()
            assert palette.color(QPalette.ColorRole.Base).lightness() < 80
            assert palette.color(QPalette.ColorRole.Text).lightness() > 200
            assert palette.color(QPalette.ColorRole.HighlightedText).lightness() > 235
            assert palette.color(QPalette.ColorRole.Highlight).lightness() < 150
            combo.hidePopup()
        window.date.open_calendar()
        calendar = window.date.calendar_popup.findChild(QCalendarWidget)
        calendar.ensurePolished()
        from PySide6.QtWidgets import QAbstractItemView

        palette = calendar.findChild(QAbstractItemView).palette()
        assert palette.color(QPalette.ColorRole.Base).lightness() < 80
        assert palette.color(QPalette.ColorRole.Text).lightness() > 200
        window.date.calendar_popup.reject()
    finally:
        app.setPalette(original)


def test_select_after_guard_save_keeps_card_and_list_in_sync(window, app, monkeypatch):
    target_id = window.list.item(1).data(Qt.ItemDataRole.UserRole)
    window.edits['contacts'].setPlainText('Учебное изменение')
    monkeypatch.setattr(window, 'guard_changes', window.save_tender)
    window.list.setCurrentRow(1)
    app.processEvents()
    assert window.current.id == target_id
    assert window.list.currentItem().data(Qt.ItemDataRole.UserRole) == target_id
