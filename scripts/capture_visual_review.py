"""Capture real Qt widgets on disposable fictional data, without user settings."""
import argparse
from pathlib import Path
import sys
from copy import deepcopy
from datetime import timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from tender_app.execution_seed import create_demo_database
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_window import ExecutionWindow
from tender_app.execution_comments import PositionCommentsDialog
from tender_app.execution_editor import SpecificationDialog, PositionDialog, PositionQuickEditDialog
from tender_app.models import now_moscow
from tender_app.settings import Settings


def capture(output, stress=False):
    output.mkdir(parents=True, exist_ok=True)
    database = output / "fictional.db"
    create_demo_database(database)
    repository = ExecutionRepository(database)
    if stress:
        tender = repository.all()[0]
        tender.customer = 'Учебный завод промышленного машиностроения «Длинное название заказчика»'
        tender.title = 'Вал приводной по чертежу ДЕМО.101 — комплект запасных частей для промышленного конвейера'
        tender.contacts = 'Учебный отдел снабжения\nИван Примеров · demo@example.invalid\nДоб. 123 · приём документов с 09:00 до 17:00 МСК'
        tender.comment = '\n'.join([
            'Учебный импорт реестра: исходные сведения сохранены без изменений.',
            'Строка 7 исходного листа; заказчик из заголовка группы.',
            'Наименование: вал приводной ДЕМО.101.',
            'Заказано: 24. Отгружено: 0. Остаток: 24.',
            'Единица измерения требует сверки с документами.',
            'Приложение поставщика: ДЕМО-П/1.',
            'Техническое пояснение о происхождении значений из исходного файла.',
        ])
        repository.save(tender)
        spec = repository.specifications()[0]
        spec.contract = 'Договор ДЕМО-Д/26 от 01.10.2026 с дополнительным соглашением № 123456789/2026'
        spec.positions = [deepcopy(spec.positions[0]) for _ in range(24)]
        for index, position in enumerate(spec.positions):
            position.id = None
            position.name = f'Позиция {index + 1} · Вал приводной по длинному обозначению чертежа ДЕМО.101.123456-2026'
            days = -1 if index == 0 else 3 if index == 1 else 30 + index
            position.due = (now_moscow().date() + timedelta(days=days)).isoformat()
            position.working_note = 'Отгружено 1 из 2, остальное в производстве' if index == 0 else ''
        repository.save_specification(spec)
        spec = repository.specifications()[0]
        for index in range(20):
            repository.set_position_comment(spec.id, spec.positions[0].id,
                f'Событие {index + 1}. Учебный комментарий о согласовании поставки.\nВторая строка с подробностями.')
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = ExecutionWindow(repository, Settings(output / "settings.json"), output)
    window.show()

    def shot(widget, name):
        for _ in range(12):
            app.processEvents()
        widget.grab().save(str(output / f"{name}.png"))

    window.resize(1440, 960)
    shot(window, "tenders")
    window.showMaximized()
    shot(window, "tenders-maximized")
    window.showNormal()
    window.resize(1440, 960)
    window.assistant_toggle.setChecked(True)
    shot(window, "tenders-assistant")
    window.assistant_toggle.setChecked(False)
    window.details_toggle.setChecked(True)
    shot(window, "tender-editor")
    window.details_toggle.setChecked(False)
    spec = next(s for s in repository.specifications() if not s.archived)
    window.open_specification(spec.id)
    shot(window, "specification")
    for width, height in ((1366, 768), (1920, 1080)):
        window.resize(width, height)
        shot(window, f"specification-{width}x{height}")
        if stress:
            window.execution.scroll.verticalScrollBar().setValue(400)
            shot(window, f"specification-{width}x{height}-pinned")
            window.execution.scroll.verticalScrollBar().setValue(0)
    window.resize(1440, 960)
    window.execution_assistant_toggle.setChecked(True)
    shot(window, "execution-assistant")
    window.execution_assistant_toggle.setChecked(False)
    window.execution.open_contract(window.execution.tree.currentItem().parent().data(0, 256))
    shot(window, "contract")
    window.open_specification(spec.id)
    window.resize(1000, 740)
    shot(window, "specification-narrow")
    window.execution_assistant_toggle.setChecked(True)
    shot(window, "execution-assistant-narrow")
    window.execution_assistant_toggle.setChecked(False)
    window.execution.scroll.verticalScrollBar().setValue(window.execution.scroll.verticalScrollBar().maximum())
    shot(window, "specification-comments")
    window.execution.state.setCurrentText("Архив")
    shot(window, "archive")
    window.execution.search.setText("Нет такого договора")
    shot(window, "empty")
    for dialog, name in (
        (PositionQuickEditDialog(spec.positions[0], "working_note", parent=window), "position-quick-edit"),
        (PositionCommentsDialog(repository, spec.id, spec.positions[0].id, window), "position-comments"),
        (SpecificationDialog(repository, spec, window), "specification-editor"),
        (PositionDialog(spec.positions[0], window), "position-editor"),
    ):
        dialog.show()
        shot(dialog, name)
        dialog.close()
    window.dirty = False
    window.close()
    repository.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--stress", action="store_true")
    args = parser.parse_args()
    capture(args.output, args.stress)
