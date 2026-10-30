"""Create a fictional seed in a new directory; never use source Excel."""
import sqlite3
from contextlib import closing
from pathlib import Path

from tender_app.execution_demo import demo_data
from tender_app.execution_models import Position, Specification, STAGES
from tender_app.execution_repository import ExecutionRepository
from tender_app.repository import COLUMNS


def create_demo_database(path):
    path = Path(path)
    if path.exists():
        raise FileExistsError("Демонстрационная база уже существует; перезапись запрещена.")
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(__file__).resolve().parents[1] / "data" / "sample" / "schema.sql"
    tenders, specs = demo_data()
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(schema.read_text(encoding="utf-8"))
        with connection:
            for tender in tenders:
                tender.folder_path = f"Тендеры/{tender.id}-{tender.number}"
                values = tender.values()
                connection.execute("INSERT INTO tenders (" + ",".join(COLUMNS) + ") VALUES (" +
                                   ",".join("?" for _ in COLUMNS) + ")", [values[n] for n in COLUMNS])
    repository = ExecutionRepository(path)
    try:
        for data in specs:
            values = dict(data)
            values.pop("id")
            values["positions"] = [Position(**p) for p in data["positions"]]
            values["history"] = []
            if values["stage"] not in STAGES:
                values["stage"] = "В производстве"
            spec = Specification(**values)
            note = "\n".join(f"{stamp}: {message}" for stamp, message in data["history"])
            repository.save_specification(spec, note)
    finally:
        repository.close()
