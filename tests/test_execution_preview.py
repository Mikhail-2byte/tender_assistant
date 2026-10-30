import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit

from tender_app.execution_preview import ExecutionPreview


@pytest.fixture
def preview(app):
    window = ExecutionPreview()
    window.show()
    app.processEvents()
    yield window.panel
    window.close()
    app.processEvents()


def visible_ids(panel):
    return [s["id"] for s in panel.visible_specifications]


def card_text(panel):
    QApplication.processEvents()
    labels = []
    for index in range(panel.card_layout.count()):
        widget = panel.card_layout.itemAt(index).widget()
        labels.extend([widget] if isinstance(widget,QLabel) else widget.findChildren(QLabel))
    texts = [label.text() for label in labels]
    texts.extend(editor.toPlainText() for editor in panel.card.findChildren(QPlainTextEdit) if editor.isReadOnly())
    return "\n".join(texts)


def test_default_contract_navigation_and_archive(preview):
    assert set(visible_ids(preview)) == {201,101,202}
    assert preview.tree.topLevelItemCount() == 3
    assert preview.without_specifications == 1
    assert preview.tree.columnCount() == 1
    assert preview.splitter.orientation() == Qt.Orientation.Horizontal
    parent = preview.tree.topLevelItem(0)
    assert parent.childCount() == 2
    assert not parent.isExpanded()
    assert preview.current_specification_id is None
    assert set(preview.spec_links) == {201,202}
    preview.state.setCurrentText("Архив")
    assert visible_ids(preview) == [401]
    assert preview.without_specifications == 0
    preview.open_specification(401)
    assert "Пользователь подтвердил завершение" in card_text(preview)
    preview.state.setCurrentText("Все")
    assert set(visible_ids(preview)) == {101,201,202,401}


def test_mixed_contract_shows_only_selected_list_and_archived_is_not_overdue(preview):
    spec = next(s for s in preview.specifications if s["id"] == 201)
    spec["archived"] = True
    preview.refresh()
    assert 201 not in visible_ids(preview)
    assert 201 not in preview.spec_links
    assert 202 in preview.spec_links
    preview.state.setCurrentText("Архив")
    assert set(visible_ids(preview)) == {201, 401}
    assert 201 in preview.spec_links and 202 not in preview.spec_links
    preview.search.setText("УПРОЧНЁННОЙ")
    assert visible_ids(preview) == [201]
    preview.overdue.setChecked(True)
    assert visible_ids(preview) == []


def test_contract_opens_specification_and_back_without_losing_positions(preview):
    preview.spec_links[201].click()
    assert preview.current_specification_id == 201
    assert preview.tree.currentItem().parent().isExpanded()
    text = card_text(preview)
    assert "Отправлено поставщиком" not in text
    assert len(preview.position_tables) == 1
    assert preview.position_tables[0].rowCount() == 3
    assert preview.position_tables[0].positions[0]["transport"] == "Автомобиль ДЕМО-01"
    key = preview.tree.currentItem().parent().data(0,Qt.ItemDataRole.UserRole)
    preview.open_contract(key)
    assert preview.current_specification_id is None
    assert set(preview.spec_links) == {201,202}


def test_search_contract_supplier_application_and_position(preview):
    for query in ("УПРОЧНЁННОЙ", "ЗАВОД «МАКЕТ»", "ДЕМО-П/1"):
        preview.search.setText(query)
        assert 201 in visible_ids(preview)
    preview.search.setText("УПРОЧНЁННОЙ")
    assert visible_ids(preview) == [201]
    assert preview.tree.topLevelItem(0).isExpanded()
    preview.search.setText("не существующая позиция")
    assert visible_ids(preview) == []
    assert "Ничего не найдено" in card_text(preview)


def test_navigation_shows_specification_date_tender_applications_deadline_and_status(preview):
    spec = next(s for s in preview.specifications if s["id"] == 201)
    spec["title"] = "Позиции из реестра 01.09.2026"
    spec["positions"][0]["supplier_document"] = "TTPM-TC-20240116 Прил.227 от 07/05/2026"
    spec["positions"][1]["supplier_document"] = "TTPM-TC-20240116 Прил.227 от 07/05/2026"
    spec["positions"][2]["supplier_document"] = "YGIT-TC-05-2024 Прил. № 66 от 22/04/2026"
    preview.refresh()
    preview.open_specification(201)
    text = preview.tree.currentItem().text(0)
    assert text == ("Спецификация СП-02 от 01.10.2026\nТендер № ДЕМО-202\n"
                    "Приложения №: 227, 66\nПоставить до: 18.10.2026\n"
                    "Статус: Частично отгружено · Просрочка")
    assert spec["title"] not in text
    assert spec["title"] not in card_text(preview)
    assert spec["stage"] not in card_text(preview)


