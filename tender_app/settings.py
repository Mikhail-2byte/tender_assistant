import json
from pathlib import Path
from tender_app.storage_io import atomic_text


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.values = {}
        self.load_warning = ""
        if self.path.exists():
            try:
                self.values = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(self.values, dict):
                    raise ValueError("Ожидается объект настроек")
            except (OSError, ValueError) as error:
                self.values = {}
                self.load_warning = f"Не удалось прочитать настройки: {error}. Используются исходные значения."

    def get(self, key, default=None):
        value = self.values.get(key, default)
        if default is not None and type(value) is not type(default):
            return default
        return value

    def save(self, **values):
        updated = self.values | values
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_text(self.path) as stream:
            json.dump(updated, stream, ensure_ascii=False, indent=2)
        self.values = updated
