"""Exercise real Qt navigation on fictional data and record retained resources."""
import argparse
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QTimer, qInstallMessageHandler
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid, getAllValidWrappers

from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.execution_window import ExecutionWindow
from tender_app.settings import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--cycles', type=int, default=200)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    if args.cycles < 80:
        parser.error('Use at least 80 cycles to measure after warmup.')
    if args.timeout <= 0:
        parser.error('Timeout must be positive.')
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'cycles': 0, 'samples': [], 'errors': [], 'object_metric': 'existing-python-qobject-wrappers'}
    try:
        import psutil
        process = psutil.Process()
    except ImportError:
        process = None
    app = QApplication.instance() or QApplication([])
    # The offscreen Windows plugin does not enumerate system font families.
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/segoeui.ttf'
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
    app.setQuitOnLastWindowClosed(False)

    def message(kind, context, text):
        if any(part in text for part in ('already deleted', 'QThread: Destroyed', 'Cannot set parent')):
            result['errors'].append(text)

    old_handler = qInstallMessageHandler(message)
    previous_hook = sys.excepthook

    def failed(kind, error, _traceback):
        result['errors'].append(f'{kind.__name__}: {error}')
        app.quit()

    sys.excepthook = failed
    with tempfile.TemporaryDirectory(prefix='tender-ui-check-') as temporary:
        root = Path(temporary)
        path = root / 'tenders.db'
        create_demo_database(path)
        repository = ExecutionRepository(path)
        window = ExecutionWindow(repository, Settings(root / 'settings.json'), root)
        window.resize(1366, 768)
        window.show()
        specifications = repository.specifications()
        clock = time.monotonic()
        driver = QObject()
        step = 0

        def tick():
            nonlocal step
            cycle, stage = divmod(step, 8)
            spec = specifications[cycle % len(specifications)]
            if stage == 0:
                window.open_specification(spec.id)
                window.execution.tree.expandAll()
            elif stage == 1:
                for table in window.execution.position_tables:
                    table.horizontalHeader().resizeSection(0, 200 + cycle % 15)
                    if table.details:
                        table.details[0].toggle.click()
                window.execution.scroll.verticalScrollBar().setValue(120)
            elif stage == 2:
                window.refresh_execution()
                window.execution.tree.collapseAll()
            elif stage == 3:
                window.pages.setCurrentIndex(0)
                window.open_registry_tender(spec.tender_id)
                window.return_to_registry()
            elif stage == 4:
                window.toggle_agent_dock()
                window.showMinimized()
            elif stage == 5:
                window.showNormal()
                assert window.agent_page.parentWidget() == window.agent_dock
                window.agent_dock.hide()
            elif stage == 6:
                window.pages.setCurrentIndex(1)
                window.open_specification(spec.id)
            else:
                result['cycles'] = cycle + 1
                if (cycle + 1) % 20 == 0:
                    gc.collect()
                    sample = {'cycle': cycle + 1,
                              'qobjects': sum(isinstance(obj, QObject) for obj in getAllValidWrappers()),
                              'ai_jobs': len(window.ai_service.jobs),
                              'scroll_receivers': window.execution.scroll.verticalScrollBar().receivers('2valueChanged(int)'),
                              'resource_job': window.resource_job is not None if hasattr(window, 'resource_job') else False}
                    if process:
                        sample['rss'] = process.memory_info().rss
                        sample['handles'] = process.num_handles() if os.name == 'nt' else process.num_fds()
                    result['samples'].append(sample)
                if cycle + 1 == args.cycles:
                    window.grab().save(str(args.output / 'execution.png'))
                    app.quit()
                    return
            step += 1
            QTimer.singleShot(1, driver, tick)

        def timeout():
            result['errors'].append(f'UI check exceeded {args.timeout} seconds')
            app.quit()

        watchdog = QTimer(driver)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(timeout)
        watchdog.start(args.timeout * 1000)
        QTimer.singleShot(0, driver, tick)
        try:
            app.exec()
            watchdog.stop()
            samples = result['samples'][2:]
            if samples:
                result['object_growth'] = samples[-1]['qobjects'] - samples[0]['qobjects']
                if result['object_growth'] > 30:
                    result['errors'].append('QObject count grew after warmup')
                result['scroll_receiver_growth'] = samples[-1]['scroll_receivers'] - samples[0]['scroll_receivers']
                if result['scroll_receiver_growth'] > 0:
                    result['errors'].append('Scroll callbacks accumulated after warmup')
                if process:
                    result['rss_growth'] = samples[-1]['rss'] - samples[0]['rss']
                    result['handle_growth'] = samples[-1]['handles'] - samples[0]['handles']
                    if result['rss_growth'] > 32 * 1024 * 1024 or result['handle_growth'] > 20:
                        result['errors'].append('Memory or handles grew after warmup')
            result['seconds'] = round(time.monotonic() - clock, 2)
        finally:
            if isValid(window):
                window.dirty = False
                window.close()
                window.deleteLater()
                app.processEvents()
            repository.close()
            sys.excepthook = previous_hook
            qInstallMessageHandler(old_handler)
    result['passed'] = result['cycles'] == args.cycles and not result['errors']
    (args.output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
