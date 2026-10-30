"""Архив переносимой версии с явными записями пустых папок."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import argparse

parser = argparse.ArgumentParser()
mode = parser.add_mutually_exclusive_group()
mode.add_argument("--chat-preview", action="store_true")
mode.add_argument("--execution-preview", action="store_true")
args = parser.parse_args()
name = "TenderAssistantExecutionPreview"

root = Path(__file__).resolve().parents[1]
# Упаковывается чистая сборка, а не папка запуска с рабочими документами.
portable = root / "build" / "portable-output" / name
archive = root / "dist" / f"{name}-portable.zip"
archive.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
    for path in sorted(portable.rglob("*")):
        relative = path.relative_to(portable.parent)
        if path.relative_to(portable).parts[0] == "data":
            continue  # Только чистая поставка, без пользовательской базы и настроек.
        output.write(path, relative.as_posix())
print(archive)
