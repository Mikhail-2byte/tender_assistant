"""Escaped, structured search cards shared by the result list and chat."""
from html import escape
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAbstractTextDocumentLayout, QColor, QPalette, QTextDocument
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from tender_app.record_search import SearchResult, number_references, normalized
from tender_app.theme import ENTITY_COLORS

HTML_ROLE = Qt.ItemDataRole.UserRole + 1


def highlighted(text, query):
    text = str(text or '')
    words = [r.number for r in number_references(query)] or normalized(query).split()
    if not words:
        return escape(text)
    pattern = '|'.join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))
    parts = []
    start = 0
    for match in re.finditer(pattern, text, re.IGNORECASE):
        parts.extend((escape(text[start:match.start()]), '<b><u>' + escape(match.group()) + '</u></b>'))
        start = match.end()
    return ''.join(parts) + escape(text[start:])


def result_html(result, query):
    def line(text, role=None, bold=False):
        content = highlighted(text, query)
        if bold:
            content = '<b>' + content + '</b>'
        color = ENTITY_COLORS.get(role, '#ecf0f6')
        return f'<div style="color:{color}; margin-bottom:3px">{content}</div>'

    refs = number_references(query)
    application = next((r.number for r in refs if r.kind == 'application'), None)
    parts = []
    if application and result.position_id is not None:
        parts.append(line('Приложение ' + application, bold=True))
    if result.spec_id is not None:
        parts.append(line('Спецификация № ' + result.spec_number, 'spec', True))
    parts.append(line(result.customer, 'company'))
    parts.append(line('Тендер № ' + result.tender_number, 'tender', True))
    if result.archived:
        parts.append(line('Архив'))
    if result.position_name:
        parts.append(line('Позиция: ' + result.position_name))
    elif result.kind == 'Тендер':
        parts.append(line(result.context.split(' · ', 1)[-1]))
    parts.append(line(result.field + ': ' + result.snippet))
    return ''.join(parts)


def link_html(link):
    try:
        result = SearchResult(**link['match'])
        return result_html(result, str(link.get('query', '')))
    except (KeyError, TypeError, AttributeError):
        return ''


class SearchResultDelegate(QStyledItemDelegate):
    def document(self, index, font, width):
        document = QTextDocument()
        document.setDefaultFont(font)
        document.setDocumentMargin(0)
        document.setHtml(index.data(HTML_ROLE) or '')
        document.setTextWidth(max(1, width - 24))
        return document

    def sizeHint(self, option, index):
        document = self.document(index, option.font, self.parent().viewport().width())
        return QSize(0, int(document.size().height()) + 22)

    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.text = ''
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, option.widget)
        document = self.document(index, styled.font, option.rect.width())
        painter.save()
        painter.setClipRect(option.rect.adjusted(8, 5, -8, -5))
        painter.translate(option.rect.left() + 12, option.rect.top() + 10)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, QColor('#ecf0f6'))
        document.documentLayout().draw(painter, context)
        painter.restore()
