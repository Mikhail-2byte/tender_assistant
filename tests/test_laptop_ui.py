"""Laptop layout regressions. Tests use generated data only."""
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QLabel
from tender_app.execution_window import ExecutionWindow, specification_view
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_positions import PositionTable
from tender_app.settings import Settings


@pytest.fixture
def laptop(tmp_path, app):
    db = tmp_path / "fictional.db"
    create_demo_database(db)
    repo = ExecutionRepository(db)
    settings = Settings(tmp_path / "layout.json")
    win = ExecutionWindow(repo, settings, tmp_path)
    win.resize(1366, 768)
    win.show()
    app.processEvents()
    yield win
    win.dirty = False
    win.execution.comment_drafts.clear()
    win.close()
    repo.close()
    app.processEvents()


def test_icon_rail_and_persisted_expansion(laptop, app):
    win = laptop
    assert win.app_sidebar.width() == 56
    assert all(button.text() == "" and button.toolTip() for button in win.navigation_buttons)
    win.sidebar_toggle.click()
    app.processEvents()
    assert win.app_sidebar.width() == 176
    assert all(button.text() for button in win.navigation_buttons)
    win.close()
    second = ExecutionWindow(win.repository, win.settings, win.documents_root)
    second.show()
    app.processEvents()
    try:
        assert second.sidebar_expanded
        assert second.app_sidebar.width() == 176
        second.sidebar_toggle.click()
        second.close()
        assert win.settings.get("sidebar_expanded", True) is False
    finally:
        second.dirty = False
        second.close()


@pytest.mark.parametrize("width,height", [(1024, 600), (1152, 640), (1280, 640), (1366, 768)])
def test_registry_fits_all_columns_on_laptop(laptop, app, width, height):
    from tender_app.models import Tender
    win = laptop
    win.registry.set_tenders([Tender(
        id=i + 1, number=f"32616477{i:03}", customer="Учебный заказчик",
        title="Учебная поставка " * 10, status="Податься",
        deadline_date="2026-11-01", deadline_time="12:00",
        platform="Учебная площадка", comment="Полный комментарий " * 30,
    ) for i in range(13)], unseen_ids={1})
    win.resize(width, height)
    QTest.qWait(50)
    table = win.registry.table
    assert win.width() == width
    assert table.horizontalScrollBar().maximum() == 0, (
        table.viewport().width(), table.horizontalHeader().length(),
        [table.columnWidth(i) for i in range(7)])
    assert table.horizontalHeader().length() <= table.viewport().width()
    assert all(not table.isColumnHidden(i) for i in range(7))
    assert table.item(0, 3).text() == "01.11.2026\n12:00"
    assert table.columnWidth(0) >= table.fontMetrics().horizontalAdvance(table.item(0, 0).text()) + 32
    assert len(table.item(0, 6).toolTip()) > 400
    assert table.verticalScrollBar().maximum() == 0
    win.sidebar_toggle.click()
    QTest.qWait(50)
    assert table.horizontalScrollBar().maximum() == 0
    win.registry.search.setText("Нет такой записи")
    QTest.qWait(50)
    assert table.rowCount() == 0
    assert table.horizontalScrollBar().maximum() == 0


def test_position_columns_fit_and_restore_saved_preferences(laptop, app):
    positions = specification_view(laptop.repository.specifications()[0])["positions"]
    preferred = [220, 110, 70, 105, 95, 115, 105, 270, 90]
    table = PositionTable(positions, False, widths=preferred.copy())
    table.show()
    changed = []
    table.widths_changed.connect(changed.append)
    try:
        for width in (1500, 520, 1120, 800, 1500):
            table.resize(width, table.height())
            QTest.qWait(30)
            assert table.horizontalScrollBar().maximum() == 0
            assert table.horizontalHeader().length() <= table.viewport().width()
            assert table.saved_widths == preferred
        assert [table.columnWidth(i) for i in range(9)] == preferred
        assert not changed
    finally:
        table.close()


@pytest.mark.parametrize("width", [1024, 1152, 1366])
def test_search_is_centered_and_searches_both_sections(laptop, app, width):
    win = laptop
    win.resize(width, 768)
    QTest.qWait(25)
    center = win.global_search.mapTo(win, win.global_search.rect().center()).x()
    assert abs(center - win.width() / 2) < 8
    before = win.repository.all(), win.repository.specifications()
    win.global_search.setText("ДЕМО-101")
    QTest.qWait(260)
    assert win.search.text() == win.execution.search.text() == ""
    assert win.search_results.isVisible()
    assert win.search_results.list.count() >= 2
    assert win.search_results.geometry().right() < win.width()
    assert (win.repository.all(), win.repository.specifications()) == before


