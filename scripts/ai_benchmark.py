"""Repeatable, free-only agent benchmark. Run: python -m scripts.ai_benchmark --help."""
import argparse
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import statistics
import time
from unittest.mock import patch
from uuid import uuid4

from scripts.ai_benchmark_cases import CASES, SUITE_VERSION, GRADE_VERSION, ObservedTools, fixture, grade
from tender_app.ai_client import ModelClient, _zero_prices, strict_json
from tender_app.ai_config import AIError, Profile, scrub
from tender_app.ai_engine import Turn
from tender_app.ai_models import ModelSettings
from tender_app.ai_usage import UsageLog, usage_summary
from tender_app.models import now_moscow
from tender_app.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
TEST_CLOCK = datetime(2026, 10, 24, 12, 0)
DEFAULT_MODELS = ('nvidia/nemotron-3.5-lightning:free', 'nvidia/nemotron-3.5-super-vl:free',
                  'nvidia/nemotron-3-ultra-550b-a55b:free')
STATUS_LABELS = dict(completed='Ответ получен', provider_error='Ошибка сервера / сети',
                     protocol_error='Сбой протокола агента', skipped='Пропущено',
                     not_run='Не запускалось', interrupted='Прервано')


class BudgetStop(AIError):
    pass


class Budget:
    def __init__(self, requests, tokens, seconds, interval=3.2):
        self.requests = requests; self.tokens = tokens; self.deadline = time.monotonic()+seconds
        self.interval = interval; self.last_request = 0; self.events = []

    def check(self):
        if len(self.events) >= self.requests:raise BudgetStop('Лимит HTTP-попыток стенда.')
        if usage_summary(self.events)['total_tokens'] >= self.tokens:raise BudgetStop('Порог токенов стенда.')
        if time.monotonic() >= self.deadline:raise BudgetStop('Лимит времени стенда.')
        if any(Decimal(e.get('reported_cost', '0')) > 0 for e in self.events):
            raise BudgetStop('API сообщил ненулевую стоимость при запрете платных маршрутов.')


class BenchmarkClient(ModelClient):
    def __init__(self, profile, budget, row, protocol, record):
        super().__init__(profile)
        self.budget = budget; self.row = row; self.protocol = protocol
        self.purpose = 'benchmark'; self.usage_sink = record

    def _request(self, path, payload=None):
        self.budget.check()
        wait = max(0, self.budget.interval-(time.monotonic()-self.budget.last_request))
        if wait:
            if wait >= self.budget.deadline-time.monotonic():raise BudgetStop('Лимит времени стенда.')
            if self.cancel.wait(wait):raise AIError('Прогон остановлен.')
        self.budget.check()
        # ModelClient counts internal HTTP retries too: never exceed the run cap.
        self.limit = min(self.limit, self.requests+self.budget.requests-len(self.budget.events))
        self.deadline = min(self.deadline, self.budget.deadline)
        self.budget.last_request = time.monotonic()
        if payload and self.protocol == 'text-json':
            payload = dict(payload); payload.pop('response_format', None)
        return super()._request(path, payload)

    def _record_usage(self, event, headers, data):
        event.update(case=self.row['case'], repeat=self.row['repeat'])
        usage = data.get('usage') if isinstance(data, dict) else None
        cost = usage.get('cost') if isinstance(usage, dict) else None
        if type(cost) in (float, int):
            number = Decimal(str(cost))
            if number.is_finite() and number >= 0:event['reported_cost'] = str(number)
        super()._record_usage(event, headers, data)

    def complete(self, messages):
        raw = super().complete(messages)
        self.row['replies'].append(scrub(raw, (self.config.api_key,)))
        try:strict_json(raw)
        except AIError:self.row['format_corrections'] += 1
        return raw


def load_profile(directory):
    settings = Settings(directory/'settings.json')
    registry = ModelSettings(directory/'ai-settings.json', proxy=settings.get('ai_proxy', ''))
    if registry.warning or settings.load_warning:raise AIError('Повреждены настройки приложения; они не изменены.')
    entry = next((e for e in registry.entries() if e['provider'] == 'openrouter' and registry.key(e)), None)
    if not entry:raise AIError('Добавьте OpenRouter и ключ в «Настройки → Модели» приложения.')
    return replace(registry.profile(entry['id']), allow_paid=False, allow_data_collection=None)


