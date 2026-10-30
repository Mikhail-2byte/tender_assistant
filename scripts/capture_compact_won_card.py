"""Capture tender and execution cards using disposable fictional records."""
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.registry_editor import RegistryCellDialog
from tender_app.models import Tender
from tender_app.settings import Settings


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("artifacts/checks/compact-won-card/visual")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        create_demo_database(root / "fictional.db")
        repository = ExecutionRepository(root / "fictional.db")
        active = repository.save(Tender(number="ДЕМО-555", customer="Учебный завод",
            title="Комплектующие транспортёра", status="В работе"))
        spec = next(s for s in repository.specifications() if len(s.positions) == 3)
        spec.positions[0].transport = "АВТО 76"
        repository.save_specification(spec)
        window = ExecutionWindow(repository, Settings(root / "settings.json"), root)
        window.show()
        for width, height in ((1366, 768), (1920, 1080)):
            window.resize(width, height)
            for name, callback in (
                ("active", lambda: window.open_registry_tender(active.id)),
                ("won", lambda: window.open_registry_tender(spec.tender_id)),
                ("specification", lambda: window.open_specification(spec.id)),
            ):
                callback()
                for _ in range(15):
                    app.processEvents()
                window.grab().save(str(output / f"{name}-{width}x{height}.png"))
                if name == "won":
                    window.scroll.ensureWidgetVisible(window.spec_section)
                    for _ in range(10):
                        app.processEvents()
                    window.grab().save(str(output / f"won-positions-{width}x{height}.png"))
        tender = next(t for t in repository.all() if t.id == spec.tender_id)
        tender.contacts = "\n\n".join(
            f"Учебный контакт {index + 1} · отдел снабжения\n"
            f"contact-{index + 1}@example.invalid\nПриём документов: по согласованию"
            for index in range(18)
        )
        repository.save(tender)
        spec.title = "Шпильки, гайки и гидрогайки по чертежам. Цена согласована с бюджетом; уточнены условия доставки и комплектность поставки. Потребуется согласовать дату готовности."
        repository.save_specification(spec)
        for width, height in ((1366, 768), (1920, 1080)):
            window.resize(width, height)
            window.open_specification(spec.id)
            for _ in range(15):
                app.processEvents()
            window.grab().save(str(output / f"specification-long-contacts-{width}x{height}.png"))
        dialog = RegistryCellDialog(tender, "contacts", window)
        dialog.show()
        for _ in range(10):
            app.processEvents()
        dialog.grab().save(str(output / "contacts-editor.png"))
        dialog.close()
        window.close()
        repository.close()
        app.processEvents()


if __name__ == "__main__":
    main()
