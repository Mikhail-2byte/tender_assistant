"""One-time archival by original worksheet, without changing shipment figures."""
import argparse
import hashlib
import json
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QLockFile

from tender_app.execution_repository import ExecutionRepository
from tender_app.repository import readonly


ARCHIVE_SHEET = "Отгружено (архив)"
IMPORT_NOTE = "Перенесена в архив по исходному листу «Отгружено (архив)». Количества и статус сохранены."


def archive_source_records(database):
    database = Path(database).resolve()
    lock = QLockFile(str(database.parent / "application.lock"))
    if not lock.tryLock(0):
        raise RuntimeError("Закройте приложение перед переносом архивных записей.")
    try:
        with closing(readonly(database)) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] not in (2, 3, 4, 5, 6, 7, 8, 9, 10):
                raise ValueError("Выберите существующую базу пробной версии исполнения.")
            row = connection.execute("SELECT value FROM app_meta WHERE key='execution_import_mapping'").fetchone()
            if row is None:
                raise ValueError("В базе отсутствует исходное соответствие строк Excel и спецификаций.")
            archive_ids = {entry["specification_id"] for entry in json.loads(row[0]) if entry["sheet"].strip() == ARCHIVE_SHEET}
        before_hash = hashlib.sha256(database.read_bytes()).hexdigest()
        repo = ExecutionRepository(database)
        try:
            before_tenders = repo.all()
            before = repo.specifications()
            targets = [s for s in before if s.id in archive_ids and not s.archived
                       and not any(message == IMPORT_NOTE for _, message in s.history)]
            backup = repo.backup(manual=True) if targets else None
            for spec in targets:
                repo.set_archived(spec.id, True, IMPORT_NOTE)
            after = repo.specifications()
            assert repo.all() == before_tenders
            for old, new in zip(before, after):
                old_data, new_data = asdict(old), asdict(new)
                for key in ("archived", "updated", "history"):
                    old_data.pop(key)
                    new_data.pop(key)
                assert old_data == new_data, "При архивировании изменились исходные данные."
            repo.check(repo.connection)
            return {"archived_now": len(targets), "archived_total": sum(s.archived for s in after),
                    "working": sum(not s.archived for s in after), "specifications": len(after),
                    "before_sha256": before_hash, "backup": str(backup) if backup else None}
        finally:
            repo.close()
    finally:
        lock.unlock()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(archive_source_records(args.database), ensure_ascii=True, indent=2))
