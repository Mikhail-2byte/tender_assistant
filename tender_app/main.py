import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from tender_app.repository import Repository, require_local
from tender_app.settings import Settings
from tender_app.window import Window


def main():
    parser = argparse.ArgumentParser(description="Локальный реестр тендеров")
    parser.add_argument("--data-dir", type=Path, help="Локальная папка базы и настроек")
    parser.add_argument("--documents-root", type=Path, help="Корень относительных путей документов")
    parser.add_argument("--smoke-test", action="store_true", help="Проверить запуск и завершиться")
    parser.add_argument("--assistant-preview", action="store_true", help="Открыть пробный интерфейс помощника")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Тендеры")
    app.setOrganizationName("РИНАКО")
    app.setStyle("Fusion")
    frozen = getattr(sys, "frozen", False)
    base = Path(sys.executable).resolve().parent if frozen else Path(__file__).resolve().parent
    data = (args.data_dir or base / "data").resolve()
    bundle = Path(sys._MEIPASS) if frozen else base.parent
    seed = bundle / "seed" / "tenders.db" if frozen else bundle / "data" / "sample" / "tenders.db"
    default_root = base / "Документы" if frozen else base.parent / "data" / "sample"
    repository = None
    lock = None
    try:
        require_local(data)
        data.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data / "application.lock"))
        if not lock.tryLock(100):
            raise ValueError("Эта база уже открыта в другом экземпляре приложения. Закройте его и повторите запуск.")
        repository = Repository(data / "tenders.db", seed)
        settings = Settings(data / "settings.json")
        documents_root = args.documents_root or Path(settings.get("documents_root", str(default_root)))
        window = Window(repository, settings, documents_root)
        window.show()
        if args.assistant_preview:
            window.setWindowTitle("Тендеры · помощник · пробная версия")
            window.assistant_toggle.setChecked(True)
        if args.smoke_test:
            def finish():
                window.grab().save(str(data / "smoke-test.png"))
                (data / "smoke-test.json").write_text(json.dumps({
                    "records": len(window.tenders), "visible": window.list.count(),
                    "database": str(repository.path), "documents_root": str(documents_root),
                    "frozen": frozen, "timezone": "Europe/Moscow",
                    "assistant_preview": args.assistant_preview,
                    "assistant_visible": window.assistant_dock.isVisible(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                window.close()
            QTimer.singleShot(700, finish)
        return app.exec()
    except Exception as error:
        if args.smoke_test:
            print(str(error), file=sys.stderr)
        else:
            QMessageBox.critical(None, "Не удалось запустить реестр", str(error))
        return 1
    finally:
        if repository:
            repository.close()
        if lock:
            lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