def test_navigation_uses_agreed_deadline_without_repeating_specification_date(preview):
    spec = next(s for s in preview.specifications if s["id"] == 202)
    spec["number"] = "СП-03 от 01/10/2026"
    preview.refresh()
    preview.open_specification(202)
    text = preview.tree.currentItem().text(0)
    assert text.startswith("Спецификация СП-03 от 01.10.2026\n")
    assert "Поставить до: 05.11.2026" in text
    assert "22.10.2026" not in text
    spec["date"] = None
    spec["due"] = spec["revised"] = None
    preview.refresh()
    assert "дата не указана" in preview.tree.currentItem().text(0)
    assert "Поставить до: не указано" in preview.tree.currentItem().text(0)


def test_navigation_does_not_invent_missing_application_numbers(preview):
    from tender_app.execution_panel import application_numbers

    assert application_numbers({"positions": []}) == "не указаны"
    assert application_numbers({"positions": [{"supplier_document": "не нашли в папке"}]}) == "не указаны"
    assert application_numbers({"positions": [{"supplier_document": "202"}]}) == "202"
    assert application_numbers({"positions": [{"supplier_document": "Приложение ДЕМО-П/1"}]}) == "ДЕМО-П/1"


def test_application_table_has_compact_requested_fields(preview):
    preview.open_specification(201)
    assert len(preview.position_tables) == 1
    table = preview.position_tables[0]
    assert [table.horizontalHeaderItem(i).text() for i in range(table.columnCount())] == [
        "Позиция / чертёж", "Отгружено /\nвсего", "Осталось", "Срок поставки",
        "Приложение", "Изготовитель", "Осталось дней", "Комментарий", "Авто"]
    assert table.item(0, 1).text() == "4 / 10 шт"
    assert "оставшиеся изделия" in table.item(0, 7).text()
    assert table.item(0, 5).text() == "Завод «Макет»"
    assert "ДЕМО-П/1" in table.item(0, 4).text()
    assert table.item(1, 5).text() == "Мастерская «Пример»"
    assert not hasattr(preview, "application_filter")
    preview.open_specification(202)
    assert preview.position_tables[0].item(0, 3).text() == "05.11.2026"


def test_stage_matches_positions_and_overdue_uses_agreed_extension(preview):
    preview.stage.setCurrentText("Готово к отгрузке")
    assert visible_ids(preview) == [201]
    preview.reset_filters()
    preview.overdue.setChecked(True)
    assert visible_ids(preview) == [201]
    preview.open_specification(201)
    assert "Продление запрошено, но не согласовано" in next(s for s in preview.specifications if s["id"] == 201)["risk"]
    preview.open_specification(202)
    text = card_text(preview)
    assert "22.10.2026" not in text and "05.11.2026" in text
    assert preview.position_tables[0].item(0, 3).text() == "05.11.2026"


def test_links_use_ids_for_duplicate_tender_numbers(preview):
    preview.open_tender(2)
    assert preview.window().pages.currentIndex() == 0
    assert "Комбинат «Учебный»" in preview.window().tender_details.toPlainText()
    preview.open_tender(3)
    assert "Фабрика «Вымышленная»" in preview.window().tender_details.toPlainText()
    assert "Спецификаций пока нет" in preview.window().tender_details.toPlainText()
    preview.window().follow_link(QUrl("spec:202"))
    assert preview.window().pages.currentIndex() == 1
    assert preview.current_specification_id == 202
    assert "История тендера ведётся отдельно" in card_text(preview)


@pytest.mark.parametrize("width", [900,1240])
def test_native_card_at_supported_widths_keeps_navigation_and_scroll(preview,app,width):
    preview.window().resize(width,880)
    preview.open_specification(201)
    app.processEvents()
    QTest.qWait(30)
    assert preview.window().width() == width
    assert preview.tree.isVisible() and preview.scroll.isVisible()
    assert preview.tree.width() >= 200 and preview.scroll.width() >= 400
    assert all(table.rowHeight(0) == 46 for table in preview.position_tables)
    assert all(table.wordWrap() for table in preview.position_tables)
    selected = preview.current_key
    preview.refresh()
    assert preview.current_key == selected
    assert preview.position_tables[0].rowCount() == 3
