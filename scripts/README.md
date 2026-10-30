# Служебные команды

Снимки нового реестра исполнения: `.\.venv\Scripts\python.exe scripts/capture_execution_registry.py` — временная вымышленная база, свёрнутые/раскрытые таблицы трёх размеров и редактор комментария в `artifacts/checks/execution-registry/visual/`.

Выполняйте из корня проекта в PowerShell. Скрипты импорта запускаются как модули, чтобы находить пакет `tender_app`.

Проверка единого Excel-переноса: `.\.venv\Scripts\python.exe scripts/capture_database_import.py` создаёт только вымышленные шаблон, пример и снимки интерфейса в `artifacts/checks/database-import/`. `.\.venv\Scripts\python.exe run_execution.py --smoke-test --check-database-import --data-dir artifacts/checks/database-import/runtime` дополнительно проверяет генерацию шаблона, создание отдельной пустой базы, связанный перенос и пропуск повторного файла. `scripts/verify_portable.py` запускает эту проверку внутри распакованного EXE без Python в PATH.

Снимки общей карточки и компактных позиций: `.\.venv\Scripts\python.exe scripts/capture_compact_won_card.py`; временная вымышленная база, результат — `artifacts/checks/compact-won-card/visual/`. Первым аргументом можно задать другой каталог вывода; дополнительно снимается стрессовый вид с длинными вымышленными контактами и описанием.

| Команда | Назначение |
|---|---|
| `.\.venv\Scripts\python.exe scripts/build_app_icon.py` | Пересоздать ICO (9 размеров, 16–256 px) из `tender_app/assets/tender-assistant.svg`. Сборка portable вызывает автоматически. |
| `powershell -ExecutionPolicy Bypass -File scripts/build_portable.ps1` | Собрать единственное актуальное приложение и ZIP с вымышленными данными. Существующая portable-база сохраняется. |
| `.\.venv\Scripts\python.exe scripts/verify_portable.py` | Распаковать ZIP в `artifacts/checks/portable`, проверить запуск, сохранение, экспорт и восстановление. Результат — `portable-verification.json` в той же папке. |
| `.\.venv\Scripts\python.exe scripts/check_test_modules.py artifacts/checks/tests-isolated` | Все модули из `tests/` в отдельных процессах с таймаутом 180 секунд на модуль (`--timeout`), XML, логами и `summary.json`. |
| `.\.venv\Scripts\python.exe -m scripts.check_ui_lifecycle artifacts/checks/ui-lifecycle` | 200 циклов переходов и обновлений в настоящем цикле Qt на временной вымышленной базе; замеры объектов, памяти/дескрипторов при наличии psutil, снимки и `summary.json`. По умолчанию offscreen; перед запуском не задавать `QT_QPA_PLATFORM=windows`, если не нужны видимые переходы. |
| `.\.venv\Scripts\python.exe -m scripts.check_performance artifacts/checks/performance/demo` | Замеры CPU, памяти, диска, потоков, задержек интерфейса и SQL на временной вымышленной базе. `psutil` устанавливается из requirements-dev.txt. |
| `.\.venv\Scripts\python.exe scripts/check_dependencies.py artifacts/checks/dependencies.json` | Сверка установленных зависимостей приложения и сборки с опубликованными уязвимостями PyPI. Требует интернет; отправляются только публичные имена и версии пакетов. |
| `.\.venv\Scripts\python.exe scripts/package_portable.py` | Только упаковать подготовленный `build/portable-output`. Обычно вызывается сборкой. |
| `.\.venv\Scripts\python.exe -m scripts.create_execution_seed --output artifacts/demo/tenders.db` | Создать вымышленную базу для отдельной проверки. |
| `.\.venv\Scripts\python.exe -m scripts.ai_benchmark` | Подготовить план сравнения моделей и HTML-отчёт без API-запросов. `--live` включает бесплатный прогон; команды и правила — `docs/Тестовый_стенд_моделей.md`. |
| `.\.venv\Scripts\python.exe -m scripts.capture_visual_review artifacts/checks/visual` | Снять интерфейс на вымышленных данных. `--stress` добавляет длинные тексты и много позиций. |
| `.\.venv\Scripts\python.exe -m scripts.import_execution_excel --help` | Разовый импорт исходного Excel в **новую** базу. Источники хранить в `data/source`, результат — в отдельной папке `artifacts`. |
| `.\.venv\Scripts\python.exe -m scripts.archive_execution_import --help` | Разовый перенос записей в архив по исходному листу Excel. Меняет выбранную базу, требует закрытого приложения. |

`verify_execution_portable.py`, флаги сборки `-ChatPreview` / `-ExecutionPreview` и флаги Python `--chat-preview` / `--execution-preview` оставлены для совместимости. Они используют ту же текущую сборку. Отдельные старые версии не создаются.

Для проверки ресурсов на реальных объёмах:

```powershell
# Только чтение исходной БД через SQLite backup; все изменения — во временной копии.
.\.venv\Scripts\python.exe -m scripts.check_performance artifacts/checks/performance/work --source-db artifacts/work-data-20261026/tenders.db --idle-seconds 600 --active-seconds 1800 --document-kit
# Вымышленные значения с числом записей и распределением статусов рабочей БД, объём ×10.
.\.venv\Scripts\python.exe -m scripts.check_performance artifacts/checks/performance/volume10 --match-volume artifacts/work-data-20261026/tenders.db --scale 10 --document-kit
# Отдельный прогон профилирования: его длительности не сравнивать с обычными замерами.
.\.venv\Scripts\python.exe -m scripts.check_performance artifacts/checks/performance/profile --profile
.\.venv\Scripts\python.exe -m scripts.check_ui_lifecycle artifacts/checks/performance/lifecycle --cycles 1000 --timeout 600
```

По умолчанию используется offscreen. Для отдельной видимой пробы в Windows задайте `$env:QT_QPA_PLATFORM='windows'` в текущем PowerShell. Измерения включают накладные расходы стенда: таймер задержек каждые 50 мс, выборку ресурсов раз в секунду, диагностический файл раз в минуту. CPU нормирован на все логические ядра компьютера. `startup_ms` — открытие БД и построение окна; при `--match-volume` / `--scale` туда также входит подготовка увеличенного набора. `module_init_ms` — загрузка модулей и инициализация Qt. Счётчик `qobjects` использует уже существующие Python-обёртки через shiboken6, чтобы обход внутренних QObject не менял измеряемую память; основание подсчёта указано в `object_metric`. При сравнении скорости не запускайте одновременно другие активные проверки. В отчётах нет текстов тендеров; временные БД и созданные Excel-бланки удаляются после завершения. `completed` означает завершение сценария; выполнение ориентиров производительности отражается отдельно в `acceptance` и `stability`. Если фаза простоя не выполнялась (`--idle-seconds 0`), её критерий имеет значение null.

Рабочие данные находятся в `artifacts/execution-preview-data`, portable-данные — в `dist/TenderAssistantExecutionPreview/data`. Эти каталоги и `artifacts/legacy-releases` нельзя очищать как временные файлы.


Снимки общего поиска, чата, реестра исполнения и карточек: `.\.venv\Scripts\python.exe scripts/capture_search_review.py artifacts/checks/search-review`. Используются временная вымышленная база и новый каталог результата. Для разных масштабов задавайте `QT_SCALE_FACTOR` равным `1`, `1.25` или `1.5` и отдельный каталог для каждого запуска; для фоновой проверки — `QT_QPA_PLATFORM=offscreen`. Снимки имеют логические размеры 1152×768 и 1600×900.
