"""Review the nested execution registry using disposable fictional data only."""
import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.execution_registry_table import CELL
from tender_app.settings import Settings


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/checks/execution-registry/visual")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory(prefix="execution-registry-") as directory:
        root = Path(directory)
        create_demo_database(root / "fictional.db")
        repo = ExecutionRepository(root / "fictional.db")
        for spec in repo.specifications():
            repo.update_registry_comment(("specification", spec.id), "Срок подтверждён заказчиком. Уточнить готовность перед отгрузкой.", repo.registry_comment(("specification", spec.id)))
        window = ExecutionWindow(repo, Settings(root / "settings.json"), root)
        window.show()
        window.pages.setCurrentIndex(3)
        registry = window.execution_registry
        for width, height in ((1024, 600), (1366, 768), (1920, 1080)):
            registry.collapse_all()
            window.resize(width, height)
            for _ in range(10):
                app.processEvents()
            window.grab().save(str(output / f"collapsed-{width}x{height}.png"))
            registry.expand_all()
            for _ in range(15):
                app.processEvents()
            window.grab().save(str(output / f"expanded-{width}x{height}.png"))
        registry.state.setCurrentIndex(1)
        registry.expand_all()
        for _ in range(12):
            app.processEvents()
        window.grab().save(str(output / "delivered.png"))
        registry.state.setCurrentIndex(0)
        registry.expand_all()
        for _ in range(12):
            app.processEvents()
        table = next(t for t in registry.tables if t.level == "position")
        owner = table.topLevelItem(0).data(0, CELL)["owner"]
        table.editItem(table.topLevelItem(0), 9)
        app.processEvents()
        registry.active_editor.setPlainText("Новый комментарий к позиции\nАвтомобиль согласован с заказчиком")
        app.processEvents()
        window.grab().save(str(output / "inline-comment.png"))
        registry.finish_edit()
        for _ in range(10):
            app.processEvents()
        def capture_history():
            dialog = app.activeModalWidget()
            if dialog is not None:
                dialog.grab().save(str(output / "comment-history.png"))
                dialog.reject()
        QTimer.singleShot(50, capture_history)
        registry.open_history(owner)
        for table in list(registry.tables):
            for column in range(table.columnCount() - 1):
                table.setColumnWidth(column, 100)
        for _ in range(15):
            app.processEvents()
        window.grab().save(str(output / "resized-columns.png"))
        window.close()
        repo.close()


if __name__ == "__main__":
    main()