def select_cases(names):
    if names and set(names)-{c.id for c in CASES}:raise AIError('Неизвестные сценарии. См. --list.')
    return [c for c in CASES if not names or c.id in names]


def summarize(report):
    summaries = []
    for model in report['models']:
        rows = [r for r in report['results'] if r['model'] == model]
        events = [e for r in rows for e in r.get('events', [])]
        completed = [r for r in rows if r['status'] == 'completed']
        accepted = sum(r.get('grade', {}).get('passed', False) for r in rows)
        summaries.append(dict(model=model, total=len(rows), completed=len(completed), accepted=accepted,
            provider_errors=sum(r['status'] == 'provider_error' for r in rows),
            protocol_errors=sum(r['status'] == 'protocol_error' for r in rows),
            skipped=sum(r['status'] in ('not_run', 'skipped') for r in rows),
            unsafe_attempts=sum((bool(r.get('proposals')) and r['case'] != 'comment') or
                               not r.get('grade', {}).get('checks', {}).get('нет запрещённых операций', True) for r in rows),
            unverified_reads=sum(bool(r.get('unverified_reads')) for r in rows),
            usage=usage_summary(events),
            median_seconds=round(statistics.median(r['seconds'] for r in completed), 3) if completed else None,
            reported_cost=str(sum(Decimal(e.get('reported_cost', '0')) for e in events)),
            missing_cost=sum('reported_cost' not in e for e in events)))
    candidates = [s for s in summaries if s['accepted'] == s['total'] and s['total'] and not s['usage']['unknown']]
    report['summary'] = summaries
    report['candidate'] = min(candidates, key=lambda s: (s['usage']['total_tokens'], s['median_seconds']))['model'] if candidates else None


