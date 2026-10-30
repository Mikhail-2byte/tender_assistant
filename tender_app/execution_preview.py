"""Disposable UI mockup for execution plan step 1, without database access."""

import argparse
import json
import sys
from html import escape
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QStyledItemDelegate,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tender_app.execution_panel import ExecutionPanel

from tender_app.execution_demo import (
    DEMO_TODAY,
    demo_data,
)


def paragraph(title, value):
    return (
        f"<p><b>{escape(title)}:</b> {escape(str(value)).replace(chr(10), '<br>')}</p>"
    )


class WrappedRows(QStyledItemDelegate):
    def sizeHint(self, option, index):
        padding = 24
        if index.column() == 0:
            depth = 1
            ancestor = index.parent()
            while ancestor.isValid():
                depth += 1
                ancestor = ancestor.parent()
            padding += self.parent().indentation() * depth
        width = max(40, self.parent().columnWidth(index.column()) - padding)
        bounds = option.fontMetrics.boundingRect(
            QRect(0, 0, width, 10000), Qt.TextFlag.TextWordWrap, str(index.data() or "")
        )
        return QSize(
            width, max(54, bounds.height() + option.fontMetrics.lineSpacing() + 12)
        )


class ExecutionPreview(QMainWindow):
    def __init__(self):
        super().__init__()
        self.tenders, self.specifications = demo_data()
        self.setWindowTitle("Исполнение · макет на вымышленных данных")
        self.resize(1240, 880)
        self.setMinimumSize(900, 680)
        root = QWidget()
        layout = QVBoxLayout(root)
        banner = QLabel(
            "МАКЕТ · вымышленные данные · только просмотр · условная дата 20.10.2026"
        )
        banner.setWordWrap(True)
        banner.setObjectName("banner")
        layout.addWidget(banner)
        self.pages = QTabWidget()
        layout.addWidget(self.pages)
        self.setCentralWidget(root)
        self.build_tenders()
        self.panel = ExecutionPanel(self.tenders, self.specifications)
        self.panel.tender_requested.connect(self.open_tender)
        self.pages.addTab(self.panel, "Исполнение")
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #16191e; color: #ecf0f6; font-size: 13px; }
            QLabel#banner { background: #fff1cf; color: #765515; padding: 9px; }
            QTreeWidget, QListWidget, QTextBrowser, QLineEdit, QComboBox {
                background: #1c2027; color: #ecf0f6; border: 1px solid #455166; }
            QTextBrowser { padding: 8px; }
            QTreeWidget::item, QListWidget::item { padding: 5px; }
            QTreeWidget::item:selected, QListWidget::item:selected { background: #d5e9f6; color: #d9e4ff; }
            QHeaderView::section { background: #232a34; color: #ecf0f6; padding: 7px; border: 0; }
            QTabBar::tab, QPushButton { padding: 7px 13px; }
            QTabBar::tab:selected { background: #1c2027; color: #aec6ff; }
            QComboBox QAbstractItemView { background: #1c2027; color: #ecf0f6; }
            QFrame#section { background: #1c2027; border: 1px solid #343d4b; border-radius: 7px; }
            QFrame#section QWidget { background: transparent; }
            QLabel#heading { font-size: 16px; font-weight: 600; }
            QLabel#subjectHeading { font-size: 18px; font-weight: 600; }
            QLabel#sectionHeading { font-size: 14px; font-weight: 600; }
            QLabel#fieldLabel { color: #a3adbd; }
        """)
        self.tender_list.setCurrentRow(0)
        self.pages.setCurrentIndex(1)

    def open_specification(self, spec_id):
        self.panel.open_specification(spec_id)
        self.pages.setCurrentIndex(1)

    def browser(self):
        browser = QTextBrowser()
        browser.document().setDefaultStyleSheet(
            "a { color: #aec6ff; } p { margin-top: 4px; margin-bottom: 8px; }"
        )
        browser.setOpenLinks(False)
        browser.anchorClicked.connect(self.follow_link)
        return browser

    def build_tenders(self):
        page = QSplitter()
        page.setChildrenCollapsible(False)
        self.tender_list = QListWidget()
        self.tender_list.setWordWrap(True)
        self.tender_list.setMinimumWidth(220)
        for tender in self.tenders:
            item = QListWidgetItem(f"{tender.number}\n{tender.customer}\nПобеда")
            item.setData(Qt.ItemDataRole.UserRole, tender.id)
            self.tender_list.addItem(item)
        self.tender_details = self.browser()
        page.addWidget(self.tender_list)
        page.addWidget(self.tender_details)
        page.setSizes([300, 900])
        self.tender_list.currentItemChanged.connect(self.show_tender)
        self.pages.addTab(page, "Тендеры")

    def show_tender(self, item, previous=None):
        if item is None:
            return
        tender = next(
            t for t in self.tenders if t.id == item.data(Qt.ItemDataRole.UserRole)
        )
        html = f"<h2>{escape(tender.number)} · {escape(tender.customer)}</h2>"
        for title, value in [
            ("Предмет", tender.title),
            ("Результат", tender.status),
            ("Контакты", tender.contacts),
            ("Папка", tender.folder_path),
            ("История тендера", tender.comment),
        ]:
            html += paragraph(title, value)
        html += "<h3>Связанные спецификации</h3>"
        specs = [s for s in self.specifications if s["tender_id"] == tender.id]
        for spec in specs:
            html += f'<p><a href="spec:{spec["id"]}">{escape(spec["number"])} · {escape(spec["title"])}</a></p>'
        if not specs:
            html += "<p>Спецификаций пока нет. Тендер виден в разделе «Исполнение».</p>"
        html += "<p><i>Добавление спецификации будет доступно после подключения сохранения.</i></p>"
        self.tender_details.setHtml(html)

    def follow_link(self, url):
        if url.scheme() == "tender":
            self.open_tender(int(url.path()))
        elif url.scheme() == "spec":
            self.open_specification(int(url.path()))

    def open_tender(self, tender_id):
        for index in range(self.tender_list.count()):
            if self.tender_list.item(index).data(Qt.ItemDataRole.UserRole) == tender_id:
                self.tender_list.setCurrentRow(index)
                self.pages.setCurrentIndex(0)
                return


def main():
    parser = argparse.ArgumentParser(
        description="Макет исполнения на вымышленных данных, без базы"
    )
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "artifacts"
        / "execution-preview-data",
    )
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    window = ExecutionPreview()
    window.show()
    if args.smoke_test:

        def finish():
            args.output_dir.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(args.output_dir / "execution-preview.png"))
            (args.output_dir / "smoke-test.json").write_text(
                json.dumps(
                    {
                        "tenders": len(window.tenders),
                        "specifications": len(window.specifications),
                        "visible_specifications": len(
                            window.panel.visible_specifications
                        ),
                        "without_specifications": window.panel.without_specifications,
                        "database_opened": False,
                        "demo_date": DEMO_TODAY.isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            window.close()

        QTimer.singleShot(500, finish)
    return app.exec()
