"""Execution schema migrations are isolated from the main tender application."""
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import asdict, fields
from decimal import Decimal
from pathlib import Path

from tender_app.execution_models import Position, Specification, quantity, shown
from tender_app.models import now_moscow
from tender_app.repository import Repository, check_database, readonly
from tender_app.storage_io import atomic_text
from tender_app.execution_registry_store import ExecutionRegistryStore, owner_key


SPEC_FIELDS = tuple(f.name for f in fields(Specification) if f.name not in {"id", "positions", "history", "comments"})
POSITION_FIELDS = tuple(f.name for f in fields(Position) if f.name not in {"id", "comments"})
SCHEMA = (
    """CREATE TABLE execution_specs (
        id INTEGER PRIMARY KEY, tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE RESTRICT,
        number TEXT NOT NULL, title TEXT NOT NULL, date TEXT, contract TEXT NOT NULL,
        signed INTEGER NOT NULL CHECK(signed IN (0,1)), due TEXT, revised TEXT, eta TEXT,
        stage TEXT NOT NULL, risk TEXT NOT NULL, completed INTEGER NOT NULL CHECK(completed IN (0,1)),
        updated TEXT NOT NULL)""",
    "CREATE INDEX execution_specs_tender ON execution_specs(tender_id)",
    """CREATE TABLE execution_positions (
        id INTEGER PRIMARY KEY, spec_id INTEGER NOT NULL REFERENCES execution_specs(id) ON DELETE RESTRICT,
        ordinal INTEGER NOT NULL, name TEXT NOT NULL, unit TEXT NOT NULL, ordered TEXT NOT NULL,
        placed TEXT, customer_shipped TEXT, supplier TEXT NOT NULL, supplier_document TEXT NOT NULL,
        stage TEXT NOT NULL, note TEXT NOT NULL, transport TEXT NOT NULL, location TEXT NOT NULL, eta TEXT)""",
    "CREATE INDEX execution_positions_spec ON execution_positions(spec_id)",
    """CREATE TABLE execution_history (
        id INTEGER PRIMARY KEY, spec_id INTEGER NOT NULL REFERENCES execution_specs(id) ON DELETE RESTRICT,
        stamp TEXT NOT NULL, message TEXT NOT NULL)""",
    "CREATE INDEX execution_history_spec ON execution_history(spec_id)",
)


