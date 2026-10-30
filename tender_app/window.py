from tender_app.window_actions import WindowActions
import re
from html import escape
import sqlite3
from math import ceil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDate, QRect, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPalette,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
    QAbstractTextDocumentLayout,
    QTextDocument,
    QTextBlockFormat,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QDockWidget,
    QInputDialog,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QSizePolicy,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tender_app.models import (
    SORTS,
    STATUSES,
    Tender,
    deadline,
    deadline_label,
    deadline_copy_text,
    filter_tenders,
    now_moscow,
    overdue,
    search_tenders,
    valid_web_url,
)
from tender_app.assistant_panel import AssistantPanel
from tender_app.widgets import WrappingLabel, OptionalDateEdit, submission_fields

STATUS_COLORS = {
    "Интересный": ("#edc184", "#3a3023"),
    "В работе": ("#afc6ff", "#263553"),
    "Податься": ("#edc184", "#3a3023"),
    "Подался": ("#8bd8d3", "#203b3c"),
    "Переторжка": ("#d0b1ef", "#352a43"),
    "Ожидаем итоги": ("#b2c3f5", "#29334b"),
    "Победа": ("#8bdbb3", "#203c32"),
    "Проиграли": ("#f19b9f", "#3d292f"),
    "Не участвуем": ("#b0bac8", "#282e38"),
    "Отменён": ("#c8b2a6", "#352e2b"),
}


def row(*widgets):
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    for widget in widgets:
        layout.addWidget(widget)
    return box


class GrowingTextEdit(QPlainTextEdit):
    """Переносит строки и растёт вместе с текстом; прокручивается вся карточка."""

    def __init__(self, minimum_lines=1):
        super().__init__()
        self._fitting = False
        self.minimum_lines = minimum_lines
        self.document().setDocumentMargin(1.5)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self.fit_text)
        self.textChanged.connect(self.fit_text)
        self.document().documentLayout().documentSizeChanged.connect(self.schedule_fit)

    def schedule_fit(self, *_):
        self.fit_timer.start(0)

    def fit_text(self, *_):
        if self._fitting:
            return
        self._fitting = True
        try:
            self._fit_text()
        finally:
            self._fitting = False

    def _fit_text(self):
        document = self.document()
        layout = document.documentLayout()
        height = 0
        block = document.begin()
        while block.isValid():
            layout.ensureBlockLayout(block)
            height += block.layout().boundingRect().height()
            block = block.next()
        height = max(height, self.fontMetrics().lineSpacing() * self.minimum_lines)
        chrome = self.height() - self.viewport().height()
        wanted = max(
            self.fontMetrics().lineSpacing() * self.minimum_lines + 12,
            ceil(height + document.documentMargin() * 2 + max(0, chrome) + 2),
        )
        if self.height() != wanted:
            self.setFixedHeight(wanted)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.schedule_fit()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_text()
        self.schedule_fit()