def write_report(report, directory):
    summarize(report)
    (directory/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Сравнение моделей на тестовом стенде', '',
             f"Режим: {report['mode']}; протокол: {report['protocol']}; версия сценариев: {SUITE_VERSION}.",
             'Только вымышленные данные. Рабочая база и выбранная модель не меняются.', '',
             '| Модель | Прошли автопроверку / всего | Получены ответы | Сбои: сервер / протокол | Токены | Медиана, с |',
             '|---|---:|---:|---:|---:|---:|']
    table = []
    for s in report['summary']:
        tokens = str(s['usage']['total_tokens'])+(' + неизвестно' if s['usage']['unknown'] else '')
        median = s['median_seconds'] if s['median_seconds'] is not None else '—'
        name = report.get('catalog', {}).get(s['model'], {}).get('name') or s['model']
        cells = [name, f"{s['accepted']} / {s['total']}", str(s['completed']),
                 f"{s['provider_errors']} / {s['protocol_errors']}", tokens, str(median)]
        lines.append('| '+' | '.join(cells)+' |')
        table.append('<tr>'+''.join('<td>'+escape(c)+'</td>' for c in cells)+'</tr>')
    recommendation = ('Кандидат по этому прогону: '+report['candidate'] if report['candidate'] else
                      'Победитель не выбран: ни одна модель не прошла все выбранные проверки с полным учётом токенов.')
    notes = [recommendation,
             '«Получены ответы» означает завершённый протокол; это ещё не правильные факты.',
             'Ошибки сервера и пропуски не доказывают слабый интеллект модели; они показаны отдельно в деталях.',
             'Автопроверка проверяет заданные поля и источники ID, не все возможные утверждения свободного текста. Ошибки разметки требуют просмотра ответа.',
             'Рейтинг относится только к этим сценариям и настройкам. Для выбора модели повторите полный прогон несколько раз.',
             'Время включает ограничение частоты запросов и повторы. Кэш и reasoning уже входят в токены API.',
             'Порог токенов проверяется между обращениями; один ответ может превысить порог. HTTP-попытки ограничены жёстко.',
             'Бюджеты относятся к генерации. Служебное чтение каталога выполняется перед прогоном отдельно.',
             'Платные маршруты запрещены. Отсутствующие usage/стоимость не считаются нулевым измеренным расходом.',
             'text-json убирает API response_format у всех моделей. Такой результат не доказывает совместимость со штатным JSON-режимом приложения.']
    if report.get('setup_error'):notes.insert(0, 'Прогон не запущен: '+report['setup_error'])
    if report['mode']=='dry-run':notes.insert(0, 'Это план: живые запросы не выполнялись, оценки моделей пока отсутствуют.')
    processed = sum(r['status'] != 'not_run' for r in report['results'])
    if report['mode']=='dry-run':state='План без обращений к моделям'
    elif report.get('setup_error'):state='Запуск не выполнен'
    elif report.get('interrupted'):state='Прервано пользователем; результаты неполные'
    elif processed<len(report['results']):state=f"Прогон выполняется: завершено {processed} из {len(report['results'])}. Обновите страницу для новых результатов."
    else:state='Прогон завершён'
    if processed<len(report['results']) and report['mode']=='live' and not report.get('setup_error'):
        notes[0]='Итоги предварительные: выбор модели возможен после завершения прогона.'
    lines.insert(2,state)
    lines.extend(['', *notes, '', '## Сценарии и ответы'])
    details = []
    for row in report['results']:
        failures = [name for name, ok in row.get('grade', {}).get('checks', {}).items() if not ok]
        text = json.dumps(dict(status=row['status'], checks=row.get('grade'), error=row.get('error'),
                               answer=row.get('answer'), reads=row.get('reads'), proposals=row.get('proposals'),
                               unverified_reads=row.get('unverified_reads'), replies=row.get('replies')), ensure_ascii=False, indent=2)
        title = f"{row['model']} · {row['title']} · повтор {row['repeat']}"
        lines.extend(['', '### '+title, row['status'], ', '.join(failures), '```json', text, '```'])
        outcome = ('Автопроверка пройдена' if row.get('grade', {}).get('passed') else
                   'Ответ не прошёл автопроверку' if row['status']=='completed' else STATUS_LABELS[row['status']])
        details.append('<details><summary>'+escape(title)+' — '+escape(outcome)+'</summary><pre>'+escape(text)+'</pre></details>')
    (directory/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    html = ('<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<title>Сравнение моделей</title><style>body{font:16px system-ui;background:#f5f6f8;color:#17212d;'
            'max-width:1200px;margin:36px auto;padding:0 20px}table{border-collapse:collapse;background:white;'
            'width:100%;font-size:14px}td,th{padding:12px;border:1px solid #d9dfe7;text-align:left}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;font-size:13px}'
            'details{margin:12px 0}summary{cursor:pointer;padding:10px;background:#e4eaf2}li{margin:8px 0}</style>'
            '<h1>Сравнение моделей</h1><p>Режим: '+escape(report['mode'])+' · протокол: '+escape(report['protocol'])+'</p>'
            '<p><strong>'+escape(state)+'</strong></p>'
            '<table><tr><th>Модель</th><th>Прошли / всего</th><th>Получены ответы</th><th>Сбои: сервер / протокол</th><th>Токены</th><th>Медиана, с</th></tr>'
            +''.join(table)+'</table><p><strong>'+escape(notes[0])+'</strong></p>'
            '<details><summary>Как читать результаты и ограничения проверки</summary><ul>'
            +''.join('<li>'+escape(n)+'</li>' for n in notes[1:])+'</ul></details><h2>Разбор сценариев</h2>'
            +''.join(details)+'</html>')
    (directory/'report.html').write_text(html, encoding='utf-8')


def run_case(case, profile, budget, row, protocol, record, client_factory=BenchmarkClient):
    data = fixture(case); before = deepcopy(data); tools = ObservedTools(*data, case)
    client = client_factory(profile, budget, row, protocol, record)
    started = time.monotonic()
    try:
        with patch('tender_app.ai_engine.now_moscow', return_value=TEST_CLOCK), \
             patch('tender_app.agent_queries.now_moscow', return_value=TEST_CLOCK), \
             patch('tender_app.execution_models.now_moscow', return_value=TEST_CLOCK):
            result = Turn(client, tools, deepcopy(case.history)).run(case.question)
        row.update(status='completed', answer=scrub(result['text'], (profile.api_key,)),
                   grade=grade(case, result, tools, data == before))
        proposal = result.get('proposal')
        if proposal:row['proposal_preview'] = proposal.preview
    except BudgetStop as error:
        row.update(status='skipped', error=str(error))
    except AIError as error:
        failed_api = any(e['status'] != 'ok' for e in row['events'])
        row.update(status='provider_error' if failed_api else 'protocol_error', error=scrub(error, (profile.api_key,)))
    finally:
        row.update(seconds=round(time.monotonic()-started, 3), reads=tools.reads, proposals=tools.proposals,
                   unverified_reads=tools.unverified, usage=usage_summary(row['events']))
    if data != before:raise RuntimeError('Стенд изменил исходные вымышленные данные; прогон остановлен.')


def regrade_report(report):
    """Re-evaluate recorded replies locally; retain the original observations/grade."""
    if report.get('suite_version') != SUITE_VERSION:raise AIError('Другая версия сценариев; пересчёт не выполнен.')
    known = {c.id: c for c in CASES}
    for item in report['manifest']:
        case = known.get(item['case']['id'])
        expected = dict(case=asdict(case), fixture=[asdict(x) for group in fixture(case) for x in group]) if case else None
        if json.loads(json.dumps(expected, default=str)) != item:
            raise AIError('Изменились вопросы или исходные данные; пересчёт не выполнен.')
    for row in report['results']:
        if row['status'] != 'completed':continue
        case = known[row['case']]
        tools = ObservedTools(*fixture(case), case)
        result = {'text': row.get('answer', '')}
        accepted = next((p for p in reversed(row.get('proposals', [])) if p['ok']), None)
        if accepted:
            result['proposal'] = tools.propose(accepted['tool'], accepted['args'])
        tools.reads = deepcopy(row.get('reads', [])); tools.proposals = deepcopy(row.get('proposals', []))
        tools.unverified = deepcopy(row.get('unverified_reads', []))
        unchanged = row.get('grade', {}).get('checks', {}).get('данные не изменены', False)
        row.setdefault('initial_grade', deepcopy(row.get('grade')))
        row['grade'] = grade(case, result, tools, unchanged)
    report.update(grading_version=GRADE_VERSION, regraded_at=now_moscow().isoformat(),
                  grading_source_hash=sha256(Path(__file__).with_name('ai_benchmark_cases.py').read_bytes()).hexdigest())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Разрешить живые бесплатные API-запросы.')
    parser.add_argument('--list', action='store_true', help='Показать сценарии без сети.')
    parser.add_argument('--models', nargs='+', default=list(DEFAULT_MODELS))
    parser.add_argument('--cases', nargs='+', help='ID сценариев из --list; по умолчанию все.')
    parser.add_argument('--repeat', type=int, choices=range(1, 4), default=1)
    parser.add_argument('--protocol', choices=('app', 'text-json'), default='app')
    parser.add_argument('--max-requests', type=int, default=90)
    parser.add_argument('--max-tokens', type=int, default=150000)
    parser.add_argument('--max-seconds', type=int, default=900)
    parser.add_argument('--settings-dir', type=Path, default=ROOT/'artifacts/execution-preview-data')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--regrade', type=Path, help='Пересчитать сохранённый report.json без API-запросов.')
    args = parser.parse_args(argv)
    if args.regrade:
        path = args.regrade.resolve()
        if not path.is_relative_to((ROOT/'artifacts/checks').resolve()):parser.error('Отчёт должен быть внутри artifacts/checks.')
        report = json.loads(path.read_text(encoding='utf-8'))
        regrade_report(report);write_report(report, path.parent)
        print('Пересчёт без API: '+str(path.parent/'report.html'));return 0
    cases = select_cases(args.cases)
    if args.list:
        for case in cases:print(case.id+' — '+case.title)
        return 0
    if min(args.max_requests, args.max_tokens, args.max_seconds) < 1:parser.error('Бюджеты должны быть положительными.')
    models = list(dict.fromkeys(args.models))
    for model in models:
        Profile(provider='openrouter', model=model).validate()
        if not model.endswith(':free'):parser.error('Первая версия стенда допускает только модели :free.')
    directory = (args.output or ROOT/'artifacts/checks'/('ai-benchmark-'+now_moscow().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:6])).resolve()
    if not directory.is_relative_to((ROOT/'artifacts/checks').resolve()):parser.error('--output должен быть внутри artifacts/checks.')
    directory.mkdir(parents=True, exist_ok=False)
    manifest = [dict(case=asdict(c), fixture=[asdict(x) for group in fixture(c) for x in group]) for c in cases]
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, default=str)
    report = dict(suite_version=SUITE_VERSION, grading_version=GRADE_VERSION,
                  fixture_hash=sha256(encoded.encode()).hexdigest(), manifest=manifest,
                  test_clock=TEST_CLOCK.isoformat(), mode='live' if args.live else 'dry-run', protocol=args.protocol,
                  started=now_moscow().isoformat(), models=models, repeats=args.repeat,
                  budgets=dict(requests=args.max_requests, tokens=args.max_tokens, seconds=args.max_seconds), results=[])
    report['source_hashes'] = {name: sha256((ROOT/name).read_bytes()).hexdigest()
                              for name in ('tender_app/ai_engine.py', 'tender_app/ai_tools.py', 'tender_app/ai_client.py',
                                           'scripts/ai_benchmark.py', 'scripts/ai_benchmark_cases.py') if (ROOT/name).exists()}
    # JSON-safe decimal quantities in the reproducible fixture manifest.
    report['manifest'] = json.loads(encoded)
    for repeat in range(1, args.repeat+1):
        for case in cases:
            for model in models:
                report['results'].append(dict(model=model, case=case.id, title=case.title, repeat=repeat,
                    status='not_run', question=case.question, events=[], replies=[], format_corrections=0))
    write_report(report, directory)
    if not args.live:
        print('План без API-запросов: '+str(directory/'report.html'));return 0
    try:
        profile = load_profile(args.settings_dir)
        catalog = ModelClient(profile)._request('/models')
        by_id = {m['id']: m for m in catalog.get('data', []) if isinstance(m, dict) and 'id' in m}
        report['catalog'] = {m: {k: by_id[m].get(k) for k in ('name', 'pricing', 'supported_parameters', 'context_length')}
                             for m in models if m in by_id}
        if any(m in by_id and not _zero_prices(by_id[m]) for m in models):
            raise AIError('Каталог не подтвердил нулевую цену всех выбранных моделей; генерация не запускалась.')
    except (AIError, OSError) as error:
        report['setup_error'] = scrub(error)
        write_report(report, directory)
        print('Прогон не запущен. Отчёт: '+str(directory/'report.html'))
        return 2
    budget = Budget(args.max_requests, args.max_tokens, args.max_seconds)
    log = UsageLog(directory/'usage.jsonl'); disabled = set()
    lookup = {c.id: c for c in cases}
    try:
        for row in report['results']:
            key = (row['model'], row['repeat'])
            if row['model'] not in by_id or key in disabled:
                row.update(status='skipped', error='Модель отсутствует в каталоге или недоступна в этом повторе.')
                write_report(report, directory);continue
            try:budget.check()
            except BudgetStop as error:
                row.update(status='skipped', error=str(error));write_report(report, directory);continue
            def record(event):
                budget.events.append(dict(event));row['events'].append(dict(event));log.append(event)
                write_report(report, directory)
            print(f"{row['model']} · {row['case']} · {row['repeat']}", flush=True)
            run_case(lookup[row['case']], replace(profile, model=row['model']), budget, row, args.protocol, record)
            if row['status'] == 'provider_error':disabled.add(key)
            write_report(report, directory)
            print('  '+row['status']+' · '+('прошёл' if row.get('grade', {}).get('passed') else 'см. отчёт'), flush=True)
    except KeyboardInterrupt:
        report['interrupted'] = True
        row.update(status='interrupted', error='Прервано пользователем.')
    finally:
        report['log_warning'] = log.warning
        write_report(report, directory)
    print('Отчёт: '+str(directory/'report.html'))
    return 0


if __name__ == '__main__':
    try:raise SystemExit(main())
    except (AIError, OSError, ValueError) as error:
        print('Стенд остановлен: '+scrub(error));raise SystemExit(2)
