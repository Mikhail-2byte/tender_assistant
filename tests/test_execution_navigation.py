from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from tests.test_tender_registry import registry_window


def test_only_execution_remains_for_contracts(registry_window, app):
    window = registry_window
    assert [window.pages.tabText(i) for i in range(window.pages.count())] == [
        "Реестр", "Исполнение", "Агент", "Реестр исполнения",
    ]
    assert len(window.navigation_buttons) == 4
    assert not hasattr(window, "contracts_registry")
    assert not hasattr(window, "open_contract_record")
    parent = window.execution.parentWidget()
    specs = [s for s in window.repository.specifications() if not s.archived]
    before = window.repository.all(), window.repository.specifications()
    window.pages.setCurrentIndex(1)
    contract_key = next(key for key in window.execution.groups if key[0] == "contract")
    window.execution.open_contract(contract_key)
    app.processEvents()
    assert window.execution.current_key == contract_key
    for spec in specs * 2:
        window.pages.setCurrentIndex(2)
        window.agent_page.specification_requested.emit(spec.id)
        app.processEvents()
        assert window.pages.currentIndex() == 1
        assert window.execution.current_specification_id == spec.id
        assert window.execution.parentWidget() is parent
        assert window.execution.scroll.parentWidget() is window.execution.splitter
        assert window.save_action.isEnabled()
        window.toggle_agent_dock()
        assert window.agent_dock.isVisible()
        window.agent_dock.hide()
        window.pages.setCurrentIndex(0)
        app.processEvents()
        assert not window.save_action.isEnabled()
    assert (window.repository.all(), window.repository.specifications()) == before
    window.open_specification(specs[0].id)
    window.execution.comment_drafts[specs[0].id] = "Проверка сохранения в исполнении"
    assert window.save_current()
    assert next(s for s in window.repository.specifications() if s.id == specs[0].id).comments[0][1] == "Проверка сохранения в исполнении"
    QTest.keyClick(window.global_search, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)
    assert window.execution.tree.hasFocus()
