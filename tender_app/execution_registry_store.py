"""Current registry notes and narrow, optimistic inline updates."""
import json

from tender_app.execution_groups import contract_key
from tender_app.execution_models import quantity, shown
from tender_app.models import now_moscow


def owner_key(owner):
    return json.dumps(owner, ensure_ascii=False, separators=(",", ":"))


class ExecutionRegistryStore:
    def migrate_registry_comments(self):
        self.check_structure(self.connection, 9)
        self.backup_before_migration(9)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute("""CREATE TABLE execution_registry_comments (
                id INTEGER PRIMARY KEY, owner TEXT NOT NULL,
                spec_id INTEGER REFERENCES execution_specs(id) ON DELETE RESTRICT,
                position_id INTEGER REFERENCES execution_positions(id) ON DELETE RESTRICT,
                stamp TEXT, message TEXT NOT NULL,
                source_comment_id INTEGER REFERENCES execution_comments(id) ON DELETE SET NULL,
                source_deleted INTEGER NOT NULL DEFAULT 0 CHECK(source_deleted IN (0,1)),
                CHECK(position_id IS NULL OR spec_id IS NOT NULL))""")
            self.connection.execute("CREATE INDEX registry_comments_owner ON execution_registry_comments(owner,id)")
            for spec in self.specifications():
                self._seed_registry_comment(("specification", spec.id), spec.id, None)
                for position in spec.positions:
                    self._seed_registry_comment(("position", spec.id, position.id), spec.id,
                                                position.id, position.working_note)
            self.connection.execute("PRAGMA user_version=10")
            self.check(self.connection)

    def _seed_registry_comment(self, owner, spec_id, position_id, fallback=""):
        row = self.connection.execute(
            "SELECT id,stamp,message FROM execution_comments WHERE spec_id=? AND position_id IS ? ORDER BY id DESC LIMIT 1",
            (spec_id, position_id)).fetchone()
        self._append_registry_comment(owner, row["message"] if row else fallback,
                                      row["stamp"] if row else None, row["id"] if row else None)

    def _append_registry_comment(self, owner, message, stamp, source_comment_id=None):
        spec_id = owner[1] if owner[0] in ("specification", "position") else None
        position_id = owner[2] if owner[0] == "position" else None
        self.connection.execute(
            "INSERT INTO execution_registry_comments(owner,spec_id,position_id,stamp,message,source_comment_id) VALUES (?,?,?,?,?,?)",
            (owner_key(owner), spec_id, position_id, stamp, message, source_comment_id))

    def registry_comments(self):
        return {owner: entry["message"] for owner, entry in self.registry_comment_summaries().items()}

    def registry_comment_summaries(self):
        return {row["owner"]: dict(stamp=row["stamp"], message=row["message"])
                for row in self.connection.execute(
                    "SELECT owner,stamp,message FROM execution_registry_comments ORDER BY id")}

    def registry_comment(self, owner):
        row = self.connection.execute(
            "SELECT message FROM execution_registry_comments WHERE owner=? ORDER BY id DESC LIMIT 1",
            (owner_key(owner),)).fetchone()
        return row[0] if row else ""

    def registry_comment_entries(self, owner):
        versions = [dict(row) for row in self.connection.execute(
            "SELECT id,stamp,message,source_comment_id,source_deleted FROM execution_registry_comments WHERE owner=? ORDER BY id DESC",
            (owner_key(owner),))]
        if owner[0] in ("specification", "position"):
            position_id = owner[2] if owner[0] == "position" else None
            represented = {row["source_comment_id"] for row in versions}
            legacy = [dict(row) for row in self.comment_entries(owner[1], position_id)
                      if row["id"] not in represented]
            for row in legacy:
                row["legacy"] = True
            versions.extend(legacy)
            if position_id is not None:
                row = self.connection.execute("SELECT working_note FROM execution_positions WHERE id=? AND spec_id=?",
                                              (position_id, owner[1])).fetchone()
                if row and row[0] and not any(item["message"] == row[0] for item in versions):
                    versions.append(dict(stamp=None, message=row[0], legacy=True))
        # No invented timestamp for migrated, undated notes.
        return sorted(versions, key=lambda row: row["stamp"] or "", reverse=True)

    def _validate_registry_owner(self, owner):
        if owner[0] == "contract":
            key = tuple(owner[1])
            tenders = {t.id: t for t in self.all()}
            specs = self.specifications()
            keys = {contract_key(tenders[s.tender_id], {"contract": s.contract}) for s in specs}
            linked = {s.tender_id for s in specs}
            keys.update(("unassigned", t.id) for t in tenders.values()
                        if t.status == "Победа" and t.id not in linked)
            if key not in keys:
                raise ValueError("Договор изменился или больше не найден. Обновите реестр.")
        elif owner[0] == "specification":
            if not self.connection.execute("SELECT 1 FROM execution_specs WHERE id=?", (owner[1],)).fetchone():
                raise ValueError("Спецификация не найдена.")
        elif owner[0] == "position":
            if not self.connection.execute("SELECT 1 FROM execution_positions WHERE spec_id=? AND id=?", owner[1:]).fetchone():
                raise ValueError("Позиция не найдена в этой спецификации.")
        else:
            raise ValueError("Неизвестный владелец комментария.")

    def update_registry_comment(self, owner, value, expected, *, append=False):
        value = value.strip()
        if append and not value:
            return False
        self.backup()
        stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._validate_registry_owner(owner)
            previous = self.registry_comment(owner)
            if not append and previous != expected:
                raise ValueError("Комментарий уже изменён в другом месте. Ваш текст оставлен в редакторе; отмените правку и обновите реестр.")
            if not append and previous == value:
                return False
            source_id = None
            if owner[0] in ("specification", "position"):
                spec_id = owner[1]
                position_id = owner[2] if owner[0] == "position" else None
                cursor = self.connection.execute(
                    "INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,?,?)",
                    (spec_id, position_id, stamp, value))
                source_id = cursor.lastrowid
                if position_id is not None:
                    self.connection.execute("UPDATE execution_positions SET comment=? WHERE id=? AND spec_id=?",
                                            (value, position_id, spec_id))
                self.connection.execute("UPDATE execution_specs SET updated=? WHERE id=?", (stamp, spec_id))
                self.connection.execute("INSERT INTO execution_history(spec_id,stamp,message) VALUES (?,?,?)",
                                        (spec_id, stamp, "Комментарий добавлен в реестре исполнения." if append
                                         else "Текущий комментарий изменён в реестре исполнения."))
            self._append_registry_comment(owner, value, stamp, source_id)
        return True

    def update_registry_field(self, owner, field, value, expected):
        """Read, validate and update under the same SQLite write lock."""
        allowed = ({"number", "date"} if owner[0] == "specification" else
                   {"name", "customer_shipped", "due", "stage", "supplier", "supplier_document", "transport"}
                   if owner[0] == "position" else set())
        if field not in allowed:
            raise ValueError("Это поле вычисляется или изменяется через реквизиты.")
        self.backup()
        stamp = now_moscow().isoformat(timespec="seconds") + "+03:00"
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            spec = next((s for s in self.specifications() if s.id == owner[1]), None)
            target = (spec if owner[0] == "specification" else
                      next((p for p in spec.positions if p.id == owner[2]), None) if spec else None)
            if target is None:
                raise ValueError("Запись больше не найдена. Обновите реестр.")
            previous = getattr(target, field)
            if previous != expected:
                raise ValueError("Поле уже изменено в другом месте. Ваш текст оставлен в редакторе; отмените правку и обновите реестр.")
            value = value.strip()
            if field in ("date", "due"):
                value = value or None
            elif field == "customer_shipped":
                value = quantity(value, "Отгружено заказчику", optional=True)
            if previous == value:
                return False
            setattr(target, field, value)
            spec.validate()
            table = "execution_specs" if owner[0] == "specification" else "execution_positions"
            stored = str(value) if field == "customer_shipped" and value is not None else value
            self.connection.execute(f"UPDATE {table} SET {field}=? WHERE id=?", (stored, target.id))
            self.connection.execute("UPDATE execution_specs SET updated=? WHERE id=?", (stamp, spec.id))
            labels = dict(number="Номер спецификации", date="Дата спецификации", name="Позиция / чертёж",
                          customer_shipped="Отгружено заказчику", due="Срок поставки", stage="Этап",
                          supplier="Изготовитель", supplier_document="Приложение", transport="Авто")
            message = f"{labels[field]}: {previous if previous is not None else 'не указано'} → {value if value is not None else 'не указано'}"
            if owner[0] == "position":
                if field == "customer_shipped":
                    message += f". Осталось: {shown(target.remaining)}."
                self.connection.execute("INSERT INTO execution_comments(spec_id,position_id,stamp,message) VALUES (?,?,?,?)",
                                        (spec.id, target.id, stamp, message))
            self.connection.execute("INSERT INTO execution_history(spec_id,stamp,message) VALUES (?,?,?)",
                                    (spec.id, stamp, message))
        return True
