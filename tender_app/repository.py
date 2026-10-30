import csv
import ctypes
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from tender_app.models import Tender, deadline, now_moscow
from tender_app.storage_io import atomic_text


COLUMNS = tuple(f.name for f in fields(Tender))


def validate_tender(tender):
    """Shared validation for manual saves and atomic clipboard imports."""
    if tender.id is None and not tender.number.strip():
        raise ValueError("Укажите номер нового тендера.")
    day, clock = deadline(tender)
    if tender.deadline_date and day is None:
        raise ValueError("Проверьте дату подачи: ожидается ГГГГ-ММ-ДД.")
    if tender.deadline_time and (not day or clock is None):
        raise ValueError("Для времени подачи нужна корректная дата.")


def require_local(path):
    path = Path(path).resolve()
    if str(path).startswith("\\\\"):
        raise ValueError("База должна находиться на локальном диске, а не в сетевой папке.")
    if os.name == "nt" and ctypes.windll.kernel32.GetDriveTypeW(path.anchor) == 4:
        raise ValueError("Сетевой диск не подходит для рабочей базы SQLite.")
    return path


def check_database(connection, version=1):
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("Проверка целостности базы не пройдена.")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(tenders)")}
    if not set(COLUMNS).issubset(columns):
        raise ValueError("Файл не содержит ожидаемую таблицу тендеров.")
    if connection.execute("PRAGMA user_version").fetchone()[0] != version:
        raise ValueError("Неподдерживаемая версия базы данных.")


