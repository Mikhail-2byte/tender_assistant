from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent


def test_wheel_scrolls_part_of_card_and_keeps_selection(window, app):
    listing = window.list
    scrollbar = listing.verticalScrollBar()
    scrollbar.setValue(0)
    app.processEvents()
    selected = listing.currentItem()
    first_height = listing.visualItemRect(listing.item(0)).height()
    assert scrollbar.maximum() > first_height
    before = listing.visualItemRect(listing.item(0)).top()
    point = listing.viewport().rect().center()
    event = QWheelEvent(
        QPointF(point), QPointF(listing.viewport().mapToGlobal(point)),
        QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
    )
    app.sendEvent(listing.viewport(), event)
    app.processEvents()
    moved = before - listing.visualItemRect(listing.item(0)).top()
    assert 0 < moved < first_height
    assert listing.currentItem() is selected
    position = scrollbar.value()
    window.refresh_list()
    app.processEvents()
    assert scrollbar.value() == position
