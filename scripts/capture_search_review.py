"""Capture shared search and entity accents on fictional, isolated data."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    app = QApplication([])
    for font in ('segoeui.ttf', 'arial.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + font)
    app.setFont(QFont('Segoe UI', 10))
    app.setStyle('Fusion')
    with tempfile.TemporaryDirectory(prefix='search-review-', dir=output) as temporary:
        data = Path(temporary)
        database = data / 'fictional.db'
        create_demo_database(database)
        repo = ExecutionRepository(database)
        spec = next(s for s in repo.specifications() if not s.archived and len(s.positions) > 1)
        spec.number = '9 от 23.04.2026'
        spec.positions[-1].supplier_document = 'ДЕМО-TC прил.234 от 14/05/2026'
        spec = repo.save_specification(spec)
        window = ExecutionWindow(repo, Settings(data / 'settings.json'), data)
        window.show()
        sizes = []

        def capture(name):
            for _ in range(6):
                app.processEvents()
            window.grab().save(str(output / (name + '.png')))

        for width, height in ((1152, 768), (1600, 900)):
            window.resize(width, height)
            window.pages.setCurrentIndex(0)
            window.global_search.setText('приложение 234')
            QTest.qWait(210)
            assert window.search_results.list.count() == 1
            capture(f'search-{width}')
            window.global_search.clear()
            window.open_search_position(spec.id, spec.positions[-1].id)
            QTest.qWait(30)
            capture(f'specification-{width}')
            window.pages.setCurrentIndex(3)
            window.execution_registry.expand_all()
            capture(f'execution-registry-{width}')
            window.open_registry_tender(spec.tender_id)
            capture(f'tender-card-{width}')
            window.pages.setCurrentIndex(2)
            if not window.agent_page.history.active['messages']:
                window.agent_page.chat.input.setPlainText('Найди приложение 234')
                window.agent_page.send_message()
            capture(f'chat-{width}')
            sizes.append(dict(requested=[width, height], actual=[window.width(), window.height()],
                              device_pixel_ratio=window.devicePixelRatioF()))
        window.close()
        repo.close()
        app.processEvents()
    (output / 'summary.json').write_text(json.dumps(sizes, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
