"""Performance mechanisms and preserved behavior, using fictional data only."""
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import sqlite3
import gc
import weakref

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QStyleOptionViewItem, QScrollArea
from tender_app.execution_panel import NavigationRows, ROLE
from tender_app.execution_positions import PositionTable, StickyPositionHeader

from tender_app.execution_models import Position
from tests.test_execution_window import execution_window


def test_bulk_specifications_preserve_order_quantities_comments_and_history(execution_window):
    repo = execution_window.repository
    spec = next(s for s in repo.specifications() if not s.archived)
    spec.positions.extend([Position(name="Unknown", ordered=Decimal("2.50"), placed=None,
                                   customer_shipped=None), Position(name="Zero", ordered=Decimal("3"),
                                   placed=Decimal("0"), customer_shipped=Decimal("0"))])
    saved = repo.save_specification(spec, "History one")
    repo.add_specification_comment(saved.id, "Older")
    repo.add_specification_comment(saved.id, "Newer")
    repo.set_position_comment(saved.id, saved.positions[-1].id, "Position older")
    repo.set_position_comment(saved.id, saved.positions[-1].id, "Position newer")
    # Capture expected materialized values before recording bounded query count.
    expected = repo.specifications()
    statements = []
    repo.connection.set_trace_callback(statements.append)
    try:
        actual = repo.specifications()
    finally:
        repo.connection.set_trace_callback(None)
    assert actual == expected
    assert len(statements) == 4
    target = next(s for s in actual if s.id == saved.id)
    assert [p.name for p in target.positions[-2:]] == ["Unknown", "Zero"]
    assert target.positions[-2].ordered == Decimal("2.50")
    assert target.positions[-2].placed is None
    assert target.positions[-1].placed == Decimal("0")
    assert [m for _, m in target.comments] == ["Newer", "Older", "History one"] + [m for _, m in spec.comments]
    assert [m for _, m in target.positions[-1].comments] == ["Position newer", "Position older"]
    assert "History one" in [m for _, m in target.history]
    assert [s.id for s in actual] == sorted(s.id for s in actual)


def test_unchanged_refresh_keeps_registry_items_and_execution_drafts(execution_window, app):
    window = execution_window
    window.open_specification(next(s.id for s in window.repository.specifications() if not s.archived))
    app.processEvents()
    window.refresh_execution()
    item = window.registry.table.item(0, 0)
    tree_item = window.execution.tree.currentItem()
    spec_id = window.execution.current_specification_id
    window.execution.comment_drafts[spec_id] = "Draft stays"
    window.refresh_execution()
    assert window.registry.table.item(0, 0) is item
    assert window.execution.tree.currentItem() is tree_item
    assert window.execution.comment_drafts[spec_id] == "Draft stays"
    window.execution.comment_drafts.clear()


def test_minute_tick_updates_deadline_without_sql_or_hidden_rebuild(execution_window, monkeypatch):
    import tender_app.models as models
    window = execution_window
    tender = window.repository.save(replace(window.repository.all()[0], status="В работе",
                                            deadline_date="2026-10-28", deadline_time="12:00",
                                            deadline_needs_review=0))
    monkeypatch.setattr(models, "now_moscow", lambda: datetime(2026, 10, 28, 11, 59))
    window.refresh_execution()
    def item():
        return next(window.registry.table.item(row, 3) for row in range(window.registry.table.rowCount())
                    if window.registry.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == tender.id)
    before = item().foreground().color().name()
    statements = []
    window.repository.connection.set_trace_callback(statements.append)
    monkeypatch.setattr(window.execution, "refresh", lambda: (_ for _ in ()).throw(AssertionError("Hidden page refreshed")))
    monkeypatch.setattr(models, "now_moscow", lambda: datetime(2026, 10, 28, 12, 1))
    try:
        window.clock_timer.timeout.emit()
        assert item().foreground().color().name() != before
        assert not statements
    finally:
        window.repository.connection.set_trace_callback(None)


def test_hidden_page_shows_saved_changes_when_opened(execution_window, app):
    window = execution_window
    spec = next(s for s in window.repository.specifications() if not s.archived)
    window.repository.save_specification(replace(spec, number="Changed while hidden"))
    window.pages.setCurrentIndex(1)
    app.processEvents()
    assert any(s["number"] == "Changed while hidden" for s in window.execution.specifications)


def test_specification_selection_updates_draft_indicator(execution_window):
    window = execution_window
    specs = [s.id for s in window.repository.specifications() if not s.archived]
    try:
        window.open_specification(specs[0])
        window.execution.comment_drafts[specs[0]] = "Unfinished comment"
        window.update_execution_save_state()
        assert window.save_indicator.text() == "Есть несохранённый комментарий"
        window.open_specification(specs[-1])
        assert window.save_indicator.text() == "Без изменений"
        window.open_specification(specs[0])
        assert window.save_indicator.text() == "Есть несохранённый комментарий"
        assert window.execution.comment_drafts[specs[0]] == "Unfinished comment"
    finally:
        window.execution.comment_drafts.clear()