def readonly(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


class Repository:
    schema_version = 1

    def __init__(self, path, seed=None):
        self.path = require_local(path)
        self.backup_dir = self.path.parent / "backups"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            if not seed or not Path(seed).is_file():
                raise FileNotFoundError("Исходная база не найдена. Восстановите полный комплект приложения.")
            # Сначала законченная копия; прерванный первый запуск не оставляет пустую рабочую базу.
            staging = self.path.with_suffix(".initializing")
            try:
                with closing(readonly(seed)) as source:
                    source_version = source.execute("PRAGMA user_version").fetchone()[0]
                    if self.schema_version > 1 and source_version == self.schema_version:
                        self.check(source)
                    elif self.schema_version > source_version and source_version in (2, 3, 4, 5, 6, 7, 8, 9):
                        check_database(source, source_version)
                    else:
                        check_database(source)
                    with closing(sqlite3.connect(staging)) as target:
                        source.backup(target)
                os.replace(staging, self.path)
            finally:
                staging.unlink(missing_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=3)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        try:
            self.prepare_database()
            self.check(self.connection)
        except Exception:
            self.connection.close()
            raise

    @classmethod
    def open_existing_for_import(cls, path, *, read_only=False):
        """Worker-owned connection, strict existing schema; NEVER seed or migrate."""
        obj = cls.__new__(cls)
        obj.path = require_local(path)
        obj.backup_dir = obj.path.parent / "backups"
        if not obj.path.is_file():
            raise FileNotFoundError("Рабочая база не найдена; импорт не создаёт новую базу.")
        obj.connection = (readonly(obj.path) if read_only else
            sqlite3.connect(obj.path.as_uri() + "?mode=rw", uri=True, timeout=3))
        obj.connection.row_factory = sqlite3.Row
        obj.connection.execute("PRAGMA foreign_keys = ON")
        try:
            obj.check(obj.connection)
        except BaseException:
            obj.connection.close()
            raise
        return obj

    def preview_1c(self, text):
        from .import_storage import preview
        return preview(self, text)

    def import_1c(self, plan, selected, documents_root=None):
        from .import_storage import commit_import
        return commit_import(self, plan, selected, documents_root)

    def unseen_tender_ids(self):
        return set()

    def mark_tender_seen(self, tender_id):
        pass  # Legacy databases do not track first views.

    def prepare_database(self):
        pass

    def check(self, connection):
        check_database(connection, self.schema_version)

    def all(self):
        return [Tender(**dict(row)) for row in self.connection.execute(
            "SELECT " + ", ".join(COLUMNS) + " FROM tenders ORDER BY id")]

    def duplicates(self, number, exclude_id=None):
        from .import_1c import normalize_number, number_aliases
        key = normalize_number(number)
        return [t for t in self.all() if key and key in number_aliases(t.number)
                and t.id != exclude_id]

    def backup(self, manual=False):
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        day = now_moscow().strftime("%Y-%m-%d")
        daily = self.backup_dir / f"daily-{day}.db"
        if not manual and daily.exists():
            return daily
        token = uuid4().hex
        name = f"manual-{day}-{datetime.now():%H%M%S-%f}-{token}.db" if manual else daily.name
        target = self.backup_dir / name
        staging = self.backup_dir / f"{token}.tmp"
        try:
            with closing(sqlite3.connect(staging)) as copy:
                self.connection.backup(copy)
                self.check(copy)
            os.replace(staging, target)
        finally:
            staging.unlink(missing_ok=True)
        # Rotate only files whose names this application generates. A user may
        # keep an unrelated/manual archive in the same directory.
        copies = sorted((p for p in self.backup_dir.glob("*.db") if re.fullmatch(
            r'(?:daily-\d{4}-\d{2}-\d{2}|manual-\d{4}-\d{2}-\d{2}-\d{6}-\d{6}-[0-9a-f]{32})\.db', p.name)),
            key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for old in copies[14:]:
            old.unlink()
        return target

    def save(self, tender):
        validate_tender(tender)
        self.backup()
        values = tender.values()
        values["updated_at"] = now_moscow().isoformat(timespec="seconds") + "+03:00"
        with self.connection:
            if tender.id is None:
                values["created_at"] = values["updated_at"]
                names = COLUMNS[1:]
                cursor = self.connection.execute(
                    "INSERT INTO tenders (" + ", ".join(names) + ") VALUES (" +
                    ", ".join("?" for _ in names) + ")", [values[n] for n in names])
                values["id"] = cursor.lastrowid
            else:
                names = COLUMNS[1:]
                cursor = self.connection.execute(
                    "UPDATE tenders SET " + ", ".join(n + "=?" for n in names) + " WHERE id=?",
                    [values[n] for n in names] + [tender.id])
                if cursor.rowcount != 1:
                    raise ValueError("Запись не найдена. Введённый текст сохранён в карточке.")
        return Tender(**values)

    def delete_tender_comment(self, tender_id, expected, start, end):
        comment = expected[:start] + expected[end:]
        if start == 0:
            comment = comment.lstrip("\n")
        self.backup(manual=True)
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE tenders SET comment=?,updated_at=? WHERE id=? AND comment=?",
                (comment, now_moscow().isoformat(timespec="seconds") + "+03:00", tender_id, expected),
            )
            if cursor.rowcount != 1:
                raise ValueError("История уже изменилась. Обновите карточку перед удалением.")
        return next(t for t in self.all() if t.id == tender_id)

    def export_csv(self, path):
        path = Path(path).resolve()
        if path == self.path or path.suffix.lower() != ".csv":
            raise ValueError("Для экспорта выберите отдельный файл с расширением .csv.")
        with atomic_text(path, encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, delimiter=";")
            writer.writeheader()
            writer.writerows(t.values() for t in self.all())

    def restore(self, source):
        source = Path(source).resolve()
        if source == self.path:
            raise ValueError("Выберите резервную копию, а не текущую базу.")
        # Снимок в памяти не зависит от ротации файлов резервных копий.
        with closing(sqlite3.connect(":memory:")) as snapshot:
            with closing(readonly(source)) as copy:
                self.check(copy)
                copy.backup(snapshot)
            safety = self.backup(manual=True)
            snapshot.backup(self.connection)
            # SQLite backup writes do not advance total_changes/data_version on
            # the destination connection. Invalidate any materialized UI data.
            self._restore_generation = getattr(self, "_restore_generation", 0) + 1
        self.check(self.connection)
        return safety

    def close(self):
        self.connection.close()
