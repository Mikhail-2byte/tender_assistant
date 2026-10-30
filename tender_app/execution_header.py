"""Compact execution summary with an independent, bounded contact panel."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QToolButton, QVBoxLayout

from tender_app.execution_demo import date_label, delivery_due, is_overdue
from tender_app.execution_models import delivery_days
from tender_app.theme import ENTITY_COLORS


def read_only_text(text, name, height):
    editor = QPlainTextEdit()
    editor.setPlainText(text)
    editor.setReadOnly(True)
    editor.setAccessibleName(name)
    editor.setMinimumWidth(0)
    editor.setFixedHeight(height)
    editor.setFrameShape(QFrame.Shape.NoFrame)
    editor.setStyleSheet("QPlainTextEdit { background: transparent; color: #c7d1df; border: none; padding: 0; font-size: 12px; }")
    return editor


def fact_label(text, name):
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setMinimumWidth(0)
    return label


def specification_facts(spec, today):
    frame = QFrame()
    frame.setObjectName("specFacts")
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    frame.setStyleSheet("""
        QFrame#specFacts { background: #19212c; border: 1px solid #344153; border-radius: 7px; }
        QFrame#specFacts QLabel { background: transparent; border: none; }
        QLabel#factCaption { color: #8f9fb5; font-size: 11px; }
        QLabel#factValue { color: #e1e8f2; font-size: 13px; font-weight: 600; }
        QLabel#factHint { color: #acbad0; font-size: 11px; }
        QLabel#deliveryValue { color: #a9ccff; font-size: 22px; font-weight: 600; }
        QLabel#overdueValue { color: #ff9f9f; font-size: 22px; font-weight: 600; }
        QFrame#deliveryBand { background: #202e40; border: none; border-left: 3px solid #7eafff; border-radius: 4px; }
    """)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    documents = QHBoxLayout()
    documents.setSpacing(18)
    for caption, value, hint in (
        ("Договор", spec["contract"] or "Не указан", ""),
        ("Спецификация", spec["number"] or "Не указана", "Дата: " + date_label(spec["date"])),
    ):
        cell = QVBoxLayout()
        cell.setSpacing(3)
        cell.addWidget(fact_label(caption, "factCaption"))
        value_label = fact_label(value, 'specNumber' if caption == 'Спецификация' else 'factValue')
        if caption == 'Спецификация':
            value_label.setStyleSheet('color: ' + ENTITY_COLORS['spec'] + '; font-weight: 600;')
        cell.addWidget(value_label)
        if hint:
            cell.addWidget(fact_label(hint, "factHint"))
        cell.addStretch(1)
        documents.addLayout(cell, 2 if caption == "Договор" else 1)
    layout.addLayout(documents)
    band = QFrame()
    band.setObjectName("deliveryBand")
    delivery = QGridLayout(band)
    delivery.setContentsMargins(10, 6, 10, 6)
    delivery.setHorizontalSpacing(16)
    delivery.setVerticalSpacing(2)
    overdue = is_overdue(spec, today)
    for column, (caption, value, style) in enumerate((
        ("Поставить до" + (" · Просрочка" if overdue else ""), date_label(delivery_due(spec)),
         "overdueValue" if overdue else "deliveryValue"),
        ("Срок поставки", delivery_days(spec["date"], delivery_due(spec)), "factValue"),
    )):
        delivery.addWidget(fact_label(caption, "factCaption"), 0, column)
        delivery.addWidget(fact_label(value, style), 1, column)
        delivery.setColumnStretch(column, 1)
    layout.addWidget(band)
    return frame


def contact_panel(text):
    panel = QFrame()
    panel.setObjectName("contactPanel")
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
    panel.setStyleSheet("""
        QFrame#contactPanel { background: #171d25; border: 1px solid #303b49; border-radius: 7px; }
        QLabel { background: transparent; color: #a2b2c7; border: none; font-size: 11px; }
    """)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(8)
    heading = QHBoxLayout()
    heading.addWidget(fact_label("Контакты заказчика", "contactCaption"), 1)
    panel.open_button = QToolButton()
    panel.open_button.setText("Открыть…")
    panel.open_button.setFixedHeight(24)
    panel.open_button.setAccessibleName("Открыть и изменить контакты")
    panel.open_button.setToolTip("Открыть полный список контактов в большом окне и изменить")
    heading.addWidget(panel.open_button)
    layout.addLayout(heading)
    panel.contacts = read_only_text(text.strip() or "Контакты не указаны", "Контакты заказчика", 96)
    layout.addWidget(panel.contacts)
    return panel