def test_execution_snapshot_detects_external_commits(execution_window):
    window = execution_window
    spec = window.repository.specifications()[0]
    with sqlite3.connect(window.repository.path) as other:
        other.execute("UPDATE execution_specs SET number=? WHERE id=?", ("External commit", spec.id))
    window.refresh_execution()
    assert next(s["number"] for s in window.execution.specifications if s["id"] == spec.id) == "External commit"


def test_no_change_refresh_does_not_reread_all_records(execution_window, monkeypatch):
    window = execution_window
    window.refresh_execution()
    def unexpected():
        raise AssertionError("Unchanged data reread")
    monkeypatch.setattr(window.repository, "all", unexpected)
    monkeypatch.setattr(window.repository, "specifications", unexpected)
    window.refresh_execution()


def test_execution_snapshot_is_invalidated_by_restore(execution_window):
    window = execution_window
    repo = window.repository
    original = repo.specifications()[0]
    backup = repo.backup(manual=True)
    repo.save_specification(replace(original, number="Before restore"))
    window.refresh_execution()
    repo.restore(backup)
    window.refresh_execution()
    assert next(s["number"] for s in window.execution.specifications if s["id"] == original.id) == original.number


def test_registry_refresh_preserves_sort_selection_scroll_and_width(execution_window):
    page = execution_window.registry
    page.status_filter.setCurrentIndex(1)
    page.table.sortItems(1, Qt.SortOrder.DescendingOrder)
    page.table.setCurrentCell(1, 0)
    page.table.setColumnWidth(1, 275)
    selected = page.table.item(1, 0).data(Qt.ItemDataRole.UserRole)
    page.refresh()
    assert page.table.item(page.table.currentRow(), 0).data(Qt.ItemDataRole.UserRole) == selected
    assert page.table.columnWidth(1) == 275
    assert page.table.horizontalHeader().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder


def test_destroyed_sticky_headers_are_not_retained_by_scrollbar(execution_window, app):
    window = execution_window
    specs = [s.id for s in window.repository.specifications() if s.positions and not s.archived]
    window.open_specification(specs[0])
    app.processEvents()
    scrollbar = window.execution.scroll.verticalScrollBar()
    receivers = scrollbar.receivers("2valueChanged(int)")
    old_header = weakref.ref(window.execution.position_tables[0].pinned_header)
    for spec_id in [specs[-1], specs[0]] * 5:
        window.open_specification(spec_id)
        for _ in range(2):
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()
    gc.collect()
    assert old_header() is None
    assert scrollbar.receivers("2valueChanged(int)") == receivers


def test_pending_position_layout_does_not_retain_deleted_widgets(app):
    scroll = QScrollArea()
    table = PositionTable([], True)
    scroll.setWidget(table)
    header = StickyPositionHeader(table, scroll)
    table.pinned_header = header
    header.eventFilter(table, QEvent(QEvent.Type.Hide))
    old_table, old_header = weakref.ref(table), weakref.ref(header)
    table.deleteLater()
    # Delete the table and pinned header BEFORE queued layout work can run.
    for _ in range(2):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del table, header
    gc.collect()
    assert old_table() is None
    assert old_header() is None


def test_row_height_cache_reuses_layout_and_tracks_text_font_and_width(app, monkeypatch):
    tree = QTreeWidget()
    tree.resize(300, 200)
    item = QTreeWidgetItem(tree, ["Text"])
    delegate = NavigationRows(tree)
    option = QStyleOptionViewItem()
    index = tree.indexFromItem(item)
    calls = []
    original = delegate.document
    def document(*args):
        calls.append(True)
        return original(*args)
    monkeypatch.setattr(delegate, "document", document)
    first = delegate.sizeHint(option, index)
    assert delegate.sizeHint(option, index) == first and len(calls) == 1
    item.setData(0, ROLE + 3, "Changed text\nSecond line")
    delegate.sizeHint(option, index)
    assert len(calls) == 2
    option.font.setPointSize(18)
    delegate.sizeHint(option, index)
    assert len(calls) == 3
    tree.resize(500, 200)
    tree.show()
    app.processEvents()
    delegate.sizeHint(option, index)
    assert len(calls) == 4


def test_row_height_cache_is_bounded(app):
    tree = QTreeWidget()
    item = QTreeWidgetItem(tree, ["Text"])
    delegate = NavigationRows(tree)
    index = tree.indexFromItem(item)
    option = QStyleOptionViewItem()
    for number in range(2050):
        item.setData(0, ROLE + 3, str(number))
        delegate.sizeHint(option, index)
    assert len(delegate._heights) == 2048
