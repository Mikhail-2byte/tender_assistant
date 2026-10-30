"""Compact native shell. UI preferences are stored in existing settings, never in SQLite."""
from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QToolBar, QToolButton, QMenu, QSizePolicy, QButtonGroup, QLineEdit,
)
from tender_app.ui_icons import icon
from tender_app.app_identity import application_icon


def install_shell(window, pages=None):
    if hasattr(window, "app_sidebar"):
        return
    body = window.takeCentralWidget()
    root = QWidget()
    root_layout = QHBoxLayout(root)
    root_layout.setContentsMargins(0, 0, 0, 0)
    root_layout.setSpacing(0)
    side = QWidget()
    side.setObjectName("appSidebar")
    layout = QVBoxLayout(side)
    layout.setContentsMargins(6, 12, 6, 10)
    layout.setSpacing(8)
    window.app_sidebar = side

    expand = QPushButton()
    expand.setObjectName("railToggle")
    expand.setMinimumHeight(40)
    expand.setCheckable(True)
    expand.setIconSize(QSize(20, 20))
    layout.addWidget(expand)
    window.sidebar_toggle = expand

    brand = QLabel("Тендеры.")
    brand.setObjectName("brand")
    layout.addWidget(brand)
    window.navigation_buttons = []
    rail_entries = []
    group = QButtonGroup(root)
    group.setExclusive(True)
    window.navigation_group = group
    for index, (label, name) in enumerate(
        ([(pages.tabText(i), name) for i, name in enumerate(("list", "layers", "chat", "briefcase"))]
         if pages else [("Тендеры", "briefcase")])
    ):
        button = QPushButton(label)
        button.setObjectName("navItem")
        button.setIcon(icon(name))
        button.setIconSize(QSize(20, 20))
        button.setCheckable(True)
        button.setMinimumHeight(42)
        button.setAccessibleName("Раздел " + label)
        button.setToolTip(label)
        group.addButton(button, index)
        layout.addWidget(button)
        window.navigation_buttons.append(button)
        rail_entries.append((button, label))
        if pages:
            button.clicked.connect(lambda checked=False, i=index: pages.setCurrentIndex(i))
    window.navigation_buttons[0].setChecked(True)
    layout.addStretch(1)

    list_button = QPushButton("Список записей")
    list_button.setObjectName("navItem")
    list_button.setIcon(icon("list"))
    list_button.setMinimumHeight(42)
    list_button.setCheckable(True)
    list_button.setChecked(window.list_toggle.isChecked())
    list_button.setAccessibleName("Показать или скрыть список записей")
    list_button.setToolTip("Список договоров / тендеров")
    layout.addWidget(list_button)
    rail_entries.append((list_button, "Список записей"))
    window.shell_list_button = list_button

    def toggle_list(visible):
        if pages and pages.currentIndex() in (0, 2, 3):
            return
        if pages and pages.currentIndex() == 1:
            if visible and window.execution.splitter.width() < 1280:
                window.execution_assistant_toggle.setChecked(False)
            window.execution.splitter.widget(0).setVisible(visible)
        else:
            window.list_toggle.setChecked(visible)
    list_button.clicked.connect(toggle_list)
    window.list_toggle.toggled.connect(
        lambda checked: list_button.setChecked(checked)
        if not pages or pages.currentIndex() == 0 else None
    )
    hint = QLabel("Локальная база\nНа этом компьютере")
    hint.setWordWrap(True)
    hint.setObjectName("muted")
    layout.addWidget(hint)

    def set_expanded(expanded):
        window.sidebar_expanded = bool(expanded)
        window.settings.values["sidebar_expanded"] = bool(expanded)
        side.setFixedWidth(176 if expanded else 56)
        side.setProperty("collapsed", not expanded)
        expand.setChecked(expanded)
        expand.setIcon(icon("panel_close" if expanded else "panel_open"))
        expand.setText("Свернуть панель" if expanded else "")
        expand.setToolTip("Свернуть и запомнить" if expanded else "Развернуть и закрепить панель")
        expand.setAccessibleName(expand.toolTip())
        brand.setVisible(expanded)
        hint.setVisible(expanded)
        for button, label in rail_entries:
            button.setText(label if expanded else "")
        side.style().unpolish(side)
        side.style().polish(side)
        if hasattr(window, "shell_assistant"):
            window.sync_minimum_width()
            if pages:
                QTimer.singleShot(0, window, window.adapt_execution_panels)
    expand.clicked.connect(set_expanded)
    window.set_sidebar_expanded = set_expanded
    set_expanded(window.settings.get("sidebar_expanded", False))
    root_layout.addWidget(side)
    root_layout.addWidget(body, 1)
    window.setCentralWidget(root)

    toolbar = QToolBar()
    toolbar.setObjectName("appToolbar")
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    window.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
    window.app_toolbar = toolbar
    top = QWidget()
    top.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    grid = QGridLayout(top)
    grid.setContentsMargins(4, 0, 4, 0)
    grid.setHorizontalSpacing(16)
    grid.setColumnStretch(0, 1)
    grid.setColumnStretch(1, 2)
    grid.setColumnStretch(2, 1)
    left = QWidget()
    left.setMinimumWidth(0)
    left_layout = QHBoxLayout(left)
    left_layout.setContentsMargins(0, 0, 0, 0)
    menu_button = QToolButton()
    menu_button.setIcon(icon("menu"))
    menu_button.setToolTip("Меню приложения")
    menu_button.setAccessibleName("Меню приложения")
    menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(menu_button)
    for action in window.menuBar().actions():
        if action.menu():
            menu.addMenu(action.menu())
    menu_button.setMenu(menu)
    window.application_menu = menu
    window.application_menu_button = menu_button
    left_layout.addWidget(menu_button)
    window.menuBar().hide()
    breadcrumb = QLabel("Тендеры")
    breadcrumb.setObjectName("breadcrumb")
    breadcrumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    left_layout.addWidget(breadcrumb, 1)
    window.breadcrumb = breadcrumb
    grid.addWidget(left, 0, 0)

    search = QLineEdit()
    search.setObjectName("globalSearch")
    search.setAccessibleName("Общий поиск по тендерам и исполнению")
    search.setPlaceholderText("Поиск по тендерам и исполнению…  Ctrl+K")
    search.setClearButtonEnabled(True)
    search.setMinimumWidth(240)
    search.setMaximumWidth(480)
    search.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
    grid.addWidget(search, 0, 1)
    window.global_search = search
    window.search.hide()
    if pages:
        window.execution.search.hide()

    def reveal_registry():
        if pages and pages.currentIndex() in (0, 2, 3):
            return
        if pages and pages.currentIndex() == 1:
            if window.execution.splitter.width() < 1280 and window.execution_assistant_toggle.isChecked():
                window.execution_assistant_toggle.setChecked(False)
            window.execution.splitter.widget(0).show()
            list_button.setChecked(True)
        else:
            window.list_toggle.setChecked(True)

    from tender_app.global_search import SearchResults
    # Global results are independent of registry filters in either section.
    window.search.clear()
    if pages:
        window.execution.search.clear()
    window.search_results = SearchResults(window, search)

    def focus_search():
        search.setFocus()
        search.selectAll()
        if search.text().strip():
            window.search_results.refresh()
    shortcut = QShortcut(QKeySequence("Ctrl+K"), window)
    shortcut.activated.connect(focus_search)
    window.search_shortcut = shortcut

    def focus_registry():
        window.search_results.timer.stop()
        window.search_results.hide()
        if pages and pages.currentIndex() == 3:
            window.execution_registry.table.setFocus()
            return
        if pages and pages.currentIndex() == 0:
            if window.registry_pages.currentWidget() == window.registry:
                window.registry.table.setFocus()
            else:
                window.scroll.setFocus()
            return
        if pages and pages.currentIndex() == 2:
            window.agent_page.chat.input.setFocus()
            return
        reveal_registry()
        (window.execution.tree if pages and pages.currentIndex() == 1 else window.list).setFocus()
    registry_shortcut = QShortcut(QKeySequence("Ctrl+L"), window)
    registry_shortcut.activated.connect(focus_registry)
    window.registry_shortcut = registry_shortcut

    def escape_panel():
        if pages and pages.currentIndex() == 3 and window.execution_registry.active_editor is not None:
            window.execution_registry.cancel_edit()
            return
        if window.search_results.isVisible():
            window.search_results.timer.stop()
            window.search_results.hide()
            search.setFocus()
            return
        if pages and pages.currentIndex() == 2:
            window.agent_page.chat.input.setFocus()
            return
        if pages and pages.currentIndex() == 1 and window.execution_assistant_toggle.isChecked():
            window.execution_assistant_toggle.setChecked(False)
        elif not pages and window.assistant_toggle.isChecked():
            window.assistant_toggle.setChecked(False)
        else:
            focus_registry()
    escape_shortcut = QShortcut(QKeySequence("Escape"), window)
    escape_shortcut.activated.connect(escape_panel)
    window.escape_shortcut = escape_shortcut

    window.list.itemActivated.connect(lambda *_: window.scroll.setFocus())
    if pages:
        window.execution.tree.setExpandsOnDoubleClick(False)
        def activate_tree(item, *_):
            if item.parent() is None:
                item.setExpanded(not item.isExpanded())
            else:
                window.execution.scroll.setFocus()
        window.execution.tree.itemActivated.connect(activate_tree)

    right = QWidget()
    right_layout = QHBoxLayout(right)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.addStretch(1)
    assistant = QToolButton()
    assistant.setText("Помощник")
    assistant.setIcon(icon("sparkles"))
    assistant.setObjectName("assistantToggle")
    assistant.setCheckable(True)
    assistant.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    assistant.setAccessibleName("Показать или скрыть помощника")
    right_layout.addWidget(assistant)
    grid.addWidget(right, 0, 2)
    toolbar.addWidget(top)
    window.shell_assistant = assistant
    def active_assistant():
        return window.execution_assistant_toggle if pages and pages.currentIndex() == 1 else window.assistant_toggle
    assistant.clicked.connect(lambda visible: active_assistant().setChecked(visible))
    def sync_assistant(*_):
        assistant.blockSignals(True)
        assistant.setChecked(active_assistant().isChecked())
        assistant.blockSignals(False)
    window.assistant_toggle.toggled.connect(sync_assistant)
    if pages:
        window.execution_assistant_toggle.toggled.connect(sync_assistant)
    def sync_page(index):
        for i, button in enumerate(window.navigation_buttons):
            button.setChecked(i == index)
        registry_card = index == 0 and window.registry_pages.currentWidget() == window.registry_card_page
        breadcrumb.setText("Реестр · Карточка тендера" if registry_card else pages.tabText(index))
        assistant.setVisible(index == 1)
        list_button.setVisible(index == 1)
        if hasattr(window, "save_indicator"):
            window.save_indicator.setVisible(index in (1, 3) or registry_card)
        list_button.setChecked(window.list_toggle.isChecked() if index == 0 else not window.execution.splitter.widget(0).isHidden())
        sync_assistant()
    if pages:
        window.sync_shell_page = lambda: sync_page(pages.currentIndex())
        pages.tabBar().hide()
        pages.currentChanged.connect(lambda index: QTimer.singleShot(0, window, lambda: sync_page(pages.currentIndex())))
        sync_page(pages.currentIndex())
    window.assistant_toggle.hide()
    if pages:
        window.execution_assistant_toggle.hide()
    window.details_toggle.setIcon(icon("edit"))
    window.details_toggle.setToolTip("Изменить основные сведения")
    window.etp_button.setIcon(icon("external"))
    window.folder_button.setIcon(icon("folder"))
    def assistant_heading(close):
        head = QWidget()
        line = QHBoxLayout(head)
        line.setContentsMargins(12, 8, 8, 8)
        line.addWidget(QLabel("Помощник"), 1)
        button = QToolButton()
        button.setText("×")
        button.setToolTip("Закрыть помощника")
        button.setAccessibleName("Закрыть помощника")
        button.clicked.connect(close)
        line.addWidget(button)
        return head
    window.assistant_dock.setTitleBarWidget(assistant_heading(lambda: window.assistant_toggle.setChecked(False)))
    if pages:
        window.execution_assistant.layout().insertWidget(0, assistant_heading(lambda: window.execution_assistant_toggle.setChecked(False)))
    window.setWindowIcon(application_icon())
    window.save_indicator = QLabel("Без изменений")
    window.save_indicator.setObjectName("saveIndicator")
    window.statusBar().addPermanentWidget(window.save_indicator)
    window.statusBar().showMessage("Локальная база · компактный режим", 5000)
    sync_assistant()
    window.sync_minimum_width()
