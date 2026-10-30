"""Render contract/specification cards; ExecutionPanel owns selection and navigation."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget
from tender_app.execution_groups import counted, contract_key
from tender_app.execution_demo import date_label, is_overdue, delivery_due
from tender_app.execution_header import specification_facts, contact_panel
from tender_app.widgets import WrappingLabel
from tender_app.execution_positions import PositionTable, StickyPositionHeader
from tender_app.execution_comments import CommentFeed

class ExecutionCards:
    def label(self, value, name=None):
        widget = WrappingLabel(str(value))
        widget.setWordWrap(True)
        widget.setTextFormat(Qt.TextFormat.PlainText)
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if name:
            widget.setObjectName(name)
        return widget

    def section(self, title):
        section = QFrame()
        section.setObjectName("section")
        section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(section)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)
        if title:
            layout.addWidget(self.label(title, "sectionHeading"))
        self.card_layout.addWidget(section)
        return layout

    def button(self, layout, title, callback):
        button = QPushButton(title)
        button.clicked.connect(callback)
        from tender_app.ui_icons import icon

        if "папку" in title:
            button.setIcon(icon("folder"))
            button.setProperty("secondary", True)
        elif "ЭТП" in title:
            button.setIcon(icon("external"))
            button.setProperty("secondary", True)
        layout.addWidget(button)
        return button

    def fold(self, layout, title, body, key, initially_open=False):
        toggle = QToolButton()
        toggle.setText(title)
        toggle.setCheckable(True)
        toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        checked = key in self.expanded_sections or initially_open
        toggle.setChecked(checked)
        body.setVisible(checked)

        def change(opened):
            body.setVisible(opened)
            toggle.setArrowType(
                Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow
            )
            if opened:
                self.expanded_sections.add(key)
            else:
                self.expanded_sections.discard(key)

        toggle.toggled.connect(change)
        change(checked)
        layout.addWidget(toggle)
        layout.addWidget(body)
        return toggle

    def resource_buttons(self, layout, tender, include_contacts=True):
        actions = QHBoxLayout()
        tender_button = self.button(
            actions, f"Тендер {tender.number}", lambda: self.open_tender(tender.id)
        )
        from tender_app.theme import style_record_link
        style_record_link(tender_button, 'tender')
        if not self.read_only:
            folder = self.button(
                actions,
                "Открыть папку",
                lambda: self.resource_requested.emit(tender.id, "folder"),
            )
            folder.setEnabled(bool(tender.folder_path.strip()))
            folder.setToolTip("Открыть документы" if folder.isEnabled() else "Папка не задана. Укажите её в карточке тендера.")
            url = self.button(
                actions,
                "Открыть ЭТП",
                lambda: self.resource_requested.emit(tender.id, "url"),
            )
            url.setEnabled(bool(tender.url.strip()))
            url.setToolTip("Открыть процедуру на ЭТП" if url.isEnabled() else "Ссылка ЭТП не задана. Добавьте её в карточке тендера.")
        actions.addStretch(1)
        layout.addLayout(actions)
        if tender.status != "Победа":
            layout.addWidget(
                self.label(
                    f"Результат тендера: {tender.status}. Исполнение сохранено.",
                    "warning",
                )
            )
        if include_contacts and tender.contacts:
            self.fold(
                layout,
                "Контакты заказчика",
                self.label(tender.contacts),
                ("contacts", tender.id),
            )

    def show_contract(self, key, group):
        header = self.section(None)
        header.addWidget(self.label(group["customer"], "companyName"))
        header.addWidget(
            self.label(group["contract"] or "Договор пока не указан", "subjectHeading")
        )
        specs = group["specs"]
        active = [s for s in specs if not s["archived"] and not s["completed"]]
        next_due = min((delivery_due(s) for s in active if delivery_due(s)), default=None)
        header.addWidget(self.label(
            counted(len(specs), "спецификация", "спецификации", "спецификаций") + f" · В работе: {len(active)} · Просрочено: {sum(is_overdue(s, self.today) for s in specs)}"
            + f"\nБлижайший срок: {date_label(next_due)}", "fieldLabel"))
        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        for tender in group["tenders"].values():
            self.resource_buttons(detail_layout, tender)
        self.fold(header, "Документы и контакты", details, ("contract-details", key))
        if not self.read_only:
            actions = QHBoxLayout()
            self.button(actions, "+ Спецификация", lambda: self.create_requested.emit(tuple(group["tenders"]), group["contract"]))
            actions.addStretch(1)
            header.addLayout(actions)
        if not specs:
            self.section("Спецификации").addWidget(
                self.label(
                    "Спецификаций пока нет. Добавьте первую после согласования договора."
                )
            )
        for spec in (
            s for s in specs if self.matches(group["tenders"][s["tender_id"]], s)
        ):
            section = self.section(f"Спецификация {spec['number']}")
            section.addWidget(self.label(spec["title"]))
            section.addWidget(self.label(f"{spec['stage']} · До {date_label(delivery_due(spec))} · " + counted(len(spec["positions"]), "позиция", "позиции", "позиций"), "fieldLabel"))
            self.spec_links[spec["id"]] = self.button(
                section,
                "Открыть спецификацию",
                lambda checked=False, sid=spec["id"]: self.open_specification(sid),
            )

    def show_specification(self, spec):
        tender = next(t for t in self.tenders if t.id == spec["tender_id"])
        key = contract_key(tender, spec)
        header = self.section(None)
        self.spec_header = header.parentWidget()
        self.spec_header.setObjectName("specOverview")
        header.setContentsMargins(12, 4, 12, 4)
        header.setSpacing(6)
        identity = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(self.label(tender.customer or "Заказчик не указан", "customerName"))
        titles.addWidget(self.label(tender.title or "Предмет закупки не указан", "subjectHeading"))
        identity.addLayout(titles, 1)
        if not self.read_only:
            actions = QHBoxLayout()
            edit = self.button(actions, "Изменить", lambda: self.edit_requested.emit(spec["id"]))
            edit.setToolTip("Изменить реквизиты, сроки и позиции спецификации")
            self.archive_button = self.button(actions, "Вернуть в работу" if spec["archived"] else "В архив",
                lambda: self.archive_requested.emit(spec["id"], not spec["archived"]))
            identity.addLayout(actions)
            identity.setAlignment(actions, Qt.AlignmentFlag.AlignTop)
        header.addLayout(identity)
        resources = QHBoxLayout()
        back = self.button(resources, "←", lambda: self.open_contract(key))
        back.setFixedWidth(32)
        back.setStyleSheet("padding: 0;")
        back.setToolTip("К договору")
        back.setProperty("secondary", True)
        back.setAccessibleName("Вернуться к договору")
        self.resource_buttons(resources, tender, include_contacts=False)
        resources.addWidget(self.label(f"{tender.platform or 'Площадка не указана'} · {tender.status}", "muted"))
        header.addLayout(resources)
        summary = QHBoxLayout()
        summary.setSpacing(12)
        summary.addWidget(specification_facts(spec, self.today), 2, Qt.AlignmentFlag.AlignTop)
        contacts = contact_panel(tender.contacts)
        contacts.open_button.setEnabled(not self.read_only)
        contacts.open_button.clicked.connect(lambda: self.contacts_requested.emit(tender.id))
        self.customer_contacts = contacts.contacts
        summary.addWidget(contacts, 1, Qt.AlignmentFlag.AlignTop)
        header.addLayout(summary)
        self.show_positions(spec)
        comments = self.section("Комментарии по спецификации")
        self.spec_comment_feed = CommentFeed(
            spec["comments"],
            self.read_only,
            self.comment_drafts.get(spec["id"], ""),
            compact=True,
            input_height=self.comment_input_height,
            comment_ids=spec.get("comment_ids", []),
        )
        self.spec_comment_feed.height_changed.connect(
            lambda height: setattr(self, "comment_input_height", height)
        )
        self.spec_comment_feed.draft_changed.connect(
            lambda text: self.remember_comment_draft(spec["id"], text)
        )
        self.spec_comment_feed.submitted.connect(
            lambda text: self.spec_comment_requested.emit(spec["id"], text)
        )
        self.spec_comment_feed.delete_requested.connect(
            lambda key: self.spec_comment_delete_requested.emit(spec["id"], key)
        )
        comments.addWidget(self.spec_comment_feed)

    def remember_comment_draft(self, spec_id, text):
        if text.strip():
            self.comment_drafts[spec_id] = text
        else:
            self.comment_drafts.pop(spec_id, None)
        self.draft_changed.emit()

    def show_positions(self, spec):
        section = self.section("Позиции спецификации")
        self.position_filter = QComboBox()
        self.position_filter.addItems(["Все позиции", "Осталось отгрузить"])
        self.position_filter.setAccessibleName("Показать позиции")
        self.position_filter.setCurrentIndex(int(self.only_remaining_positions))
        section.addWidget(self.position_filter)
        table = PositionTable(
            spec["positions"], self.read_only, self.position_column_widths,
            fallback_due=spec["revised"] or spec["due"], today=self.today,
            inactive=spec["completed"] or spec["archived"]
        )
        self.position_tables.append(table)
        table.comment_requested.connect(
            lambda pid: self.comment_requested.emit(spec["id"], pid)
        )
        table.edit_requested.connect(
            lambda pid, field: self.position_edit_requested.emit(spec["id"], pid, field)
        )
        table.shipped_requested.connect(lambda pid: self.shipped_requested.emit(spec["id"], pid))
        table.complete_requested.connect(lambda pid: self.shipment_complete_requested.emit(spec["id"], pid))
        def filter_positions(index):
            self.only_remaining_positions = bool(index)
            table.filter_remaining(bool(index))
        self.position_filter.currentIndexChanged.connect(filter_positions)
        table.filter_remaining(self.only_remaining_positions)
        table.widths_changed.connect(
            lambda widths: setattr(self, "position_column_widths", widths)
        )
        section.addWidget(table)
        table.pinned_header = StickyPositionHeader(table, self.scroll)