class ExecutionRepository(ExecutionRegistryStore, Repository):
    schema_version = 10

    def import_excel(self, plan, documents_root=None):
        from tender_app.database_import import commit_excel
        return commit_excel(self, plan, documents_root)

    def prepare_database(self):
        self._prepare_execution_database()
        if self.connection.execute('PRAGMA user_version').fetchone()[0] == 7:
            self.check_structure(self.connection, 7)
            self.backup_before_migration(7)
            with self.connection:
                self.connection.execute('BEGIN IMMEDIATE')
                self.connection.execute('''CREATE TABLE platform_checks (
                    id INTEGER PRIMARY KEY,
                    tender_id INTEGER NOT NULL REFERENCES tenders(id) ON DELETE RESTRICT,
                    platform TEXT NOT NULL, checked_at TEXT NOT NULL, source_1c TEXT,
                    url TEXT NOT NULL, deadline_raw TEXT NOT NULL,
                    deadline_date TEXT, deadline_time TEXT, deadline_timezone TEXT,
                    applied INTEGER NOT NULL CHECK(applied IN (0,1)),
                    files_json TEXT NOT NULL, messages_json TEXT NOT NULL)''')
                self.connection.execute('CREATE INDEX platform_checks_tender ON platform_checks(tender_id,id)')
                self.connection.execute('PRAGMA user_version=8')
                self.check_structure(self.connection, 8)
        if self.connection.execute('PRAGMA user_version').fetchone()[0] == 8:
            self.check_structure(self.connection, 8)
            self.backup_before_migration(8)
            with self.connection:
                self.connection.execute('BEGIN IMMEDIATE')
                self.connection.execute('ALTER TABLE tenders ADD COLUMN is_unseen INTEGER NOT NULL DEFAULT 0 CHECK(is_unseen IN (0,1))')
                self.connection.execute('PRAGMA user_version=9')
                self.check_structure(self.connection, 9)
        if self.connection.execute('PRAGMA user_version').fetchone()[0] == 9:
            self.migrate_registry_comments()

    def unseen_tender_ids(self):
        return {row[0] for row in self.connection.execute('SELECT id FROM tenders WHERE is_unseen=1')}

    def mark_tender_seen(self, tender_id):
        with self.connection:
            self.connection.execute('UPDATE tenders SET is_unseen=0 WHERE id=? AND is_unseen=1', (tender_id,))

    def import_1c(self, plan, selected, documents_root=None):
        from .import_storage import commit_import
        return commit_import(self, plan, selected, documents_root, mark_unseen=True)

    def _prepare_execution_database(self):
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 2:
            self.migrate_archive()
            self.migrate_comments()
            self.migrate_comment_history()
            self.migrate_position_due()
            self.migrate_working_note()
            return
        if version == 3:
            self.migrate_comments()
            self.migrate_comment_history()
            self.migrate_position_due()
            self.migrate_working_note()
            return
        if version == 4:
            self.migrate_comment_history()
            self.migrate_position_due()
            self.migrate_working_note()
            return
        if version == 5:
            self.migrate_position_due()
            self.migrate_working_note()
            return
        if version == 6:
            self.migrate_working_note()
            return
        if version != 1:
            return  # check() rejects unsupported versions without modifying them.
        check_database(self.connection)
        backup = self.path.parent / "before-execution-v2.db"
        if backup.exists():
            with closing(readonly(backup)) as copy:
                check_database(copy)
                if list(copy.iterdump()) != list(self.connection.iterdump()):
                    raise ValueError("Копия до миграции относится к другим данным. Сохраните её отдельно.")
        else:
            staging = backup.with_suffix(".tmp")
            try:
                with closing(sqlite3.connect(staging)) as copy:
                    self.connection.backup(copy)
                    check_database(copy)
                os.replace(staging, backup)
            finally:
                staging.unlink(missing_ok=True)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            for statement in SCHEMA:
                self.connection.execute(statement)
            self.connection.execute("PRAGMA user_version = 2")
            check_database(self.connection, 2)
        self.migrate_archive()
        self.migrate_comments()
        self.migrate_comment_history()
        self.migrate_position_due()
        self.migrate_working_note()

    def migrate_archive(self):
        self.check_structure(self.connection, 2)
        self.backup_before_migration(2)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("ALTER TABLE execution_specs ADD COLUMN archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1))")
            self.connection.execute("UPDATE execution_specs SET archived=1 WHERE completed=1")
            self.connection.execute("PRAGMA user_version = 3")
            self.check_structure(self.connection, 3)

    def migrate_comments(self):
        self.check_structure(self.connection, 3)
        self.backup_before_migration(3)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("ALTER TABLE execution_positions ADD COLUMN comment TEXT NOT NULL DEFAULT ''")
            self.connection.execute("PRAGMA user_version = 4")
            self.check_structure(self.connection, 4)

    def migrate_comment_history(self):
        self.check_structure(self.connection, 4)
        self.backup_before_migration(4)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("""CREATE TABLE execution_comments (
                id INTEGER PRIMARY KEY, spec_id INTEGER NOT NULL REFERENCES execution_specs(id) ON DELETE RESTRICT,
                position_id INTEGER REFERENCES execution_positions(id) ON DELETE RESTRICT,
                stamp TEXT, message TEXT NOT NULL)""")
            self.connection.execute("CREATE INDEX execution_comments_spec ON execution_comments(spec_id, position_id)")
            positions = self.connection.execute("SELECT id, spec_id, name, comment FROM execution_positions ORDER BY id").fetchall()
            for row in self.connection.execute("SELECT spec_id, stamp, message FROM execution_history ORDER BY id").fetchall():
                message = row["message"]
                matches = [p for p in positions if p["spec_id"] == row["spec_id"]
                           and message.startswith(f"Комментарий к позиции «{p['name']}»:\n")]
                if len(matches) == 1:
                    self.connection.execute("INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,?,?)",
                        (row["spec_id"], matches[0]["id"], row["stamp"], message[len(f"Комментарий к позиции «{matches[0]['name']}»:\n"):]))
                elif not message.startswith(("Импорт ", "Комментарий к позиции «", "Перенесена в архив", "Возвращена в работу.", "Изменения спецификации сохранены.")):
                    self.connection.execute("INSERT INTO execution_comments(spec_id,stamp,message) VALUES (?,?,?)", tuple(row))
            for p in positions:
                latest = self.connection.execute("SELECT message FROM execution_comments WHERE position_id=? ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()
                if p["comment"].strip() and (latest is None or latest[0] != p["comment"]):
                    self.connection.execute("INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,NULL,?)",
                                            (p["spec_id"], p["id"], p["comment"]))
            self.connection.execute("PRAGMA user_version=5")
            self.check_structure(self.connection, 5)

    def migrate_position_due(self):
        self.check_structure(self.connection, 5)
        self.backup_before_migration(5)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("ALTER TABLE execution_positions ADD COLUMN due TEXT")
            self.connection.execute("PRAGMA user_version=6")
            self.check_structure(self.connection, 6)

    def migrate_working_note(self):
        self.check_structure(self.connection, 6)
        self.backup_before_migration(6)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("ALTER TABLE execution_positions ADD COLUMN working_note TEXT NOT NULL DEFAULT ''")
            self.connection.execute("UPDATE execution_positions SET working_note=comment")
            self.connection.execute("PRAGMA user_version=7")
            self.check_structure(self.connection, 7)

    def backup_before_migration(self, version):
        backup = self.path.parent / f"before-execution-v{version+1}.db"
        if backup.exists():
            with closing(readonly(backup)) as copy:
                self.check_structure(copy, version)
                if list(copy.iterdump()) != list(self.connection.iterdump()):
                    raise ValueError("Копия до миграции относится к другим данным. Сохраните её отдельно.")
        else:
            staging = backup.with_suffix(".tmp")
            try:
                with closing(sqlite3.connect(staging)) as copy:
                    self.connection.backup(copy)
                    self.check_structure(copy, version)
                os.replace(staging, backup)
            finally:
                staging.unlink(missing_ok=True)

    def check(self, connection):
        self.check_structure(connection, self.schema_version)

    def check_structure(self, connection, version):
        check_database(connection, version)
        if version >= 10:
            required = {"id", "owner", "spec_id", "position_id", "stamp", "message", "source_comment_id", "source_deleted"}
            if not required.issubset({r[1] for r in connection.execute("PRAGMA table_info(execution_registry_comments)")}):
                raise ValueError("Неполная структура комментариев реестра исполнения.")
            if connection.execute("""SELECT 1 FROM execution_registry_comments c
                    JOIN execution_positions p ON p.id=c.position_id WHERE c.spec_id<>p.spec_id LIMIT 1""").fetchone():
                raise ValueError("Комментарий реестра связан с позицией другой спецификации.")
        if version >= 9:
            if 'is_unseen' not in {row[1] for row in connection.execute('PRAGMA table_info(tenders)')}:
                raise ValueError('Нет отметок новых тендеров.')
        if version >= 8:
            required = {'id', 'tender_id', 'platform', 'checked_at', 'source_1c', 'url',
                        'deadline_raw', 'deadline_date', 'deadline_time', 'deadline_timezone',
                        'applied', 'files_json', 'messages_json'}
            if not required.issubset({row[1] for row in connection.execute('PRAGMA table_info(platform_checks)')}):
                raise ValueError('Неполная структура проверок площадок.')
        spec_fields = set(SPEC_FIELDS) - ({"archived"} if version == 2 else set())
        position_fields = set(POSITION_FIELDS) - ({"comment"} if version < 4 else set()) - ({"due"} if version < 6 else set()) - ({"working_note"} if version < 7 else set())
        for table, required in (("execution_specs", {"id", *spec_fields}),
                                ("execution_positions", {"id", "spec_id", "ordinal", *position_fields}),
                                ("execution_history", {"id", "spec_id", "stamp", "message"})):
            if not required.issubset({row[1] for row in connection.execute(f"PRAGMA table_info({table})")}):
                raise ValueError("Неполная структура данных исполнения.")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Нарушены связи данных исполнения.")
        if version >= 5:
            if not {"id", "spec_id", "position_id", "stamp", "message"}.issubset(
                    {row[1] for row in connection.execute("PRAGMA table_info(execution_comments)")}):
                raise ValueError("Неполная структура комментариев.")
            if connection.execute("""SELECT 1 FROM execution_comments c JOIN execution_positions p ON p.id=c.position_id
                                      WHERE c.spec_id<>p.spec_id LIMIT 1""").fetchone():
                raise ValueError("Комментарий связан с позицией другой спецификации.")

    def specifications(self):
        result = []
        specifications = {}
        for row in self.connection.execute("SELECT * FROM execution_specs ORDER BY id"):
            values = dict(row)
            values["signed"] = bool(values["signed"])
            values["completed"] = bool(values["completed"])
            values["archived"] = bool(values["archived"])
            spec = Specification(**values)
            result.append(spec)
            specifications[spec.id] = spec
        positions = {}
        for row in self.connection.execute("SELECT * FROM execution_positions ORDER BY spec_id, ordinal, id"):
            data = dict(row)
            spec_id = data.pop("spec_id")
            del data["ordinal"]
            for name in ("ordered", "placed", "customer_shipped"):
                data[name] = Decimal(data[name]) if data[name] is not None else None
            item = Position(**data)
            specifications[spec_id].positions.append(item)
            positions[(spec_id, item.id)] = item
        for row in self.connection.execute("SELECT spec_id, stamp, message FROM execution_history ORDER BY id DESC"):
            specifications[row["spec_id"]].history.append((row["stamp"], row["message"]))
        for row in self.connection.execute("SELECT spec_id, position_id, stamp, message FROM execution_comments ORDER BY id DESC"):
            owner = (specifications[row["spec_id"]] if row["position_id"] is None
                     else positions[(row["spec_id"], row["position_id"])])
            owner.comments.append((row["stamp"], row["message"]))
        return result

    def save_specification(self, spec, note=""):
        spec.validate()
        previous = self.connection.execute("SELECT completed FROM execution_specs WHERE id=?", (spec.id,)).fetchone()
        if spec.completed and (previous is None or not previous[0]):
            spec.archived = True
        tender = self.connection.execute("SELECT status FROM tenders WHERE id=?", (spec.tender_id,)).fetchone()
        if tender is None or (spec.id is None and tender[0] != "Победа"):
            raise ValueError("Новую спецификацию можно добавить только к выигранному тендеру.")
        if spec.id is not None:
            existing = self.connection.execute("SELECT tender_id FROM execution_specs WHERE id=?", (spec.id,)).fetchone()
            if existing is None or existing[0] != spec.tender_id:
                raise ValueError("Спецификация не найдена или принадлежит другому тендеру.")
        ids = [p.id for p in spec.positions if p.id is not None]
        owned = {r[0] for r in self.connection.execute("SELECT id FROM execution_positions WHERE spec_id=?", (spec.id,))}
        if len(ids) != len(set(ids)) or not set(ids).issubset(owned):
            raise ValueError("Позиция принадлежит другой спецификации или повторяется.")
        for position_id in owned - set(ids):
            if self.connection.execute("SELECT 1 FROM execution_comments WHERE position_id=?", (position_id,)).fetchone():
                raise ValueError("Нельзя удалить позицию с историей комментариев. Сохраните её для истории поставки.")
            if self.connection.execute("SELECT 1 FROM execution_registry_comments WHERE position_id=? AND message<>''", (position_id,)).fetchone():
                raise ValueError("Нельзя удалить позицию с историей комментариев реестра.")
        self.backup()
        stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
        values = asdict(spec)
        values["updated"] = stamp
        with self.connection:
            if spec.id is None:
                cursor = self.connection.execute(
                    "INSERT INTO execution_specs (" + ",".join(SPEC_FIELDS) + ") VALUES (" +
                    ",".join("?" for _ in SPEC_FIELDS) + ")", [values[n] for n in SPEC_FIELDS])
                spec_id = cursor.lastrowid
            else:
                spec_id = spec.id
                self.connection.execute("UPDATE execution_specs SET " + ",".join(n + "=?" for n in SPEC_FIELDS)
                                        + " WHERE id=?", [values[n] for n in SPEC_FIELDS] + [spec_id])
            for position_id in owned - set(ids):
                self.connection.execute("DELETE FROM execution_registry_comments WHERE position_id=?", (position_id,))
                self.connection.execute("DELETE FROM execution_positions WHERE id=? AND spec_id=?", (position_id, spec_id))
            for ordinal, position in enumerate(spec.positions):
                data = asdict(position)
                previous_comment = self.connection.execute("SELECT comment, working_note FROM execution_positions WHERE id=?", (position.id,)).fetchone()
                for name in ("ordered", "placed", "customer_shipped"):
                    data[name] = str(data[name]) if data[name] is not None else None
                if position.id is None:
                    names = ("spec_id", "ordinal", *POSITION_FIELDS)
                    data.update(spec_id=spec_id, ordinal=ordinal)
                    cursor = self.connection.execute("INSERT INTO execution_positions (" + ",".join(names) + ") VALUES (" +
                                            ",".join("?" for _ in names) + ")", [data[n] for n in names])
                    position_id = cursor.lastrowid
                else:
                    position_id = position.id
                    self.connection.execute("UPDATE execution_positions SET ordinal=?," +
                                            ",".join(n + "=?" for n in POSITION_FIELDS) + " WHERE id=? AND spec_id=?",
                                            [ordinal] + [data[n] for n in POSITION_FIELDS] + [position.id, spec_id])
                if data["comment"].strip() and (previous_comment is None or previous_comment[0] != data["comment"]):
                    cursor = self.connection.execute("INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,?,?)",
                                            (spec_id, position_id, stamp, data["comment"]))
                    self._append_registry_comment(("position", spec_id, position_id), data["comment"], stamp, cursor.lastrowid)
                previous_note = previous_comment[1] if previous_comment is not None else ""
                if previous_comment is not None and previous_note != data["working_note"]:
                    self.connection.execute("INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,?,?)",
                        (spec_id, position_id, stamp,
                         f"Рабочая заметка: {previous_note or 'не указано'} → {data['working_note'] or 'не указано'}"))
                    self._append_registry_comment(("position", spec_id, position_id), data["working_note"], stamp)
                elif previous_comment is None and not data["comment"] and data["working_note"]:
                    self._append_registry_comment(("position", spec_id, position_id), data["working_note"], None)
            self.connection.execute("INSERT INTO execution_history (spec_id,stamp,message) VALUES (?,?,?)",
                                    (spec_id, stamp, note.strip() or "Изменения спецификации сохранены."))
            if note.strip() and not note.startswith("Импорт "):
                cursor = self.connection.execute("INSERT INTO execution_comments(spec_id,stamp,message) VALUES (?,?,?)", (spec_id, stamp, note.strip()))
                self._append_registry_comment(("specification", spec_id), note.strip(), stamp, cursor.lastrowid)
        return next(s for s in self.specifications() if s.id == spec_id)

    def set_archived(self, spec_id, archived, note=""):
        row = self.connection.execute("SELECT archived FROM execution_specs WHERE id=?", (spec_id,)).fetchone()
        if row is None:
            raise ValueError("Спецификация не найдена.")
        if bool(row[0]) == archived:
            return
        self.backup()
        stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
        with self.connection:
            self.connection.execute("UPDATE execution_specs SET archived=?, updated=? WHERE id=?", (archived, stamp, spec_id))
            self.connection.execute("INSERT INTO execution_history (spec_id,stamp,message) VALUES (?,?,?)",
                                    (spec_id, stamp, note or ("Перенесена в архив." if archived else "Возвращена в работу.")))

    def set_position_comment(self, spec_id, position_id, comment):
        row = self.connection.execute("SELECT name, comment FROM execution_positions WHERE id=? AND spec_id=?",
                                      (position_id, spec_id)).fetchone()
        if row is None:
            raise ValueError("Позиция не найдена в этой спецификации.")
        comment = comment.strip()
        if not comment:
            raise ValueError("Введите комментарий.")
        self.backup()
        stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
        with self.connection:
            self.connection.execute("UPDATE execution_positions SET comment=? WHERE id=? AND spec_id=?", (comment, position_id, spec_id))
            self.connection.execute("UPDATE execution_specs SET updated=? WHERE id=?", (stamp, spec_id))
            self.connection.execute("INSERT INTO execution_history(spec_id,stamp,message) VALUES (?,?,?)",
                                    (spec_id, stamp, f"Комментарий к позиции «{row[0]}»:\n{comment or '(очищен)'}"))
            cursor = self.connection.execute("INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,?,?)",
                                    (spec_id, position_id, stamp, comment))
            self._append_registry_comment(("position", spec_id, position_id), comment, stamp, cursor.lastrowid)

    def update_position_field(self, spec_id, position_id, field, value):
        labels = {"due": "Срок поставки", "supplier": "Изготовитель",
                  "transport": "Транспорт", "supplier_document": "Приложение поставщика", "working_note": "Рабочая заметка"}
        if field not in labels:
            raise ValueError("Поле не поддерживает быструю правку.")
        spec = next((s for s in self.specifications() if s.id == spec_id), None)
        position = next((p for p in spec.positions if p.id == position_id), None) if spec else None
        if position is None:
            raise ValueError("Позиция не найдена в этой спецификации.")
        value = value.strip() if value else ""
        if field == "due":
            value = value or None
        previous = getattr(position, field)
        if previous == value:
            return False
        setattr(position, field, value)
        position.validate()
        self.backup()
        stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
        message = f"{labels[field]}: {previous or 'не указано'} → {value or 'не указано'}"
        with self.connection:
            self.connection.execute(f"UPDATE execution_positions SET {field}=? WHERE id=? AND spec_id=?",
                                    (value, position_id, spec_id))
            self.connection.execute("UPDATE execution_specs SET updated=? WHERE id=?", (stamp, spec_id))
            self.connection.execute("INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,?,?)",
                                    (spec_id, position_id, stamp, message))
            self.connection.execute("INSERT INTO execution_history(spec_id,stamp,message) VALUES (?,?,?)",
                                    (spec_id, stamp, f"Позиция «{position.name}»: {message}"))
            if field == "working_note":
                self._append_registry_comment(("position", spec_id, position_id), value, stamp)
        return True

    def set_position_shipped(self, spec_id, position_id, value):
        spec = next((s for s in self.specifications() if s.id == spec_id), None)
        position = next((p for p in spec.positions if p.id == position_id), None) if spec else None
        if position is None:
            raise ValueError("Позиция не найдена в этой спецификации.")
        shipped = quantity(value, "Отгружено заказчику", optional=True)
        if shipped == position.customer_shipped:
            return
        previous = position.customer_shipped
        position.customer_shipped = shipped
        spec.validate()
        message = f"Отгружено заказчику: {shown(previous)} → {shown(shipped)}. Осталось: {shown(position.remaining)}."
        self.backup()
        stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
        with self.connection:
            self.connection.execute("UPDATE execution_positions SET customer_shipped=?,comment=? WHERE id=? AND spec_id=?",
                                    (str(shipped) if shipped is not None else None, message, position_id, spec_id))
            self.connection.execute("UPDATE execution_specs SET updated=? WHERE id=?", (stamp, spec_id))
            self.connection.execute("INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,?,?)",
                                    (spec_id, position_id, stamp, message))
            self.connection.execute("INSERT INTO execution_history(spec_id,stamp,message) VALUES (?,?,?)",
                                    (spec_id, stamp, f"Отгрузка позиции «{position.name}»:\n{message}"))

    def comment_entries(self, spec_id, position_id=None):
        return self.connection.execute(
            "SELECT id,stamp,message FROM execution_comments WHERE spec_id=? AND position_id IS ? ORDER BY id DESC",
            (spec_id, position_id),
        ).fetchall()

    def delete_execution_comment(self, spec_id, position_id, comment_id):
        self.backup(manual=True)
        with self.connection:
            owner = ("position", spec_id, position_id) if position_id is not None else ("specification", spec_id)
            current = self.connection.execute(
                "SELECT source_comment_id FROM execution_registry_comments WHERE owner=? ORDER BY id DESC LIMIT 1",
                (owner_key(owner),)).fetchone()
            self.connection.execute("UPDATE execution_registry_comments SET source_deleted=1 WHERE source_comment_id=?", (comment_id,))
            cursor = self.connection.execute(
                "DELETE FROM execution_comments WHERE id=? AND spec_id=? AND position_id IS ?",
                (comment_id, spec_id, position_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Комментарий уже удалён или не найден. Обновите карточку.")
            if position_id is not None:
                latest = self.connection.execute(
                    "SELECT message FROM execution_comments WHERE spec_id=? AND position_id=? ORDER BY id DESC LIMIT 1",
                    (spec_id, position_id),
                ).fetchone()
                self.connection.execute("UPDATE execution_positions SET comment=? WHERE id=? AND spec_id=?",
                                        (latest[0] if latest else "", position_id, spec_id))
            stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
            if current and current[0] == comment_id:
                previous = self.connection.execute(
                    "SELECT message FROM execution_registry_comments WHERE owner=? AND source_deleted=0 ORDER BY id DESC LIMIT 1",
                    (owner_key(owner),)).fetchone()
                self._append_registry_comment(owner, previous[0] if previous else "", stamp)
            self.connection.execute("UPDATE execution_specs SET updated=? WHERE id=?", (stamp, spec_id))

    def add_specification_comment(self, spec_id, comment):
        comment = comment.strip()
        if not comment:
            raise ValueError("Введите комментарий.")
        if not self.connection.execute("SELECT 1 FROM execution_specs WHERE id=?", (spec_id,)).fetchone():
            raise ValueError("Спецификация не найдена.")
        self.backup()
        stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
        with self.connection:
            cursor = self.connection.execute("INSERT INTO execution_comments(spec_id,stamp,message) VALUES (?,?,?)", (spec_id, stamp, comment))
            self._append_registry_comment(("specification", spec_id), comment, stamp, cursor.lastrowid)
            self.connection.execute("UPDATE execution_specs SET updated=? WHERE id=?", (stamp, spec_id))

    def restore(self, source):
        with closing(readonly(source)) as copy:
            version = copy.execute("PRAGMA user_version").fetchone()[0]
        if version not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            return super().restore(source)
        # Мигрируем временную копию, сохраняя исходный файл резервной копии.
        with tempfile.TemporaryDirectory(prefix="execution-restore-") as directory:
            path = Path(directory) / "tenders.db"
            with closing(readonly(source)) as copy, closing(sqlite3.connect(path)) as target:
                copy.backup(target)
            migrated = ExecutionRepository(path)
            migrated.close()
            return super().restore(path)

    def export_execution(self, path):
        path = Path(path).resolve()
        if path.suffix.lower() != ".json" or path in {self.path, self.path.parent / "before-execution-v2.db"}:
            raise ValueError("Выберите отдельный файл .json для полного экспорта.")
        data = {"schema_version": self.schema_version, "tenders": [t.values() for t in self.all()],
                "specifications": [asdict(s) for s in self.specifications()],
                "registry_comments": [dict(r) for r in self.connection.execute("SELECT * FROM execution_registry_comments ORDER BY id")]}
        with atomic_text(path) as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, default=str)
