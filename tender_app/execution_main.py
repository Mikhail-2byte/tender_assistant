import argparse
import json
import sys
from pathlib import Path

# An interrupted source update must never start a mixed application version.
# Frozen EXE files are not updated by the source installer and remain independent.
if (not getattr(sys, "frozen", False)
        and (Path(__file__).resolve().parents[1] / ".apply-update.lock").exists()):
    print("Обновление исходников ещё не завершено. Дождитесь установщика или восстановите исходники из .ui-backups.", file=sys.stderr)
    raise SystemExit(1)


from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.repository import require_local
from tender_app.settings import Settings
from tender_app.app_identity import application_icon, set_windows_app_id
from tender_app.database_workspace import select_data_directory, restart_workspace


def main():
    parser = argparse.ArgumentParser(description="Тендеры, исполнение и агент")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--check-database-import", action="store_true", help="Offline Excel check; requires --smoke-test")
    args = parser.parse_args()
    if args.check_database_import and not args.smoke_test:
        parser.error('--check-database-import requires --smoke-test')
    set_windows_app_id()
    app = QApplication(sys.argv[:1])
    app.setWindowIcon(application_icon())
    app.setStyle("Fusion")
    frozen = getattr(sys, "frozen", False)
    root = Path(__file__).resolve().parents[1]
    base = Path(sys.executable).resolve().parent if frozen else root / "artifacts" / "execution-preview-data"
    default_data = (base / "data" if frozen else base).resolve()
    repository = None
    lock = None
    window = None
    try:
        data = select_data_directory(default_data, args.data_dir)
        require_local(data)
        # Existing main/assistant deployments and the source seed are never migration targets.
        protected = [root / "tender_app" / "data", root / "data" / "sample",
                     root / "dist" / "TenderAssistant", root / "dist" / "TenderAssistantChatPreview"]
        if (any(data == p.resolve() or p.resolve() in data.parents for p in protected)
                or any(p.name in {"TenderAssistant", "TenderAssistantChatPreview"} for p in (data, *data.parents))):
            raise ValueError("Выберите отдельную папку данных пробной версии исполнения.")
        data.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(data / "application.lock"))
        if not lock.tryLock(100):
            raise ValueError("Эта база уже открыта. Закройте другой экземпляр пробной версии.")
        if frozen:
            seed = Path(sys._MEIPASS) / "seed" / "tenders.db"
        else:
            seed = data / "demo-seed" / "tenders.db"
            if not (data / "tenders.db").exists() and not seed.exists():
                create_demo_database(seed)
        repository = ExecutionRepository(data / "tenders.db", seed)
        if args.check_database_import:
            from tender_app.database_checks import verify_excel_runtime
            verify_excel_runtime()
        window = ExecutionWindow(repository, Settings(data / "settings.json"), base / "Документы")
        window.database_selection_path = default_data / 'database-selection.json'
        window.pages.setCurrentIndex(0)
        window.showMaximized()
        if not args.smoke_test:
            window.resource_sync_enabled = True
            QTimer.singleShot(0, window, window.sync_resource_links)
        if args.smoke_test:
            def finish():
                from .platform_runtime import verify_platform_runtime
                platform_runtime = verify_platform_runtime()
                window.pages.setCurrentIndex(3)
                window.execution_registry.expand_all()
                for _ in range(8):
                    app.processEvents()
                window.grab().save(str(data / "execution-smoke.png"))
                (data / "execution-smoke.json").write_text(json.dumps({
                    "records": len(repository.all()), "specifications": len(repository.specifications()),
                    "archived": sum(s.archived for s in repository.specifications()),
                    "working": sum(not s.archived for s in repository.specifications()),
                    "without_specifications": window.execution.without_specifications,
                    "contracts": window.execution.visible_contracts, "navigation": "contracts",
                    "schema_version": repository.schema_version, "frozen": frozen,
                    "database": str(repository.path),
                    "agent_available": hasattr(window, "ai_service"),
                    "maximized": window.isMaximized(),
                    "platform_runtime_ready": platform_runtime,
                    "sections": [window.pages.tabText(i) for i in range(window.pages.count())],
                    "execution_registry_columns": window.execution_registry.table.columnCount(),
                    "execution_registry_levels": sorted({t.level for t in window.execution_registry.tables}),
                    "history_entries": sum(len(s.history) for s in repository.specifications()),
                    "position_comments": sum(len(p.comments) for s in repository.specifications() for p in s.positions),
                    **({'excel_import_runtime': True} if args.check_database_import else {}),
                    "specification_comments": sum(len(s.comments) for s in repository.specifications()),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                window.close()
            QTimer.singleShot(600, window, finish)
        return app.exec()
    except Exception as error:
        if args.smoke_test:
            print(str(error), file=sys.stderr)
        else:
            QMessageBox.critical(None, "Не удалось запустить пробную версию", str(error))
        return 1
    finally:
        if repository:
            repository.close()
        if lock:
            lock.unlock()
        if window is not None and getattr(window, 'pending_data_directory', None):
            try:
                restart_workspace(window.pending_data_directory, window.database_selection_path)
            except OSError as error:
                QMessageBox.critical(None, 'База сохранена, перезапуск не выполнен', str(error))
