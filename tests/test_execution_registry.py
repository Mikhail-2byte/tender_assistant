import json
import sqlite3
import sys
from datetime import date
from decimal import Decimal

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAccessible, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QTreeWidget

from tender_app.execution_groups import contract_key
from tender_app.execution_models import Position, Specification
from tender_app.execution_registry import ExecutionRegistry, pending_due
from tender_app.execution_registry_store import owner_key
from tender_app.execution_registry_table import CELL
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.models import Tender
from tender_app.execution_window import ExecutionWindow
from tender_app.settings import Settings


@pytest.fixture
def storage(tmp_path):
    path = tmp_path / "fictional.db"
    create_demo_database(path)
    repo = ExecutionRepository(path)
    yield repo
    repo.close()


@pytest.fixture
def registry(storage, app):
    widget = ExecutionRegistry(storage)
    widget.resize(1366, 768)
    widget.show()
    app.processEvents()
    yield widget
    widget.cancel_edit()
    widget.close()


def first_position(registry, app):
    registry.expand_all()
    for _ in range(6):
        app.processEvents()
    table = next(t for t in registry.tables if t.level == "position" and t.topLevelItem(0).data(0, CELL))
    item = table.topLevelItem(0)
    return table, item, item.data(0, CELL)["owner"]


def edit_cell(table, item, column, app):
    table.setCurrentItem(item, column)
    table.editItem(item, column)
    app.processEvents()
    editor = table.registry.active_editor
    assert editor is not None
    return editor


def test_due_excludes_shipped_and_inactive_and_uses_fallback():
    spec = Specification(due="2026-11-20", revised="2026-11-25", positions=[
        Position(name="Поставлено", due="2026-01-01", ordered=Decimal(2), customer_shipped=Decimal(2)),
        Position(name="Неизвестно", due="2026-11-26", customer_shipped=None),
        Position(name="Остаток", ordered=Decimal(2), customer_shipped=Decimal(1)),
    ])
    assert pending_due(spec) == "2026-11-25"
    spec.positions[2].customer_shipped = Decimal(2)
    assert pending_due(spec) == "2026-11-26"
    spec.archived = True
    assert pending_due(spec) is None
    spec.archived = False
    spec.positions.clear()
    assert pending_due(spec) == "2026-11-25"


def test_manual_comments_sync_but_events_do_not_replace_current(storage):
    spec = storage.specifications()[0]
    p = spec.positions[0]
    owner = ("position", spec.id, p.id)
    storage.set_position_comment(spec.id, p.id, "Едет машиной 17")
    assert storage.registry_comment(owner) == "Едет машиной 17"
    storage.set_position_shipped(spec.id, p.id, "0.5")
    storage.update_position_field(spec.id, p.id, "transport", "Машина 17")
    assert storage.registry_comment(owner) == "Едет машиной 17"
    assert storage.update_registry_comment(owner, "", "Едет машиной 17")
    assert storage.registry_comment(owner) == ""
    entries = storage.registry_comment_entries(owner)
    assert any(e["message"] == "" for e in entries)
    assert any(e["message"] == "Едет машиной 17" for e in entries)
    assert storage.specifications()[0].positions[0].comments[0][1] == ""
    so = ("specification", spec.id)
    storage.add_specification_comment(spec.id, "Согласовано")
    assert storage.registry_comment(so) == "Согласовано"
    storage.update_registry_comment(so, "Ожидаем", "Согласовано")
    assert storage.specifications()[0].comments[0][1] == "Ожидаем"


def test_deleting_legacy_manual_comment_restores_previous_current(storage):
    spec = storage.specifications()[0]
    owner = ("position", spec.id, spec.positions[0].id)
    storage.set_position_comment(spec.id, owner[2], "Первая заметка")
    storage.set_position_comment(spec.id, owner[2], "Вторая заметка")
    comment_id = storage.comment_entries(spec.id, owner[2])[0]["id"]
    storage.delete_execution_comment(spec.id, owner[2], comment_id)
    assert storage.registry_comment(owner) == "Первая заметка"
    assert any(e.get("source_deleted") and e["message"] == "Вторая заметка"
               for e in storage.registry_comment_entries(owner))