class DetailsPanel(QFrame):
    def __init__(self, information, contacts):
        super().__init__()
        self.setObjectName("section")
        self.information = information
        self.contacts = contacts
        layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        layout.addWidget(information)
        self.contacts_column = QWidget()
        column = QVBoxLayout(self.contacts_column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.contacts_field = field("Контакты заказчика", contacts)
        contacts.setPlaceholderText("Имя, телефон, почта или другие контакты заказчика")
        column.addWidget(self.contacts_field)
        contacts.textChanged.connect(self.update_contacts)
        layout.addWidget(self.contacts_column)
        self.update_contacts()

    def update_contacts(self):
        # Contacts are always visible, including empty/new cards.
        self.contacts_field.setVisible(True)
        self.contacts.schedule_fit()
        self.arrange_columns()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange_columns()

    def arrange_columns(self):
        if self.layout().count() < 2:
            return
        side_by_side = self.width() >= max(
            900, self.information.minimumSizeHint().width() + 340
        )
        direction = (
            QBoxLayout.Direction.LeftToRight
            if side_by_side
            else QBoxLayout.Direction.TopToBottom
        )
        if self.layout().direction() != direction:
            self.layout().setDirection(direction)
            self.layout().setStretch(0, 2 if side_by_side else 0)
            self.layout().setStretch(1, 3 if side_by_side else 0)
            self.contacts.minimum_lines = 4 if side_by_side else 1
            self.contacts.schedule_fit()


class ReadableComboBox(QComboBox):
    """Явный светлый popup, включая Windows с тёмной системной темой."""

    def __init__(self):
        super().__init__()
        view = QListView()
        self.setView(view)
        from tender_app.theme import style_popup

        style_popup(view)
        view.setStyleSheet("""
            QListView, QListView QWidget { background: #1c2027; color: #ecf0f6; }
            QListView { border: 1px solid #455166; outline: none;
                selection-background-color: #4e6fcb; selection-color: white; }
            QListView::item { padding: 5px 8px; border: none; }
            QListView::item:selected { background: #4e6fcb; color: white; }
            QListView::item:hover:!selected { background: #293345; color: #ecf0f6; }
        """)


class CardScrollArea(QScrollArea):
    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        if self.widget():
            scrollbar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
            size.setWidth(
                self.widget().minimumSizeHint().width()
                + scrollbar
                + 2 * self.frameWidth()
            )
        return size

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setMinimumWidth(self.minimumSizeHint().width())


class TenderListDelegate(QStyledItemDelegate):
    """Одинаковый перенос при расчёте высоты и рисовании, без скрытого elide Qt."""

    def document(self, option, width, index):
        document = QTextDocument()
        document.setDefaultFont(option.font)
        document.setDocumentMargin(0)
        document.setPlainText(index.data(Qt.ItemDataRole.DisplayRole))
        identity = QTextCursor(document.firstBlock())
        identity.movePosition(
            QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
        )
        identity_style = QTextCharFormat()
        identity_style.setFontWeight(600)
        from tender_app.theme import ENTITY_COLORS
        identity_style.setForeground(QColor(ENTITY_COLORS["company"]))
        identity.setCharFormat(identity_style)
        company = QTextCursor(document.findBlockByNumber(1))
        number_length = document.findBlockByNumber(1).text().find(' · ')
        company.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.KeepAnchor,
                             max(0, number_length))
        company_style = QTextCharFormat()
        company_style.setForeground(QColor(ENTITY_COLORS['tender']))
        company.setCharFormat(company_style)
        status = index.data(Qt.ItemDataRole.UserRole + 1) or ""
        color, background = STATUS_COLORS.get(status, ("#a3adbd", "#191d24"))
        cursor = QTextCursor(document.lastBlock())
        cursor.movePosition(
            QTextCursor.MoveOperation.Right,
            QTextCursor.MoveMode.KeepAnchor,
            len(status),
        )
        label = QTextCharFormat()
        label.setForeground(QColor(color))
        label.setBackground(QColor(background))
        label.setFontWeight(600)
        cursor.setCharFormat(label)
        if index.data(Qt.ItemDataRole.UserRole + 2):
            start = document.lastBlock().text().find("Просрочен")
            if start >= 0:
                cursor.setPosition(document.lastBlock().position() + start)
                cursor.movePosition(
                    QTextCursor.MoveOperation.Right,
                    QTextCursor.MoveMode.KeepAnchor,
                    len("Просрочен"),
                )
                warning = QTextCharFormat()
                warning.setForeground(QColor("#f19b9f"))
                warning.setFontWeight(600)
                cursor.setCharFormat(warning)
        block = document.begin()
        while block.isValid():
            cursor = QTextCursor(block)
            fmt = QTextBlockFormat()
            fmt.setBottomMargin(5)
            if block.blockNumber() == 0:
                fmt.setTopMargin(3)
            cursor.setBlockFormat(fmt)
            block = block.next()
        document.setTextWidth(max(1, width - 34))
        return document

    def sizeHint(self, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        width = self.parent().viewport().width()
        document = self.document(option, width, index)
        return QSize(width, ceil(document.size().height()) + 16)

    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option)
        self.initStyleOption(option, index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        unseen = bool(index.data(Qt.ItemDataRole.UserRole + 3))
        painter.save()
        painter.fillRect(
            option.rect,
            QColor("#25314a" if selected else "#29445b" if unseen and hovered else "#23394d" if unseen else "#252d3a" if hovered else "#1c2027"),
        )
        painter.setPen(QColor("#303742"))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        if selected:
            painter.setPen(QColor("#6d8fc9"))
            painter.drawRect(option.rect.adjusted(0, 0, -1, -1))
        if selected:
            painter.fillRect(
                QRect(
                    option.rect.left(),
                    option.rect.top() + 8,
                    3,
                    option.rect.height() - 16,
                ),
                QColor("#8ba9ff"),
            )
        document = self.document(option, option.rect.width(), index)
        painter.setClipRect(option.rect.adjusted(18, 8, -12, -8))
        painter.translate(option.rect.left() + 18, option.rect.top() + 8)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(
            QPalette.ColorRole.Text, QColor("#ecf0f6" if selected else "#ecf0f6")
        )
        document.documentLayout().draw(painter, context)
        painter.restore()


def field(label, widget):
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    caption = QLabel(label)
    caption.setObjectName("fieldLabel")
    caption.setBuddy(widget)
    layout.addWidget(caption)
    layout.addWidget(widget)
    return box


class Window(WindowActions, QMainWindow):
    def __init__(self, repository, settings, documents_root):
        super().__init__()
        self.repository = repository
        self.settings = settings
        self.documents_root = Path(settings.get("documents_root", str(documents_root)))
        self.current = None
        self.loading = False
        self.dirty = False
        self.deadline_changed = False
        self.pending_assistant = None
        self.assistant_saving = False
        self.assistant_layout_set = False
        self.expanded_previous_notes = set()
        self.last_list_width = 260
        self.tenders = repository.all()
        self.setWindowTitle("Тендеры · локальный реестр")
        self.resize(1366, 860)
        self.setMinimumSize(820, 600)
        self._build()
        self._menus()
        self._style()
        self.ensurePolished()
        self.sync_minimum_width()
        size = settings.get("window_size", [1366, 860])
        if len(size) == 2 and all(type(v) is int and 600 <= v <= 10000 for v in size):
            self.resize(*size)
        sizes = settings.get("splitter_sizes", [370, 870])
        if len(sizes) == 2 and all(type(v) is int and v > 0 for v in sizes):
            self.splitter.setSizes(sizes)
        self.status_filter.setCurrentIndex(
            max(0, self.status_filter.findData(settings.get("status", "Все")))
        )
        self.sort.setCurrentText(settings.get("sort", SORTS[0]))
        self.search.setText(settings.get("query", ""))
        self.refresh_list()
        selected = next(
            (t for t in self.tenders if t.id == settings.get("selected_id")), None
        )
        if selected:
            self.load_tender(selected)
            self.refresh_list()
        elif self.list.count():
            first_id = self.list.item(0).data(Qt.ItemDataRole.UserRole)
            self.load_tender(next(t for t in self.tenders if t.id == first_id))
            self.refresh_list()
        else:
            self.card.setEnabled(False)
        self.list_toggle.setChecked(settings.get("list_visible", True))
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(60000)
        self.clock_timer.timeout.connect(self.refresh_list)
        self.clock_timer.start()
        if type(self) is Window:
            from tender_app.app_shell import install_shell

            install_shell(self)
        if settings.load_warning:
            self.statusBar().showMessage(settings.load_warning)

    def _build(self):
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        self.setCentralWidget(self.splitter)
        left = QWidget()
        self.registry_panel = left
        left.setObjectName("registryPanel")
        layout = QVBoxLayout(left)
        layout.setContentsMargins(16, 20, 14, 12)
        layout.setSpacing(7)
        heading = QLabel("Тендеры")
        heading.setObjectName("heading")
        layout.addWidget(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Номер, заказчик или предмет…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Поиск тендеров")
        layout.addWidget(self.search)
        self.status_filter = ReadableComboBox()
        self.status_filter.addItem("Все статусы", "Все")
        for status in STATUSES:
            self.status_filter.addItem(status, status)
        self.status_filter.setAccessibleName("Фильтр по статусу")
        self.status_filter.setToolTip(
            "Показывать тендеры с выбранным статусом во всём реестре"
        )
        layout.addWidget(field("Статус", self.status_filter))
        self.sort = ReadableComboBox()
        self.sort.addItems(SORTS)
        self.sort.setAccessibleName("Порядок сортировки")
        new = QPushButton("+ Тендер")
        new.clicked.connect(self.new_tender)
        layout.addWidget(self.sort)
        self.import_1c_button = QPushButton("Из 1С")
        self.import_1c_button.setToolTip("Добавить новые тендеры из скопированной таблицы 1С")
        self.import_1c_button.clicked.connect(self.open_import_1c)
        layout.addWidget(row(new, self.import_1c_button))
        self.count = QLabel()
        self.count.setObjectName("muted")
        layout.addWidget(self.count)
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.verticalScrollBar().setSingleStep(20)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setItemDelegate(TenderListDelegate(self.list))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self.choose_tender)
        self.list.itemClicked.connect(self.mark_clicked_tender_seen)
        layout.addWidget(self.list, 1)
        self.empty = QLabel("Ничего не найдено")
        self.reset = QPushButton("Сбросить фильтры")
        self.reset.clicked.connect(self.reset_filters)
        layout.addWidget(self.empty)
        layout.addWidget(self.reset)
        self.splitter.addWidget(left)

        self.card = QWidget()
        self.card.setObjectName("cardPanel")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 12, 16, 12)
        card_layout.setSpacing(5)
        self.caption = QLabel("Выберите тендер или создайте новый")
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        self.caption.setObjectName("tenderNumber")
        self.save_button = QPushButton("Сохранить")
        self.save_button.setObjectName("primary")
        self.save_button.setFixedWidth(100)
        self.save_button.clicked.connect(self.save_tender)
        self.save_button.setEnabled(False)
        self.save_button.setToolTip("Нет несохранённых изменений")
        self.details_toggle = QToolButton()
        self.details_toggle.setText("Сведения")
        self.details_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.details_toggle.setToolTip("Изменить номер, заказчика, предмет и площадку")
        self.details_toggle.setCheckable(True)
        self.details_toggle.toggled.connect(self.toggle_details)
        self.assistant_toggle = QToolButton()
        self.assistant_toggle.setObjectName("assistantToggle")
        self.assistant_toggle.setText("Помощник")
        self.assistant_toggle.setCheckable(True)
        header = row(
            self.caption, self.details_toggle, self.assistant_toggle, self.save_button
        )
        header.layout().setStretch(0, 1)
        card_layout.addWidget(header)
        self.added_caption = QLabel()
        self.added_caption.setObjectName("muted")
        self.added_caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.added_caption.hide()
        card_layout.addWidget(self.added_caption)
        self.unsaved = QLabel()
        self.unsaved.setObjectName("warning")
        self.unsaved.hide()
        card_layout.addWidget(self.unsaved)
        self.outside = QLabel()
        self.outside.setWordWrap(True)
        self.outside.setObjectName("muted")
        self.outside.hide()
        card_layout.addWidget(self.outside)
        self.scroll = CardScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        form_widget = QWidget()
        form = QVBoxLayout(form_widget)
        form.setContentsMargins(0, 2, 2, 0)
        form.setSpacing(14)
        information = QWidget()
        details_layout = QVBoxLayout(information)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)
        details_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.identity_summary = QWidget()
        self.identity_summary.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        summary_layout = QVBoxLayout(self.identity_summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(4)
        self.customer_name = WrappingLabel()
        self.customer_name.setObjectName("customerName")
        self.subject_heading = WrappingLabel()
        self.subject_heading.setObjectName("subjectHeading")
        self.platform_caption = WrappingLabel()
        self.platform_caption.setObjectName("muted")
        for label in (self.customer_name, self.subject_heading, self.platform_caption):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            summary_layout.addWidget(label)
        details_layout.addWidget(self.identity_summary)
        self.identity_editor = QWidget()
        grid = QGridLayout(self.identity_editor)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 2)
        self.edits = {}
        number = QLineEdit()
        number.setAccessibleName("Номер")
        number.textChanged.connect(self.mark_dirty)
        number.textChanged.connect(
            lambda: QTimer.singleShot(0, self, self.sync_minimum_width)
        )
        self.edits["number"] = number
        grid.addWidget(field("Номер", number), 0, 0)
        self.status = ReadableComboBox()
        self.status.addItems(STATUSES)
        self.status.currentTextChanged.connect(self.mark_dirty)
        self.status.currentTextChanged.connect(self.update_status_style)
        self.status.setMaximumWidth(240)
        platform = QLineEdit()
        platform.setAccessibleName("ЭТП")
        platform.textChanged.connect(self.mark_dirty)
        self.edits["platform"] = platform
        grid.addWidget(field("Площадка", platform), 0, 1, 1, 2)
        for name, label, column, span in (
            ("customer", "Заказчик", 0, 1),
            ("title", "Предмет закупки", 1, 2),
        ):
            edit = GrowingTextEdit()
            edit.setAccessibleName(label)
            edit.textChanged.connect(self.mark_dirty)
            self.edits[name] = edit
            grid.addWidget(
                field(label, edit), 1, column, 1, span, Qt.AlignmentFlag.AlignTop
            )
        self.identity_editor.hide()
        details_layout.addWidget(self.identity_editor)

        self.date = OptionalDateEdit(display_format="dd.MM.yyyy")
        self.date.setFixedWidth(225)
        self.date.setAccessibleName("Дата подачи")
        self.time = QLineEdit()
        self.time.setPlaceholderText("ЧЧ:ММ · время не указано")
        self.time.setFixedWidth(200)
        self.time.setAccessibleName("Время подачи по Москве")
        self.time.setToolTip("Московское время, чч:мм. Например, 15:30. Полночь: 00:00.")
        self.deadline_clear = QToolButton()
        self.deadline_clear.setText("×")
        self.deadline_clear.setFixedWidth(24)
        self.deadline_clear.setToolTip("Очистить дату и время")
        self.deadline_clear.clicked.connect(self.clear_deadline)
        self.deadline_copy = QToolButton()
        from tender_app.ui_icons import icon
        self.deadline_copy.setIcon(icon("copy"))
        self.deadline_copy.setAccessibleName("Копировать срок подачи")
        self.deadline_copy.setToolTip("Копировать дату и время без МСК")
        self.deadline_copy.clicked.connect(self.copy_deadline)
        self.deadline_view = row(self.date, self.time, self.deadline_copy, self.deadline_clear)
        self.deadline_view.layout().addStretch(1)
        overview = row(field("Статус", self.status), field("Срок подачи · МСК", self.deadline_view))
        overview.layout().setStretch(1, 1)
        details_layout.addWidget(overview)
        self.date.textChanged.connect(self.change_deadline)
        self.time.textChanged.connect(self.change_deadline)
        for name in ("url", "folder_path"):
            # Существующий механизм чтения/сохранения формы остаётся общим.
            # Адреса редактируются в диалоге/выборе папки и не рисуются в карточке.
            edit = QLineEdit(self.card)
            edit.hide()
            edit.textChanged.connect(self.mark_dirty)
            edit.textChanged.connect(self.update_resource_buttons)
            self.edits[name] = edit
        self.etp_button = QToolButton()
        self.etp_button.setObjectName("quickAction")
        self.etp_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.etp_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.etp_button.clicked.connect(self.open_url)
        etp_menu = QMenu(self.etp_button)
        etp_menu.addAction("Изменить ссылку…", self.edit_url)
        self.etp_button.setMenu(etp_menu)
        self.folder_button = QToolButton()
        self.folder_button.setObjectName("quickAction")
        self.folder_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.folder_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.folder_button.clicked.connect(self.open_folder)
        folder_menu = QMenu(self.folder_button)
        folder_menu.addAction("Выбрать другую папку…", self.choose_folder)
        self.folder_button.setMenu(folder_menu)
        for menu in (etp_menu, folder_menu):
            menu.setStyleSheet(
                "QMenu { background: #1c2027; color: #ecf0f6; border: 1px solid #455166; }"
                "QMenu::item { padding: 7px 12px; }"
                "QMenu::item:selected { background: #4e6fcb; color: white; }"
            )
        actions = row(self.etp_button, self.folder_button)
        actions.layout().addStretch(1)
        details_layout.addWidget(actions)
        contacts = GrowingTextEdit()
        contacts.setAccessibleName("Контакты")
        contacts.textChanged.connect(self.mark_dirty)
        self.edits["contacts"] = contacts
        self.details_panel = DetailsPanel(information, contacts)
        form.addWidget(self.details_panel)

        history = QFrame()
        history.setObjectName("section")
        self.history_section = history
        history_layout = QVBoxLayout(history)
        history_layout.setContentsMargins(16, 16, 16, 16)
        history_layout.setSpacing(8)
        history_heading = QLabel("История работы")
        history_heading.setObjectName("sectionHeading")
        self.history_count = QLabel()
        self.history_count.setObjectName("muted")
        self.note_toggle = QToolButton()
        self.note_toggle.setCheckable(True)
        self.note_toggle.setText("+ Запись")
        heading_row = row(history_heading, self.history_count, self.note_toggle)
        heading_row.layout().setStretch(0, 1)
        history_layout.addWidget(heading_row)
        comment = GrowingTextEdit()
        comment.setParent(self.card)
        comment.setReadOnly(True)
        comment.hide()
        comment.textChanged.connect(self.mark_dirty)
        self.edits["comment"] = comment
        composer = QFrame()
        self.note_composer = composer
        composer.setObjectName("historyComposer")
        composer_layout = QVBoxLayout(composer)
        composer_layout.setContentsMargins(10, 9, 10, 9)
        composer_layout.setSpacing(6)
        composer_heading = QLabel("Новый комментарий")
        composer_heading.setObjectName("fieldLabel")
        self.note = GrowingTextEdit(minimum_lines=2)
        self.note.setPlaceholderText("Что сделано или о чём договорились…")
        self.note.setAccessibleName("Новый комментарий")
        self.note.textChanged.connect(self.mark_dirty)
        self.send_button = QPushButton("Сохранить")
        self.send_button.setObjectName("primary")
        self.note_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.note)
        self.note_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.note_shortcut.activated.connect(self.save_tender)
        self.send_button.setToolTip(
            "Сохранить комментарий и изменения карточки · Ctrl+S / Ctrl+Enter"
        )
        self.send_button.clicked.connect(self.save_tender)
        history_hint = QLabel("МСК · Ctrl+S / Ctrl+Enter")
        history_hint.setToolTip("Дата и время добавляются автоматически по Москве")
        history_hint.setObjectName("muted")
        history_hint.setWordWrap(False)
        composer_header = row(composer_heading, history_hint)
        composer_header.layout().setStretch(0, 1)
        composer_layout.addWidget(composer_header)
        send_row = row(self.note, self.send_button)
        send_row.layout().setStretch(0, 1)
        send_row.layout().setAlignment(self.send_button, Qt.AlignmentFlag.AlignBottom)
        composer_layout.addWidget(send_row)
        self.note_feedback = QLabel("Комментарий и изменения карточки сохраняются вместе.")
        self.note_feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.note_feedback.setWordWrap(True)
        self.note_feedback.setObjectName("muted")
        composer_layout.addWidget(self.note_feedback)
        history_layout.addWidget(composer)
        composer.show()
        self.note_toggle.hide()
        self.note_toggle.toggled.connect(self.toggle_note_composer)
        self.note_toggle.clicked.connect(
            lambda expanded: self.note.setFocus() if expanded else None
        )
        self.note.textChanged.connect(self.reveal_note_draft)
        self.history_entries = QWidget()
        self.history_entries_layout = QVBoxLayout(self.history_entries)
        self.history_entries_layout.setContentsMargins(0, 0, 0, 0)
        self.history_entries_layout.setSpacing(6)
        history_layout.addWidget(self.history_entries)
        comment.textChanged.connect(self.update_history)
        self.send_button.setEnabled(False)
        self.update_history()
        form.addWidget(history)

        self.import_toggle = QToolButton()
        self.import_toggle.setText("Замечания исходных данных")
        self.import_toggle.setCheckable(True)
        self.import_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.import_toggle.setArrowType(Qt.ArrowType.RightArrow)
        form.addWidget(self.import_toggle)
        self.import_info = QLabel()
        self.import_info.setWordWrap(True)
        self.import_info.setTextFormat(Qt.TextFormat.PlainText)
        self.import_info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.import_info.setObjectName("muted")
        self.import_info.hide()
        self.import_toggle.toggled.connect(self.toggle_import)
        form.addWidget(self.import_info)
        form.addStretch(1)
        self.scroll.setWidget(form_widget)
        card_layout.addWidget(self.scroll)
        self.splitter.addWidget(self.card)
        self.splitter.setStretchFactor(1, 1)
        self.assistant = AssistantPanel(ReadableComboBox())
        self.assistant.model_selector.setCurrentIndex(
            max(
                0,
                self.assistant.model_selector.findData(
                    self.settings.get("assistant_model", "primary")
                ),
            )
        )
        self.assistant_dock = QDockWidget("Помощник", self)
        self.assistant_dock.setObjectName("assistantDock")
        self.assistant_dock.setStyleSheet("""
            QDockWidget#assistantDock { background: #191e25; border-left: 2px solid #343d4b; }
            QDockWidget#assistantDock::title { background: #191e25; color: #c1d2ff;
                padding: 8px 10px; font-weight: 600; text-align: left; }
        """)
        self.assistant_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.assistant_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.assistant_dock.setWidget(self.assistant)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.assistant_dock)
        self.assistant_dock.hide()
        self.assistant_toggle.toggled.connect(self.toggle_assistant_panel)
        self.assistant_dock.visibilityChanged.connect(self.assistant_toggle.setChecked)
        self.assistant_dock.visibilityChanged.connect(
            lambda: QTimer.singleShot(0, self, self.sync_minimum_width)
        )
        self.assistant.example_requested.connect(self.assistant_example)
        self.assistant.today_requested.connect(self.assistant_today)
        self.assistant.search_requested.connect(self.assistant_search)
        self.assistant.tender_requested.connect(self.open_assistant_tender)
        self.assistant.save_requested.connect(self.confirm_assistant_change)
        self.assistant.cancel_requested.connect(self.cancel_assistant_change)
        for name in ("number", "customer", "title", "platform"):
            self.edits[name].textChanged.connect(self.update_identity_summary)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(220)
        self.search_timer.timeout.connect(self.refresh_list)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        for combo in (self.status_filter, self.sort):
            combo.currentTextChanged.connect(self.refresh_list)


    def _style(self):
        from tender_app.theme import apply_theme

        apply_theme(self)

    def toggle_registry(self, visible):
        if not visible:
            self.last_list_width = self.registry_panel.width()
        self.registry_panel.setVisible(visible)
        if visible:
            self.splitter.setSizes(
                [
                    self.last_list_width,
                    max(1, self.splitter.width() - self.last_list_width),
                ]
            )
        self.sync_minimum_width()

    def toggle_assistant_panel(self, visible):
        self.assistant_dock.setVisible(visible)
        if visible and not self.assistant_layout_set:
            self.assistant_layout_set = True
            self.resizeDocks(
                [self.assistant_dock],
                [max(280, min(340, round(self.width() * 0.22)))],
                Qt.Orientation.Horizontal,
            )
            QTimer.singleShot(0, self, self.arrange_assistant_columns)

    def arrange_assistant_columns(self):
        self.last_list_width = max(200, min(270, round(self.width() * 0.18)))
        if self.registry_panel.isVisible():
            self.splitter.setSizes(
                [
                    self.last_list_width,
                    max(1, self.splitter.width() - self.last_list_width),
                ]
            )

    def toggle_note_composer(self, expanded):
        self.note_composer.show()
        self.note_toggle.setText(
            "Скрыть ввод"
            if expanded
            else "Продолжить запись" if self.note.toPlainText().strip() else "+ Запись"
        )

    def reveal_note_draft(self):
        if self.note.toPlainText().strip():
            self.note_toggle.setChecked(True)
        self.toggle_note_composer(self.note_toggle.isChecked())

    def toggle_import(self, expanded):
        self.import_info.setVisible(expanded)
        self.import_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    def open_assistant_tender(self, tender_id):
        tender = next((item for item in self.tenders if item.id == tender_id), None)
        if tender is None or not self.guard_changes():
            return False
        self.load_tender(tender)
        self.mark_opened_tender_seen(tender.id)
        self.refresh_list()
        return True

    def assistant_example(self, kind):
        if self.current is None or self.current.id is None:
            return
        number = self.current.number
        if kind == "status":
            self.assistant.message(
                "Пример запроса", f"По тендеру {number} началась переторжка"
            )
            self.propose_assistant_change(self.current.id, status="Переторжка")
        else:
            self.assistant.message(
                "Пример запроса",
                f"По тендеру {number} отправлен запрос по электронной почте",
            )
            self.propose_assistant_change(
                self.current.id, note="Пример: отправлен запрос по электронной почте."
            )

    def propose_assistant_change(self, tender_id, *, status=None, note=""):
        if self.pending_assistant is not None:
            return False
        if self.dirty:
            self.assistant.message(
                "Помощник",
                "Сначала сохраните или отмените ваши изменения в карточке. "
                "Они не будут заменены предложением.",
            )
            return False
        if status is not None and status not in STATUSES:
            return False
        if not self.open_assistant_tender(tender_id):
            return False
        if (status is None or status == self.current.status) and not note.strip():
            self.assistant.message(
                "Помощник", "У тендера уже указан этот статус. Изменений нет."
            )
            return False
        self.pending_assistant = self.current
        self.assistant_toggle.setChecked(True)
        if status is not None:
            self.status.setCurrentText(status)
        if note:
            self.note.setPlainText(note)
        self.assistant.show_proposal(
            self.current.id, self.current.number, self.assistant_change_summary()
        )
        self.save_button.setEnabled(False)
        self.send_button.setEnabled(False)
        return True

    def assistant_field_changes(self, candidate):
        changes = []
        for name, label in (
            ("status", "Статус"),
            ("number", "Номер"),
            ("customer", "Заказчик"),
            ("title", "Предмет"),
            ("platform", "Площадка"),
            ("contacts", "Контакты"),
            ("url", "Ссылка"),
            ("folder_path", "Папка"),
        ):
            before, after = getattr(self.pending_assistant, name), getattr(
                candidate, name
            )
            if before != after:
                changes.append(
                    f"{label}: {before or 'не указано'} → {after or 'не указано'}"
                )
        if (candidate.deadline_date, candidate.deadline_time) != (
            self.pending_assistant.deadline_date,
            self.pending_assistant.deadline_time,
        ):
            changes.append(
                f"Срок: {deadline_label(self.pending_assistant)} → {deadline_label(candidate)}"
            )
        return changes

    def assistant_change_summary(self):
        changes = self.assistant_field_changes(self.candidate())
        if self.note.toPlainText().strip():
            changes.append("Новая запись в истории:\n" + self.note.toPlainText())
        return "\n\n".join(changes) or "Нет изменений. Можно отменить предложение."

    def update_assistant_proposal(self):
        if self.pending_assistant is None or self.assistant.proposal is None:
            return
        try:
            summary = self.assistant_change_summary()
            self.assistant.confirm_button.setEnabled(
                not summary.startswith("Нет изменений.")
            )
        except ValueError as error:
            summary = str(error)
            self.assistant.confirm_button.setEnabled(False)
        self.assistant.proposal_summary.setText(summary)

    def confirm_assistant_change(self):
        if self.pending_assistant is None:
            return False
        try:
            latest = next(
                (
                    item
                    for item in self.repository.all()
                    if item.id == self.pending_assistant.id
                ),
                None,
            )
        except (OSError, sqlite3.Error) as error:
            self.error(error)
            return False
        if latest != self.pending_assistant:
            self.assistant.message(
                "Помощник",
                "Сохранённая карточка изменилась. Отмените предложение и откройте её заново.",
            )
            return False
        self.assistant_saving = True
        try:
            saved = self.save_tender()
        finally:
            self.assistant_saving = False
        if saved:
            self.pending_assistant = None
            self.assistant.finish_proposal("Сохранено в карточке")
            self.assistant.set_context(self.current)
            self.update_save_state()
        return saved

    def cancel_assistant_change(self):
        if self.pending_assistant is None:
            return
        original = self.pending_assistant
        try:
            self.tenders = self.repository.all()
        except (OSError, sqlite3.Error) as error:
            self.error(error)
            return
        original = next(
            (item for item in self.tenders if item.id == original.id), original
        )
        self.pending_assistant = None
        self.load_tender(original)
        self.refresh_list()
        self.assistant.finish_proposal("Отменено · данные не изменены")
        self.assistant.set_context(self.current)
        self.update_save_state()

    def assistant_search(self, query):
        self.assistant.show_search_results(query, search_tenders(self.tenders, query))

    def assistant_today(self):
        today = filter_tenders(self.tenders, group="Сегодня")
        self.assistant.show_results(
            [
                (
                    item,
                    f"№ {item.number} · {item.customer}\n"
                    f"{deadline_label(item)} · {item.status}",
                )
                for item in today
            ],
            now_moscow().date(),
        )

    def update_history(self):
        text = self.edits["comment"].document().toRawText().replace("\u2029", "\n")
        headers = list(
            re.finditer(r"(?:\A|\n\n)\[(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2} МСК)\]\n", text)
        )
        entries = []
        previous = text[: headers[0].start()] if headers else text
        if previous.strip():
            entries.append(("Прежние заметки · без даты", previous, 0, len(previous)))
        for index, header in enumerate(headers):
            end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
            entries.append((header.group(1), text[header.end() : end], header.start(), end))
        while self.history_entries_layout.count():
            widget = self.history_entries_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.history_count.setText(f"Записей: {len(entries)}" if entries else "")
        self.history_count.setToolTip("Новые записи сверху")
        for date, body, start, end in reversed(entries):
            entry = QFrame()
            entry.setObjectName("historyEntry")
            layout = QVBoxLayout(entry)
            layout.setContentsMargins(10, 8, 10, 9)
            layout.setSpacing(5)
            date_label = QLabel(date)
            date_label.setObjectName("historyDate")
            delete = QPushButton("Удалить")
            delete.setObjectName("deleteComment")
            delete.setEnabled(self.current is not None and self.current.id is not None)
            delete.clicked.connect(lambda _, source=text, a=start, b=end: self.delete_note(source, a, b))
            body_label = WrappingLabel(body)
            body_label.setObjectName("historyBody")
            for label in (date_label, body_label):
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setWordWrap(True)
                label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
            if date == "Прежние заметки · без даты" and (
                len(body.splitlines()) > 4 or len(body) > 400
            ):
                tender_id = self.current.id if self.current else None
                toggle = QToolButton()
                toggle.setObjectName("previousNotesToggle")
                toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
                toggle.setCheckable(True)
                toggle.setChecked(tender_id in self.expanded_previous_notes)
                preview = WrappingLabel(body.strip()[:180].replace("\n", " · ") + "…")
                preview.setWordWrap(True)
                preview.setTextFormat(Qt.TextFormat.PlainText)
                preview.setObjectName("muted")
                header = row(date_label, toggle, delete)
                header.layout().setStretch(0, 1)
                layout.addWidget(header)
                layout.addWidget(preview)
                layout.addWidget(body_label)

                def show_previous(
                    expanded,
                    editor=body_label,
                    summary=preview,
                    button=toggle,
                    key=tender_id,
                ):
                    editor.setVisible(expanded)
                    summary.setVisible(not expanded)
                    button.setText("Свернуть" if expanded else "Читать полностью")
                    button.setArrowType(
                        Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
                    )
                    if expanded:
                        self.expanded_previous_notes.add(key)
                    else:
                        self.expanded_previous_notes.discard(key)

                toggle.toggled.connect(show_previous)
                show_previous(toggle.isChecked())
            else:
                header = row(date_label, delete)
                header.layout().setStretch(0, 1)
                layout.addWidget(header)
                layout.addWidget(body_label)
            self.history_entries_layout.addWidget(entry)
        if not entries:
            empty = QLabel("Записей пока нет. Добавьте первое действие выше.")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.history_entries_layout.addWidget(empty)

    def delete_note(self, expected, start, end):
        from tender_app.execution_comments import confirm_comment_deletion

        if self.current is None or self.current.id is None or not confirm_comment_deletion(self):
            return
        try:
            saved = self.repository.delete_tender_comment(self.current.id, expected, start, end)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.error(error)
            return
        self.current = replace(self.current, comment=saved.comment, updated_at=saved.updated_at)
        self.loading = True
        self.edits["comment"].setPlainText(saved.comment)
        self.loaded_text["comment"] = self.edits["comment"].toPlainText()
        self.loading = False
        self.tenders = self.repository.all()
        self.update_history()
        self.mark_dirty()
        self.note_feedback.setText("Комментарий удалён")
        self.note_feedback.setStyleSheet("color: #8bdbb3;")
        self.statusBar().showMessage("Комментарий удалён", 12000)

    def toggle_details(self, expanded):
        self.identity_editor.setVisible(expanded)
        self.identity_summary.setVisible(not expanded)
        self.details_toggle.setText("Свернуть" if expanded else "Сведения")
        QTimer.singleShot(0, self, self.sync_minimum_width)

    def update_identity_summary(self):
        self.customer_name.setText(
            self.edits["customer"].toPlainText() or "Заказчик не указан"
        )
        self.subject_heading.setText(
            self.edits["title"].toPlainText() or "Предмет закупки не указан"
        )
        platform = self.edits["platform"].text()
        self.platform_caption.setText(platform)
        self.platform_caption.setVisible(bool(platform))

    def update_status_style(self):
        color, background = STATUS_COLORS.get(
            self.status.currentText(), ("#a3adbd", "#191d24")
        )
        self.status.setStyleSheet(
            f"QComboBox {{ background: {background}; color: {color};"
            f"border: 1px solid {color}; border-radius: 4px; padding: 5px 7px; font-weight: 600; }}"
        )

    def update_resource_buttons(self):
        self.etp_button.setText(
            "Открыть ЭТП ↗" if self.edits["url"].text().strip() else "Добавить ссылку"
        )
        self.etp_button.setToolTip(
            "Открыть процедуру. Стрелка справа — добавить или изменить ссылку."
        )
        self.folder_button.setText(
            "Открыть папку"
            if self.edits["folder_path"].text().strip()
            else "Выбрать папку"
        )
        self.folder_button.setToolTip(
            "Открыть документы. Стрелка справа — выбрать другую папку."
        )

    def edit_url(self):
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Ссылка на процедуру")
        dialog.setLabelText("Адрес процедуры (http:// или https://):")
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setTextValue(self.edits["url"].text())
        dialog.setOkButtonText("Применить")
        dialog.setCancelButtonText("Отмена")
        dialog.setMinimumWidth(460)
        buttons = dialog.findChild(QDialogButtonBox)

        def validate(value):
            allowed = not value.strip() or valid_web_url(value.strip())
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(allowed)
            dialog.setLabelText(
                "Адрес процедуры (http:// или https://):"
                if allowed
                else "Укажите корректный адрес http:// или https://:"
            )

        dialog.textValueChanged.connect(validate)
        validate(dialog.textValue())
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                value = dialog.textValue().strip()
                if not value or valid_web_url(value):
                    self.edits["url"].setText(value)
        finally:
            dialog.deleteLater()

    def error(self, error):
        QMessageBox.warning(self, "Не удалось выполнить действие", str(error))

    def refresh_list(self):
        unseen_ids = self.repository.unseen_tender_ids()
        previous_scroll = self.list.verticalScrollBar().value()
        status = self.status_filter.currentData()
        visible = filter_tenders(
            self.tenders, self.search.text(), "Все", status, self.sort.currentText()
        )
        self.list.blockSignals(True)
        self.list.clear()
        now = now_moscow()
        for tender in visible:
            late = overdue(tender, now)
            marker = "Просрочен · " if late else ""
            text = (
                f"{'Новый · ' if tender.id in unseen_ids else ''}{tender.customer or 'Заказчик не указан'}\n"
                f"№ {tender.number or '[без номера — проверить]'} · "
                f"{tender.title or 'Предмет не указан'}\n"
                f"{tender.status or 'Статус не указан'} · {marker}{deadline_label(tender)}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, tender.id)
            item.setData(
                Qt.ItemDataRole.UserRole + 1, tender.status or "Статус не указан"
            )
            item.setData(Qt.ItemDataRole.UserRole + 2, late)
            item.setData(Qt.ItemDataRole.UserRole + 3, tender.id in unseen_ids)
            item.setToolTip("<qt><pre>" + escape(text) + "</pre></qt>")
            self.list.addItem(item)
            if self.current and tender.id == self.current.id:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.isVisible():
            self.list.doItemsLayout()
        self.list.verticalScrollBar().setValue(previous_scroll)
        self.count.setText(f"Найдено: {len(visible)} из {len(self.tenders)}")
        self.empty.setVisible(not visible)
        self.reset.setVisible(not visible)
        outside = (
            self.current
            and self.current.id is not None
            and not any(t.id == self.current.id for t in visible)
        )
        self.outside.setText(
            "Открытая карточка не соответствует фильтрам. Она остаётся доступной для редактирования."
            if outside
            else ""
        )
        self.outside.setVisible(bool(outside))

    def reset_filters(self):
        self.search.clear()
        self.status_filter.setCurrentIndex(0)
        self.refresh_list()

    def mark_dirty(self, *_):
        if self.loading or self.current is None:
            return
        self.note_feedback.setText("Комментарий и изменения карточки сохраняются вместе.")
        self.note_feedback.setStyleSheet("")
        try:
            self.dirty = self.current.id is None or self.candidate() != self.current or bool(self.note.toPlainText().strip())
        except ValueError:
            self.dirty = True  # Invalid draft must still be guarded.
        self.update_save_state()
        if not self.dirty:
            self.unsaved.hide()
            if hasattr(self, "save_indicator"):
                self.save_indicator.setText("Без изменений")
            return
        if hasattr(self, "save_indicator"):
            self.save_indicator.setText("Есть несохранённые изменения")
        self.unsaved.setText(
            "Предложение помощника · сохраните или отмените в чате"
            if self.pending_assistant
            else "Есть несохранённые изменения"
        )
        self.unsaved.show()
        self.update_assistant_proposal()

    def update_save_state(self):
        enabled = self.current is not None and self.dirty and self.pending_assistant is None
        self.save_button.setEnabled(enabled)
        self.send_button.setEnabled(enabled)
        self.save_button.setToolTip("Сохранить комментарий и изменения карточки · Ctrl+S" if enabled else
            "Подтвердите или отмените предложение в чате" if self.pending_assistant else "Нет несохранённых изменений")

    def change_deadline(self, *_):
        try:
            fields = submission_fields(self.date, self.time)
            self.deadline_copy.setEnabled(bool(deadline_copy_text(Tender(**fields))))
            self.deadline_view.setToolTip("")
        except ValueError as error:
            self.deadline_copy.setEnabled(False)
            self.deadline_view.setToolTip(str(error))
        if not self.loading:
            self.deadline_changed = True
            self.mark_dirty()
        QTimer.singleShot(0, self, self.sync_minimum_width)

    def copy_deadline(self):
        try:
            text = deadline_copy_text(Tender(**submission_fields(self.date, self.time)))
        except ValueError:
            return
        if text:
            QApplication.clipboard().setText(text)
            self.statusBar().showMessage("Срок подачи скопирован", 3000)

    def sync_minimum_width(self):
        model = self.assistant.model_selector
        model.setMinimumWidth(
            max(
                model.fontMetrics().horizontalAdvance(model.itemText(index))
                for index in range(model.count())
            )
            + 30
        )
        self.caption.setMinimumWidth(
            self.caption.fontMetrics().horizontalAdvance(self.caption.text()) + 4
        )
        self.details_toggle.setFixedWidth(
            max(
                self.details_toggle.fontMetrics().horizontalAdvance(text)
                for text in ("Сведения", "Свернуть")
            )
            + 60
        )
        self.assistant_toggle.setMinimumWidth(
            self.assistant_toggle.fontMetrics().horizontalAdvance("Помощник") + 18
        )
        controls = (
            self.status,
            self.date,
            self.time,
            self.deadline_clear,
            self.deadline_copy,
        )
        height = max(control.sizeHint().height() for control in controls)
        for control in controls:
            control.setFixedHeight(height)
        number = self.edits["number"]
        number.setMinimumWidth(
            max(110, number.fontMetrics().horizontalAdvance(number.text()) + 22)
        )
        self.scroll.updateGeometry()
        self.card.layout().invalidate()
        self.scroll.setMinimumWidth(self.scroll.minimumSizeHint().width())
        margins = self.card.layout().contentsMargins()
        self.card.setMinimumWidth(
            self.scroll.minimumWidth() + margins.left() + margins.right()
        )
        dock_width = (
            self.assistant_dock.minimumSizeHint().width() + 6
            if self.assistant_dock.isVisible()
            else 0
        )
        self.setMinimumWidth(
            max(820, self.splitter.minimumSizeHint().width())
            + dock_width
            + (self.app_sidebar.width() if hasattr(self, "app_sidebar") else 0)
        )

    def clear_deadline(self):
        self.date.clear()
        self.time.clear()
        self.change_deadline()

    def load_tender(self, tender):
        same_record = (
            self.current is not None
            and tender.id is not None
            and self.current.id == tender.id
        )
        details_open = (
            self.details_toggle.isChecked() if same_record else tender.id is None
        )
        self.loading = True
        self.current = tender
        self.card.setEnabled(True)
        self.caption.setText(f"№ {tender.number}" if tender.id else "Новый тендер")
        try:
            from tender_app.models import MOSCOW
            added = datetime.fromisoformat(tender.created_at)
            if added.tzinfo is not None:
                added = added.astimezone(MOSCOW)
            added_text = added.strftime("%d.%m.%Y %H:%M") + " МСК"
        except ValueError:
            added_text = "дата не указана"
        self.added_caption.setText(f"Добавлен: {added_text}")
        self.added_caption.setVisible(tender.id is not None)
        self.caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        for name, edit in self.edits.items():
            if isinstance(edit, QPlainTextEdit):
                edit.setPlainText(getattr(tender, name))
            else:
                edit.setText(getattr(tender, name))
        self.loaded_text = {
            name: (
                edit.toPlainText() if isinstance(edit, QPlainTextEdit) else edit.text()
            )
            for name, edit in self.edits.items()
        }
        if self.status.findText(tender.status) < 0:
            self.status.addItem(tender.status)
        self.status.setCurrentText(tender.status)
        day, clock = deadline(tender)
        self.date.setDate(QDate(day.year, day.month, day.day) if day else QDate())
        self.time.setText(clock.strftime("%H:%M") if clock else "")
        self.change_deadline()
        issues = tender.import_issues
        if not tender.number.strip():
            issues = "Нет номера — проверить. " + issues
        self.import_info.setText(issues or "Замечаний нет")
        self.import_toggle.setVisible(bool(issues))
        self.import_toggle.setChecked(False)
        self.import_info.hide()
        self.note.clear()
        self.note_feedback.setText("Комментарий и изменения карточки сохраняются вместе.")
        self.note_feedback.setStyleSheet("")
        self.note_toggle.setChecked(True)
        self.note_composer.show()
        self.details_panel.update_contacts()
        self.dirty = False
        if hasattr(self, "save_indicator"):
            self.save_indicator.setText("Без изменений")
        self.deadline_changed = False
        self.unsaved.setText("")
        self.unsaved.hide()
        self.details_toggle.setChecked(details_open)
        self.update_identity_summary()
        self.update_status_style()
        self.update_resource_buttons()
        self.assistant.set_context(tender)
        self.loading = False
        self.update_save_state()
        QTimer.singleShot(0, self, self.fit_editors)

    def fit_editors(self):
        for edit in (*self.edits.values(), self.note):
            if isinstance(edit, GrowingTextEdit):
                edit.fit_text()

    def candidate(self):
        values = {
            name: (
                edit.toPlainText() if isinstance(edit, QPlainTextEdit) else edit.text()
            )
            for name, edit in self.edits.items()
        }
        # QTextDocument нормализует пробелы/переносы при чтении. Нетронутые
        # поля берём из исходной записи, чтобы смена статуса не меняла текст Excel.
        for name, value in values.items():
            if value == self.loaded_text[name]:
                values[name] = getattr(self.current, name)
            elif isinstance(self.edits[name], QPlainTextEdit):
                values[name] = (
                    self.edits[name].document().toRawText().replace("\u2029", "\n")
                )
        values["status"] = self.status.currentText()
        if self.deadline_changed:
            values.update(submission_fields(self.date, self.time))
        return replace(self.current, **values)

    def add_note(self):
        if not self.note.toPlainText().strip() or self.current is None:
            return False
        return self.save_tender()

    def save_current(self):
        return self.save_tender()

    def save_tender(self):
        if self.pending_assistant is not None and not self.assistant_saving:
            self.assistant_toggle.setChecked(True)
            self.assistant.message(
                "Помощник", "Подтвердите или отмените предложение кнопками в чате."
            )
            return False
        if self.current is None:
            return True
        if not self.dirty and self.current.id is not None and not self.assistant_saving:
            return True
        note = self.note.document().toRawText().replace("\u2029", "\n")
        created = []
        committed = False
        try:
            candidate = self.candidate()
            fields_changed = candidate != self.current or self.current.id is None
            entry = note
            if self.assistant_saving:
                audit = "Подтверждено в чате"
                changes = self.assistant_field_changes(candidate)
                if changes:
                    audit += ":\n" + "\n".join(changes)
                entry = audit + ("\n\n" + note if note.strip() else "")
            if entry.strip():
                candidate = replace(
                    candidate,
                    comment=candidate.comment
                    + ("\n\n" if candidate.comment else "")
                    + f"[{now_moscow():%d.%m.%Y %H:%M} МСК]\n"
                    + entry,
                )
            duplicates = (
                self.repository.duplicates(candidate.number, candidate.id)
                if candidate.number.strip()
                else []
            )
            if duplicates and (
                self.current.id is None or candidate.number != self.current.number
            ):
                examples = "\n".join(
                    f"ID {t.id}: № {t.number} · {t.customer} · {t.title}"
                    for t in duplicates[:5]
                )
                answer = QMessageBox.question(
                    self,
                    "Возможный дубль",
                    f"Совпадающий номер:\n{examples}\n\nСохранить отдельную запись?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return False
            if candidate.id is None:
                from tender_app.repository import validate_tender
                from tender_app.models import resolve_folder
                from tender_app.import_storage import import_folder, prepare_tender_folder
                validate_tender(candidate)
                if candidate.folder_path.strip():
                    folder = resolve_folder(candidate.folder_path, self.documents_root)
                    prepare_tender_folder(folder, candidate.number, created)
                else:
                    folder = import_folder(self.documents_root, candidate.number, created)
                candidate = replace(candidate, folder_path=str(folder))
            saved = self.repository.save(candidate)
            committed = True
            self.tenders = self.repository.all()
        except (OSError, ValueError, sqlite3.Error) as error:
            if not committed:
                for path in reversed(created):
                    try:
                        if path.is_dir():
                            path.rmdir()
                        else:
                            path.unlink(missing_ok=True)
                    except OSError:
                        pass
            self.note_feedback.setText("Не сохранено. Комментарий и изменения остались в карточке.")
            self.note_feedback.setStyleSheet("color: #f19b9f;")
            self.error(error)
            return False
        self.load_tender(saved)
        if hasattr(self, "save_indicator"):
            self.save_indicator.setText(f"Сохранено в {now_moscow():%H:%M} МСК")
        self.refresh_list()
        message = ("Комментарий добавлен, изменения карточки сохранены" if note.strip() and fields_changed
                   else "Комментарий добавлен" if note.strip() else "Изменения карточки сохранены")
        self.note_feedback.setText(message)
        self.note_feedback.setStyleSheet("color: #8bdbb3;")
        self.statusBar().showMessage(message, 12000)
        return True

    def guard_changes(self):
        if getattr(self, "import_busy", False):
            return False
        if self.pending_assistant is not None:
            self.assistant_toggle.setChecked(True)
            self.assistant.message(
                "Помощник",
                "Сначала сохраните или отмените открытое предложение в чате.",
            )
            return False
        if not self.dirty:
            return True
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Несохранённые изменения")
        dialog.setText("Сохранить изменения в открытой карточке?")
        dialog.setIcon(QMessageBox.Icon.Question)
        save = dialog.addButton("Сохранить", QMessageBox.ButtonRole.AcceptRole)
        discard = dialog.addButton(
            "Не сохранять", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel = dialog.addButton("Остаться", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(save)
        dialog.setEscapeButton(cancel)
        try:
            dialog.exec()
            if dialog.clickedButton() == save:
                return self.save_tender()
            return dialog.clickedButton() == discard

        finally:
            dialog.deleteLater()

    def choose_tender(self, item, previous):
        if item is None:
            return
        tender_id = item.data(Qt.ItemDataRole.UserRole)
        if self.current and tender_id == self.current.id:
            self.mark_opened_tender_seen(tender_id)
            return
        if not self.guard_changes():
            self.refresh_list()
            return
        selected = next((t for t in self.tenders if t.id == tender_id), None)
        if selected:
            self.load_tender(selected)
            self.mark_opened_tender_seen(tender_id)
            # Saving through the guard may have rebuilt the list for the old card.
            self.list.blockSignals(True)
            for row_index in range(self.list.count()):
                row_item = self.list.item(row_index)
                if row_item.data(Qt.ItemDataRole.UserRole) == tender_id:
                    self.list.setCurrentItem(row_item)
                    break
            self.list.blockSignals(False)

    def mark_clicked_tender_seen(self, item):
        tender_id = item.data(Qt.ItemDataRole.UserRole)
        if self.current and self.current.id == tender_id:
            self.mark_opened_tender_seen(tender_id)

    def mark_opened_tender_seen(self, tender_id):
        try:
            if tender_id not in self.repository.unseen_tender_ids():
                return True
            self.repository.mark_tender_seen(tender_id)
        except (OSError, sqlite3.Error) as error:
            self.statusBar().showMessage(f'Карточка открыта, но отметка «Новый» не снята: {error}', 15000)
            return False
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == tender_id and item.data(Qt.ItemDataRole.UserRole + 3):
                item.setData(Qt.ItemDataRole.UserRole + 3, False)
                item.setText(item.text().removeprefix('Новый · '))
                item.setToolTip('<qt><pre>' + escape(item.text()) + '</pre></qt>')
        return True


    def new_tender(self):
        if not self.guard_changes():
            return
        self.load_tender(Tender())
        self.mark_dirty()
        self.refresh_list()
        self.edits["number"].setFocus()