def test_navigation_gets_primary_height_and_filters_remain_active(laptop, app):
    win = laptop
    win.pages.setCurrentIndex(1)
    QTest.qWait(25)
    panel = win.execution
    assert panel.filters_panel.isHidden()
    assert panel.tree.height() > panel.splitter.height() * .70
    panel.filters_toggle.click()
    assert not panel.filters_panel.isHidden()
    panel.overdue.setChecked(True)
    panel.filters_toggle.click()
    assert panel.filters_panel.isHidden()
    assert panel.overdue.isChecked()
    assert "1" in panel.filters_toggle.text()
    win.global_search.setText("ДЕМО-101")
    panel.reset_filters()
    assert win.global_search.text() == "ДЕМО-101"
    assert win.search.text() == panel.search.text() == ""
    assert not panel.overdue.isChecked()
    assert "1" not in panel.filters_toggle.text()


@pytest.mark.parametrize("width,assistant", [(1366,False),(1152,False),(1024,False),(1366,True)])
def test_position_rows_stay_compact_on_narrow_panels(laptop, app, width, assistant):
    win = laptop
    spec = next(s for s in win.repository.specifications() if len(s.positions) == 3)
    win.open_specification(spec.id)
    win.resize(width, 768)
    win.execution_assistant_toggle.setChecked(assistant)
    QTest.qWait(40)
    if assistant:
        chat=win.execution_assistant
        controls=(chat.model_state,chat.model_selector,chat.send_button)
        centers=[control.geometry().center().y() for control in controls]
        assert max(centers)-min(centers)<=1
    table = win.execution.position_tables[0]
    assert table.rowHeight(0) == 46
    assert table.horizontalHeader().isVisible()
    assert table.wordWrap()
    assert table.item(0, 1).text() == "4 / 10 шт"
    assert table.item(0, 5).text() == spec.positions[0].supplier
    assert table.item(0, 7).toolTip()


def test_two_level_text_details_and_resize(laptop, app):
    positions = specification_view(laptop.repository.specifications()[0])["positions"]
    positions[0]["name"] = "Длинное название " * 30
    positions[0]["working_note"] = "Рабочая заметка " * 30
    positions[0]["comments"] = [("26.10.2026 12:00 МСК", "Машина выехала"), ("25.10.2026", "Старое событие")]
    table = PositionTable(positions, False)
    table.show()
    app.processEvents()
    try:
        assert table.rowCount() == len(positions)
        assert table.item(0, 0).toolTip() == positions[0]["name"]
        assert "Машина выехала" in table.item(0, 7).toolTip()
        assert "Машина выехала" in table.item(0, 7).text()
        assert "Старое событие" not in table.item(0, 7).text()
        before = [table.item(0, i).text() for i in range(table.columnCount())]
        for width in (1500, 520, 1120):
            table.resize(width, table.height())
            QTest.qWait(25)
            assert [table.item(0, i).text() for i in range(table.columnCount())] == before
            assert table.rowHeight(0) == 46
    finally:
        table.close()