def test_comment_contract_identity_move_export_restore(storage, tmp_path):
    spec = storage.specifications()[0]
    tender = next(t for t in storage.all() if t.id == spec.tender_id)
    owner = ("contract", contract_key(tender, {"contract": spec.contract}))
    storage.update_registry_comment(owner, "Общий комментарий", "")
    other = storage.save(Tender(number="ДРУГОЙ", customer="Другой заказчик", status="Победа"))
    second = storage.save_specification(Specification(tender_id=other.id, number="1", contract=spec.contract))
    assert storage.registry_comment(("contract", contract_key(other, {"contract": second.contract}))) == ""
    backup = storage.backup(manual=True)
    storage.update_registry_comment(owner, "Другая версия", "Общий комментарий")
    storage.restore(backup)
    assert storage.registry_comment(owner) == "Общий комментарий"
    spec.contract = "Новый договор"
    storage.save_specification(spec)
    assert storage.registry_comment(("contract", contract_key(tender, {"contract": spec.contract}))) == ""
    assert storage.registry_comment(owner) == "Общий комментарий"
    target = tmp_path / "export.json"
    storage.export_execution(target)
    assert any(row["message"] == "Общий комментарий" for row in json.loads(target.read_text(encoding="utf-8"))["registry_comments"])


def test_conflicts_preserve_both_comment_and_field(storage):
    spec = storage.specifications()[0]
    owner = ("position", spec.id, spec.positions[0].id)
    second = ExecutionRepository(storage.path)
    try:
        before = storage.registry_comment(owner)
        second.update_registry_comment(owner, "Чужая правка", before)
        with pytest.raises(ValueError, match="другом месте"):
            storage.update_registry_comment(owner, "Моя правка", before)
        old = spec.positions[0].transport
        second.update_registry_field(owner, "transport", "Авто Б", old)
        with pytest.raises(ValueError, match="другом месте"):
            storage.update_registry_field(owner, "transport", "Авто А", old)
        assert storage.specifications()[0].positions[0].transport == "Авто Б"
    finally:
        second.close()


def test_v10_migration_freezes_display_without_changing_legacy(storage):
    path = storage.path
    spec = storage.specifications()[0]
    storage.set_position_comment(spec.id, spec.positions[0].id, "Последняя запись")
    before = storage.all(), storage.specifications()
    storage.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE execution_registry_comments")
        conn.execute("PRAGMA user_version=9")
    (path.parent / "before-execution-v10.db").unlink()
    migrated = ExecutionRepository(path)
    try:
        assert (migrated.all(), migrated.specifications()) == before
        assert migrated.registry_comment(("position", spec.id, spec.positions[0].id)) == "Последняя запись"
        count = migrated.connection.execute("SELECT count(*) FROM execution_registry_comments").fetchone()[0]
        migrated.close()
        migrated = ExecutionRepository(path)
        assert migrated.connection.execute("SELECT count(*) FROM execution_registry_comments").fetchone()[0] == count
    finally:
        migrated.close()


