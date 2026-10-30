"""Capture the registry on disposable fictional tenders only."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontDatabase
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.models import Tender
from tender_app.settings import Settings


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/checks/unified-registry/visual")
    output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="registry-review-")
    data_dir = Path(temporary.name)
    database = data_dir / "fictional.db"
    create_demo_database(database)
    repo = ExecutionRepository(database)
    for index in range(24):
        repo.save(Tender(
            number=f"УЧЕБНЫЙ-2026/1234567890-{index:03}",
            customer="Учебный завод промышленного оборудования",
            title="Изготовление валов и шестерён по чертежам заказчика",
            deadline_date=f"2026-11-{index + 1:02}", deadline_time="12:00",
            status=("В работе", "Податься", "Переторжка")[index % 3],
            platform="Учебная площадка", action="Уточнить стоимость и срок поставки",
            comment="Историческая запись\n\n[27.10.2026 12:00 МСК]\n"
                    "Получено предложение от учебного поставщика. Нужно уточнить материал, "
                    "комплектность и стоимость доставки.\nДополнительная строка комментария.",
        ))
    with repo.connection:
        repo.connection.execute("UPDATE tenders SET is_unseen=1 WHERE number LIKE 'УЧЕБНЫЙ-%' AND id % 3 = 0")
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")
    window = ExecutionWindow(repo, Settings(data_dir / "settings.json"), data_dir)
    window.show()
    window.pages.setCurrentIndex(0)
    window.registry.table.setCurrentCell(0, 0)
    for width, height in ((1920, 1080), (1366, 768)):
        window.resize(width, height)
        for _ in range(12):
            app.processEvents()
        window.grab().save(str(output / f"registry-{width}x{height}.png"))
        window.toggle_agent_dock()
        for _ in range(12):
            app.processEvents()
        window.grab().save(str(output / f"registry-agent-bottom-{width}x{height}.png"))
        window.toggle_agent_dock()
    def capture_right_click():
        popup = app.activePopupWidget()
        if popup is not None:
            popup.grab().save(str(output / "registry-right-click.png"))
            popup.close()
            return
        dialog = app.activeModalWidget()
        if dialog is not None:
            dialog.grab().save(str(output / "registry-right-click.png"))
            dialog.reject()

    window.registry.table.scrollToItem(window.registry.table.item(0, 3))
    QTimer.singleShot(50, capture_right_click)
    window.registry.edit_cell_at(window.registry.table.visualItemRect(window.registry.table.item(0, 3)).center())
    selected_id = window.registry.table.item(0, 0).data(256)
    for field in ("deadline", "status", "comment"):
        def capture_editor(name=field):
            dialog = app.activeModalWidget()
            if dialog is not None:
                dialog.grab().save(str(output / f"registry-edit-{name}.png"))
                dialog.reject()
        QTimer.singleShot(50, capture_editor)
        window.edit_registry_cell(selected_id, field)
    window.registry.search.setText("Несуществующий номер")
    for _ in range(8):
        app.processEvents()
    window.grab().save(str(output / "registry-empty.png"))
    window.registry.search.clear()
    tender = next(t for t in repo.all() if t.number.startswith("УЧЕБНЫЙ"))
    window.open_registry_tender(tender.id)
    for width, height in ((1920, 1080), (1366, 768)):
        window.resize(width, height)
        for _ in range(12):
            app.processEvents()
        window.grab().save(str(output / f"registry-card-{width}x{height}.png"))
    window.close()
    repo.close()
    temporary.cleanup()


if __name__ == "__main__":
    main()