def test_table_copies_cells_notes_and_unique_names(laptop, app):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTableWidgetSelectionRange
    positions = specification_view(laptop.repository.specifications()[0])["positions"]
    positions[0]["comments"] = [("28.10.2026 12:00 МСК", "Машина выехала\nОжидаем поставку")]
    table = PositionTable(positions, True)
    table.show()
    app.processEvents()
    try:
        table.clearSelection()
        table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, 1), True)
        QTest.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        assert QApplication.clipboard().text() == table.item(0, 0).text() + "\t" + table.item(0, 1).text()
        table.copy_cells(True)
        copied = QApplication.clipboard().text().splitlines()
        assert len(copied) == table.rowCount() + 1
        assert copied[0].split("\t") == [" ".join(table.horizontalHeaderItem(i).text().split()) for i in range(table.columnCount())]
        assert len(copied[1].split("\t")) == table.columnCount()
        assert "Машина выехала Ожидаем поставку" in copied[1]
        QTest.keyClick(table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        table.copy_position_names()
        assert QApplication.clipboard().text() == "\n".join(p["name"] for p in positions)
        table.clearSelection()
        QApplication.clipboard().setText("Сохранить буфер")
        table.copy_cells()
        assert QApplication.clipboard().text() == "Сохранить буфер"
    finally:
        table.close()


def test_position_actions_and_read_only(laptop, app):
    positions = specification_view(laptop.repository.specifications()[0])["positions"]
    for read_only in (False, True):
        table = PositionTable(positions, read_only)
        edited, shipped, comments = [], [], []
        table.edit_requested.connect(lambda pid, field: edited.append((pid, field)))
        table.shipped_requested.connect(shipped.append)
        table.comment_requested.connect(comments.append)
        try:
            table.cellDoubleClicked.emit(0, 1)
            table.cellDoubleClicked.emit(0, 3)
            table.cellDoubleClicked.emit(1, 0)
            table.cellDoubleClicked.emit(0, 7)
            table.cellDoubleClicked.emit(0, 8)
            if read_only:
                assert not edited and not shipped
            else:
                assert comments == [positions[0]["id"]]
                assert shipped == [positions[0]["id"]]
                assert edited == [(positions[0]["id"], "due"), (positions[0]["id"], "transport")]
        finally:
            table.close()


def test_remaining_filter_keeps_unknown_and_partial_positions(laptop, app):
    from copy import deepcopy
    position = specification_view(laptop.repository.specifications()[0])["positions"][0]
    positions = [deepcopy(position) for _ in range(4)]
    for p, shipped in zip(positions, (None, 0, position["ordered"] / 2, position["ordered"])):
        p["customer_shipped"] = shipped
    table = PositionTable(positions, True)
    table.show()
    app.processEvents()
    try:
        assert table.item(0, 1).text().startswith("? /")
        assert table.item(0, 2).text() == "не указано"
        assert table.item(3, 2).text() == "✓ Всё"
        height = table.height()
        table.filter_remaining(True)
        assert not any(table.isRowHidden(row) for row in range(3))
        assert table.isRowHidden(3)
        assert table.height() < height
        table.filter_remaining(False)
        assert table.height() == height
    finally:
        table.close()


def test_position_deadline_days_and_vehicle_are_visible(laptop, app):
    from datetime import date
    positions = specification_view(laptop.repository.specifications()[0])["positions"]
    positions[0]["due"] = "2026-10-30"
    positions[0]["transport"] = "АВТО 76"
    positions[0]["customer_shipped"] = 0
    table = PositionTable(positions, False, fallback_due="2026-11-05", today=date(2026, 10, 28))
    try:
        assert table.item(0, 3).text() == "30.10.2026"
        assert table.item(0, 6).text() == "2"
        assert table.item(0, 8).text() == "АВТО 76"
        assert table.rowCount() == len(positions)
        edited = []
        table.edit_requested.connect(lambda pid, field: edited.append(field))
        for column in (4, 5, 8):
            table.cellDoubleClicked.emit(0, column)
        assert edited == ["supplier_document", "supplier", "transport"]
    finally:
        table.close()


def test_sticky_header_tracks_scroll_width_and_horizontal_offset(laptop, app):
    from dataclasses import replace
    spec = next(s for s in laptop.repository.specifications() if len(s.positions) == 3)
    spec.positions.extend(replace(spec.positions[0], id=None, comments=[]) for _ in range(35))
    laptop.repository.save_specification(spec)
    laptop.refresh_execution()
    laptop.open_specification(spec.id)
    QTest.qWait(300)
    panel = laptop.execution
    table = panel.position_tables[0]
    pinned = table.pinned_header
    assert not pinned.isVisible()
    from PySide6.QtCore import QPoint
    panel.scroll.verticalScrollBar().setValue(table.mapTo(panel.scroll.widget(), QPoint(0, 0)).y() + 20)
    QTest.qWait(30)
    assert pinned.isVisible(), (table.height(), table.mapTo(panel.scroll.viewport(), QPoint(0, 0)).y(), panel.scroll.verticalScrollBar().value(), panel.scroll.verticalScrollBar().maximum(), table.isVisible())
    assert pinned.y() == 0
    pinned.resizeSection(0, 260)
    assert table.columnWidth(0) == 260
    table.setColumnWidth(3, 900)
    table.horizontalScrollBar().setValue(120)
    QTest.qWait(30)
    assert pinned.offset() == table.horizontalScrollBar().value()
    panel.scroll.verticalScrollBar().setValue(0)
    QTest.qWait(30)
    assert not pinned.isVisible()


def test_sticky_header_queued_update_after_table_deletion(app):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QScrollArea
    from shiboken6 import isValid
    from tender_app.execution_positions import StickyPositionHeader

    scroll = QScrollArea()
    table = PositionTable([], read_only=True)
    scroll.setWidget(table)
    pinned = StickyPositionHeader(table, scroll)
    callback = pinned.update_position
    table.deleteLater()
    QCoreApplication.sendPostedEvents(table, QEvent.Type.DeferredDelete)
    assert not isValid(table)
    callback()
    QCoreApplication.sendPostedEvents(pinned, QEvent.Type.DeferredDelete)
    assert not isValid(pinned)
    callback()
    app.processEvents()
    scroll.close()


@pytest.mark.parametrize("days, shipped, inactive, color", [
    (-1, None, False, "#ff9393"), (0, None, False, "#ffd27a"),
    (7, None, False, "#ffd27a"), (8, None, False, None),
    (-1, 1, False, None), (-1, None, True, None),
])
def test_position_deadline_colors(laptop, app, days, shipped, inactive, color):
    from datetime import date, timedelta
    position = specification_view(laptop.repository.specifications()[0])["positions"][0]
    today = date(2026, 10, 24)
    position.update(due=(today + timedelta(days=days)).isoformat(), customer_shipped=shipped)
    table = PositionTable([position], True, today=today, inactive=inactive)
    try:
        item = table.item(0, 3)
        if color:
            assert item.foreground().color().name() == color
        else:
            assert item.foreground().style().value == 0
    finally:
        table.close()