def test_v10_failure_rolls_back_and_old_backup_restores(storage, monkeypatch):
    path = storage.path
    before = storage.all(), storage.specifications()
    storage.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE execution_registry_comments")
        conn.execute("PRAGMA user_version=9")
    (path.parent / "before-execution-v10.db").unlink()
    check = ExecutionRepository.check_structure
    def fail(self, connection, version):
        if version == 10:
            raise ValueError("verification failed")
        check(self, connection, version)
    with monkeypatch.context() as patch:
        patch.setattr(ExecutionRepository, "check_structure", fail)
        with pytest.raises(ValueError, match="verification failed"):
            ExecutionRepository(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='execution_registry_comments'").fetchone() is None
    migrated = ExecutionRepository(path)
    try:
        migrated.restore(path.parent / "before-execution-v10.db")
        assert (migrated.all(), migrated.specifications()) == before
        assert migrated.schema_version == 10
    finally:
        migrated.close()


def test_nested_tables_have_independent_headers_and_one_vertical_scroll(registry, app):
    assert registry.table.columnCount() == 5
    assert all("Статус" != registry.table.headerItem().text(i) for i in range(5))
    table, item, owner = first_position(registry, app)
    assert table.columnCount() == 10
    assert len([t for t in registry.tables if t.level == "specification"]) >= 2
    assert all(t.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff for t in registry.tables if t.inner)
    assert table.height() >= table.header().height() + 52
    assert registry.expanded
    prefs = registry.preferences()
    reopened = ExecutionRegistry(registry.repository, prefs)
    try:
        assert reopened.expanded == registry.expanded
    finally:
        reopened.close()


def test_inline_save_cancel_invalid_date_and_service_note_isolation(registry, app):
    table, item, owner = first_position(registry, app)
    editor = edit_cell(table, item, 8, app)
    editor.setText("Машина А123")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    for _ in range(5):
        app.processEvents()
    spec = next(s for s in registry.repository.specifications() if s.id == owner[1])
    assert next(p for p in spec.positions if p.id == owner[2]).transport == "Машина А123"
    table, item, owner = first_position(registry, app)
    original = item.data(8, CELL)["raw"]
    editor = edit_cell(table, item, 8, app)
    editor.setText("Отменить")
    QTest.keyClick(editor, Qt.Key.Key_Escape)
    assert registry.active_editor is None
    assert next(p for p in registry.specs[owner[1]].positions if p.id == owner[2]).transport == original
    editor = edit_cell(table, item, 3, app)
    editor.setText("31.02.2026")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert registry.active_editor is editor
    assert editor.text() == "31.02.2026"
    registry.cancel_edit()
    editor = edit_cell(table, item, 9, app)
    assert isinstance(editor, QPlainTextEdit)
    editor.setPlainText("Комментарий\nвторая строка")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    for _ in range(5):
        app.processEvents()
    assert registry.repository.registry_comment(owner) == "Комментарий\nвторая строка"


def test_search_finds_vehicle_and_comment_and_filters_archive(registry, app):
    spec = registry.repository.specifications()[0]
    owner = ("position", spec.id, spec.positions[0].id)
    registry.repository.update_registry_field(owner, "transport", "УНИКАЛЬНАЯ-МАШИНА", spec.positions[0].transport)
    registry.search.setText("уникальная-машина")
    registry.refresh()
    assert registry.table.topLevelItemCount() == 1
    assert any(t.level == "position" for t in registry.tables)
    registry.search.clear()
    registry.state.setCurrentIndex(1)
    assert registry.table.topLevelItemCount() == 1
    registry.search.setText("НЕТ ТАКОГО")
    registry.refresh()
    assert registry.table.topLevelItemCount() == 0


def test_invalid_editor_blocks_navigation_and_errors_retain_text(storage, app, tmp_path, monkeypatch):
    window = ExecutionWindow(storage, Settings(tmp_path / "settings.json"), tmp_path)
    window.show()
    window.pages.setCurrentIndex(3)
    registry = window.execution_registry
    table, item, owner = first_position(registry, app)
    editor = edit_cell(table, item, 8, app)
    editor.setText("Сохранить позже")
    def fail(*_):
        raise OSError("Диск недоступен")
    with monkeypatch.context() as patch:
        patch.setattr(storage, "update_registry_field", fail)
        assert not registry.finish_edit()
        window.pages.setCurrentIndex(0)
        assert window.pages.currentIndex() == 3
        assert registry.active_editor is editor
        assert editor.text() == "Сохранить позже"
    registry.cancel_edit()
    window.pages.setCurrentIndex(0)
    assert window.pages.currentIndex() == 0
    window.close()


def test_nested_heights_survive_resize_and_numeric_sort(registry, app):
    for width, height in ((1024, 600), (1366, 768), (1920, 1080)):
        registry.collapse_all()
        registry.resize(width, height)
        first_position(registry, app)
        for _ in range(12):
            app.processEvents()
        for table in registry.tables:
            if table.level == "position":
                assert table.height() >= table.header().height() + table.topLevelItemCount() * 52
            for i in range(table.topLevelItemCount()):
                parent = table.topLevelItem(i)
                if parent.isExpanded() and parent.childCount():
                    child = parent.child(0)
                    widget = table.itemWidget(child, 0)
                    assert table.visualItemRect(child).height() >= widget.height()
    table = next(t for t in registry.tables if t.level == "position" and t.topLevelItemCount() > 1)
    registry.sorting["position"] = [1, 0]
    table.apply_sort()
    table.setColumnWidth(3, 160)
    assert all(t.columnWidth(3) == 160 for t in registry.tables if t.level == "position")


def test_specification_editor_returns_to_new_registry(storage, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from tender_app.execution_editor import SpecificationDialog
    window = ExecutionWindow(storage, Settings(tmp_path / "settings.json"), tmp_path)
    window.show()
    window.pages.setCurrentIndex(3)
    monkeypatch.setattr(SpecificationDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    window.edit_execution_specification(storage.specifications()[0].id)
    assert window.pages.currentIndex() == 3
    assert any(t.level == "position" for t in window.execution_registry.tables)
    window.close()


def test_hierarchy_has_distinct_rendered_levels_and_selected_deadline_stays_red(registry, app):
    positions, item, owner = first_position(registry, app)
    levels = [registry.table, next(t for t in registry.tables if t.level == "specification"), positions]
    colors = []
    for table in levels:
        table.clearSelection()
        row = table.topLevelItem(0)
        rect = table.visualItemRect(row)
        x = table.header().sectionViewportPosition(table.columnCount() - 1) + table.columnWidth(table.columnCount() - 1) - 12
        colors.append(table.viewport().grab().toImage().pixelColor(x, rect.bottom() - 8))
    contract, specification, position = colors
    assert contract.blue() - contract.red() > 40
    assert specification.green() - specification.red() > 20
    assert max(position.red(), position.green(), position.blue()) - min(position.red(), position.green(), position.blue()) < 20
    assert levels[0].visualItemRect(levels[0].topLevelItem(0)).height() > positions.visualItemRect(item).height()
    positions.setCurrentItem(item, 3)
    app.processEvents()
    rect = positions.visualItemRect(item)
    left = positions.header().sectionViewportPosition(3)
    image = positions.viewport().grab().toImage()
    assert any(image.pixelColor(x, y).name() == "#ffaaaa"
               for y in range(rect.top(), rect.bottom())
               for x in range(left, left + positions.columnWidth(3)))


def test_resized_columns_fill_right_edge_and_restore_without_blank_space(registry, app):
    first_position(registry, app)
    for level in ("contract", "specification", "position"):
        table = next(t for t in registry.tables if t.level == level)
        for column in range(table.columnCount()):
            table.setColumnWidth(column, 100)
    registry.resize(1920, 1080)
    for _ in range(12):
        app.processEvents()
    for table in registry.tables:
        assert table.header().length() >= table.viewport().width() - 1
        assert table.columnWidth(0) == 100
    reopened = ExecutionRegistry(registry.repository, registry.preferences())
    reopened.resize(1366, 768)
    reopened.show()
    try:
        reopened.expand_all()
        for _ in range(12):
            app.processEvents()
        for table in reopened.tables:
            assert table.header().length() >= table.viewport().width() - 1
            assert table.columnWidth(0) == 100
            assert table.horizontalScrollBar().maximum() == 0
        positions = next(t for t in reopened.tables if t.level == "position")
        positions.setColumnWidth(0, 2000)
        for _ in range(5):
            app.processEvents()
        assert positions.columnWidth(0) == 2000
        assert positions.horizontalScrollBar().maximum() > 0
    finally:
        reopened.close()


def test_comment_alt_enter_appends_dated_entry_and_preserves_history(registry, app):
    table, item, owner = first_position(registry, app)
    previous = registry.repository.registry_comment(owner)
    assert table.headerItem().text(table.columnCount() - 1) == "Комментарий"
    editor = edit_cell(table, item, 9, app)
    assert editor.toPlainText() == ""
    editor.setPlainText("Первая строка")
    editor.moveCursor(QTextCursor.MoveOperation.End)
    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.AltModifier)
    assert editor.toPlainText() == "Первая строка\n"
    assert registry.active_editor is editor
    assert registry.repository.registry_comment(owner) == previous
    editor.insertPlainText("Вторая строка")
    QTest.keyClick(editor, Qt.Key.Key_Return)
    for _ in range(6):
        app.processEvents()
    expected = "Первая строка\nВторая строка"
    assert registry.repository.registry_comment(owner) == expected
    entries = registry.repository.registry_comment_entries(owner)
    assert any(e["message"] == expected and e["stamp"] for e in entries)
    if previous:
        assert any(e["message"] == previous for e in entries)
    first_position(registry, app)
    item = next(t.topLevelItem(i) for t in registry.tables if t.level == "position"
                for i in range(t.topLevelItemCount()) if t.topLevelItem(i).data(0, CELL).get("owner") == owner)
    assert "МСК\n" in item.text(9)
    assert expected in item.toolTip(9)


def test_history_append_keeps_concurrent_and_identical_entries(storage):
    spec = storage.specifications()[0]
    owner = ("position", spec.id, spec.positions[0].id)
    previous = storage.registry_comment(owner)
    storage.set_position_comment(spec.id, owner[2], "Добавили из карточки")
    for _ in range(2):
        assert storage.update_registry_comment(owner, "Повторная запись", previous, append=True)
    entries = storage.registry_comment_entries(owner)
    assert any(e["message"] == "Добавили из карточки" for e in entries)
    assert sum(e["message"] == "Повторная запись" and not e.get("legacy") for e in entries) == 2
    assert not storage.update_registry_comment(owner, "", previous, append=True)
    assert storage.registry_comment(owner) == "Повторная запись"


def test_comment_last_keeps_saved_widths_and_sort_attached_to_their_fields(storage):
    preferences = dict(widths={"position": [100] * 8 + [260, 110]}, sorting={"position": [8, 1]})
    widget = ExecutionRegistry(storage, preferences)
    try:
        assert widget.widths["position"][8:] == [110, 260]
        assert widget.sorting["position"] == [9, 1]
        assert widget.preferences()["position_comment_last"] is True
    finally:
        widget.close()


def test_only_working_and_delivered_groups_split_specifications_of_one_contract(registry, storage, app):
    first = storage.specifications()[0]
    second = storage.save_specification(Specification(
        tender_id=first.tender_id, number="Дополнительная поставка", contract=first.contract,
        archived=True, positions=[Position(name="Историческая позиция", customer_shipped=None)]))
    registry.refresh()
    assert registry.state.count() == 2
    assert registry.state.tabText(0).startswith("В работе")
    assert registry.state.tabText(1).startswith("Поставлены")
    groups = []
    owners = []
    for state in (0, 1):
        registry.state.setCurrentIndex(state)
        registry.expand_all()
        for _ in range(6):
            app.processEvents()
        visible = {table.topLevelItem(i).data(0, CELL)["owner"][1]
                   for table in registry.tables if table.level == "specification"
                   for i in range(table.topLevelItemCount()) if table.topLevelItem(i).data(0, CELL)}
        groups.append(visible)
        contract = next(registry.table.topLevelItem(i) for i in range(registry.table.topLevelItemCount())
                        if registry.table.topLevelItem(i).text(0) == first.contract
                        and registry.table.topLevelItem(i).text(1) == registry.tenders[first.tender_id].customer)
        owners.append(contract.data(0, CELL)["owner"])
        if state == 1:
            assert contract.text(3) == "—"
            assert not contract.data(3, CELL)["overdue"]
    assert first.id in groups[0] and second.id in groups[1]
    assert owners[0] == owners[1]
    assert not groups[0] & groups[1]
    assert groups[0] | groups[1] == {s.id for s in storage.specifications()}
    assert storage.specifications()[-1].positions[0].customer_shipped is None


def test_reveal_selects_delivered_group_and_old_all_preference_falls_back_to_work(storage, app):
    widget = ExecutionRegistry(storage, {"state": 2})
    widget.show()
    try:
        assert widget.state.currentIndex() == 0
        archived = next(s for s in storage.specifications() if s.archived)
        widget.reveal_specification(archived.id)
        assert widget.state.currentIndex() == 1
        assert any(t.level == "position" for t in widget.tables)
    finally:
        widget.close()


def test_move_to_delivered_and_back_preserves_position_values(storage, app, tmp_path):
    window = ExecutionWindow(storage, Settings(tmp_path / "settings.json"), tmp_path)
    window.show()
    window.pages.setCurrentIndex(3)
    spec = next(s for s in storage.specifications() if not s.archived)
    before = spec.positions
    try:
        window.execution_registry.archive_requested.emit(spec.id, True)
        moved = next(s for s in storage.specifications() if s.id == spec.id)
        assert moved.archived and moved.positions == before
        assert "Поставлены" in window.statusBar().currentMessage()
        window.execution_registry.archive_requested.emit(spec.id, False)
        moved = next(s for s in storage.specifications() if s.id == spec.id)
        assert not moved.archived and moved.positions == before
    finally:
        window.close()


def test_customer_only_row_from_archive_stays_in_delivered_group(registry, storage):
    storage.save(Tender(number="Без номера · Учебный архив", customer="Учебный архив", status="Победа",
                        source_sheet="Отгружено (архив)"))
    registry.refresh()
    assert all(registry.table.topLevelItem(i).text(1) != "Учебный архив"
               for i in range(registry.table.topLevelItemCount()))
    registry.state.setCurrentIndex(1)
    assert any(registry.table.topLevelItem(i).text(1) == "Учебный архив"
               for i in range(registry.table.topLevelItemCount()))


def test_old_empty_wins_are_history_and_new_wins_stay_in_work(registry, storage):
    old = [storage.save(Tender(number="ИСТ-007", customer="Учебный заказчик", status="Победа",
                               source_sheet="Тендеры", source_row=row)) for row in (17, 29)]
    manual = storage.save(Tender(number="НОВЫЙ-001", status="Победа"))
    unified = storage.save(Tender(number="НОВЫЙ-002", status="Победа", source_sheet="Тендеры",
                                  import_issues="Источник: единый Excel-шаблон"))
    owner = ("contract", ("unassigned", old[0].id))
    storage.update_registry_comment(owner, "Прежняя заметка", "")
    before = storage.all(), storage.specifications(), storage.registry_comment_summaries()
    changes = storage.connection.total_changes
    registry.refresh()
    ids = {tid for i in range(registry.table.topLevelItemCount())
           for tid in registry.table.topLevelItem(i).data(0, CELL)["payload"][0]["tenders"]}
    assert not ids & {t.id for t in old}
    assert {manual.id, unified.id} <= ids
    registry.state.setCurrentIndex(1)
    rows = {item.data(0, CELL)["owner"]: item
            for item in (registry.table.topLevelItem(i) for i in range(registry.table.topLevelItemCount()))}
    for tender in old:
        item = rows[("contract", ("unassigned", tender.id))]
        assert item.text(0) == "Тендер №ИСТ-007"
        assert "Старая запись" in item.text(2)
        assert f"строка {tender.source_row}" in item.toolTip(0)
    assert "Прежняя заметка" in rows[owner].text(4)
    assert registry.count.text().startswith("Договоров: 1 · Спецификаций: 1")
    assert "Исторических тендеров: 2" in registry.count.text()
    assert storage.connection.total_changes == changes
    assert before == (storage.all(), storage.specifications(), storage.registry_comment_summaries())


def test_real_execution_of_legacy_tender_is_not_archived(registry, storage):
    tender = storage.save(Tender(number="ДЕЙСТВ-001", customer="Учебный завод", status="Победа",
                                source_sheet="Тендеры", source_row=33))
    spec = storage.save_specification(Specification(
        tender_id=tender.id, number="СП-АКТ", contract="Договор АКТ-001",
        positions=[Position(name="Действующая позиция", customer_shipped=None)]))
    registry.refresh()
    assert any(spec.id in {s.id for s in registry.table.topLevelItem(i).data(0, CELL)["payload"][1]}
               for i in range(registry.table.topLevelItemCount()))
    registry.state.setCurrentIndex(1)
    assert not any(tender.id in registry.table.topLevelItem(i).data(0, CELL)["payload"][0]["tenders"]
                   for i in range(registry.table.topLevelItemCount()))
    saved = next(s for s in storage.specifications() if s.id == spec.id)
    assert not saved.archived and saved.positions[0].customer_shipped is None


def test_rebuild_with_pending_nested_layouts_does_not_use_deleted_rows(registry, app, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, traceback: errors.append(str(error)))
    for step in range(12):
        registry.expand_all()
        app.processEvents()
        # A click may switch the selection while child resize events are pending.
        registry.resize(1024 if step % 2 else 1366, 768)
        for table in registry.tables[1:]:
            table.schedule_height()
        registry.state.setCurrentIndex(1 - registry.state.currentIndex())
        QTest.qWait(10)
    assert errors == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation workaround")
def test_registry_accessibility_avoids_native_table_provider(registry, app):
    first_position(registry, app)
    for table in registry.tables:
        interface = QAccessible.queryAccessibleInterface(table)
        assert interface.isValid()
        assert interface.role() == QAccessible.Role.Grouping
        assert not interface.interface_cast(QAccessible.InterfaceType.TableInterface)
        assert interface.text(QAccessible.Text.Name) == table.accessibleName()
    ordinary_tree = QTreeWidget(registry)
    assert QAccessible.queryAccessibleInterface(ordinary_tree).role() == QAccessible.Role.Tree
