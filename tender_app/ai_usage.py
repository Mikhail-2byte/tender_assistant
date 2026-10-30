"""Local API accounting. Never stores prompts, responses, credentials or URLs."""
from collections import deque
import json
from pathlib import Path
from .models import now_moscow


def token_usage(data):
    usage = data.get('usage') if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    result = {}
    for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        value = usage.get(name)
        result[name] = value if type(value) is int and value >= 0 else None
    for group, name in (('prompt_tokens_details', 'cached_tokens'),
                        ('completion_tokens_details', 'reasoning_tokens')):
        details = usage.get(group)
        value = details.get(name) if isinstance(details, dict) else None
        result[name] = value if type(value) is int and value >= 0 else None
    return result


def usage_summary(events):
    calls = [e for e in events if e.get('endpoint') == '/chat/completions']
    totals = {name: sum(e[name] for e in calls if type(e.get(name)) is int)
              for name in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    unknown = sum(any(e.get(name) is None for name in totals) for e in calls)
    totals.update(calls=len(calls), unknown=unknown)
    return totals


def summary_text(events):
    s = usage_summary(events)
    if not s['calls']:
        return 'Обращений к модели пока нет.'
    text = (f"Обращений: {s['calls']}; токены по API: вход {s['prompt_tokens']}, "
            f"выход {s['completion_tokens']}, всего {s['total_tokens']}.")
    if s['unknown']:
        text += f" Неполные данные: {s['unknown']} обращений; это известная часть расхода."
    return text


class UsageLog:
    def __init__(self, path):
        self.path = Path(path)
        self.warning = ''

    def append(self, event):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        except OSError:
            self.warning = 'Не удалось сохранить журнал расхода AI. Учёт на диске неполный.'

    def report(self):
        totals = []; recent = deque(maxlen=200); damaged = 0
        try:
            if self.path.exists():
                with self.path.open(encoding='utf-8') as stream:
                    for line in stream:
                        try:
                            event = json.loads(line)
                            if not isinstance(event, dict):
                                raise ValueError()
                        except (ValueError, TypeError):
                            damaged += 1
                            continue
                        totals.append(event)
                        recent.append(event)
        except (OSError, UnicodeError):
            return 'Не удалось прочитать журнал расхода AI. Файл не изменён.'
        today = now_moscow().date().isoformat()
        lines = ['За сегодня (МСК):', summary_text([e for e in totals if str(e.get('time', '')).startswith(today)]),
                 '', 'За всё время с начала учёта:', summary_text(totals),
                 'Числа взяты из usage API; символы в токены не пересчитываются.',
                 'Кэшированные и reasoning-токены — части расхода, отдельно к итогу не прибавляются.',
                 'Стоимость в деньгах и остаток квоты здесь не рассчитываются.',
                 'При сетевой ошибке или отсутствии usage фактический расход неизвестен.',
                 'Журнал общий для моделей этой установки. Счётчик не является остатком квоты провайдера.']
        turns = {}
        for e in totals:
            if e.get('endpoint') == '/chat/completions':
                turns.setdefault(e.get('turn_id', '?'), []).append(e)
        lines.extend(['', 'Последние 50 вопросов / проверок подключения:'])
        for ident, events in list(turns.items())[-50:]:
            first = events[0]
            lines.append(f"{first.get('time', '?')} | {first.get('model', '?')} | "
                         f"{first.get('purpose', 'dialog')} | {ident}\n  " + summary_text(events))
        lines.extend(['', 'Последние 200 HTTP-попыток (время МСК):'])
        for e in recent:
            def shown(name):
                value = e.get(name)
                return str(value) if value is not None else '?'
            lines.append(f"{e.get('time', '?')} | {e.get('provider', '?')} / {e.get('model', '?')} | "
                         f"{e.get('purpose', 'dialog')} | вопрос {e.get('turn_id', '?')} | "
                         f"{e.get('endpoint', '?')} | HTTP {shown('http_status')} | {e.get('status', '?')}\n"
                         f"  вход {shown('prompt_tokens')}; выход {shown('completion_tokens')}; "
                         f"всего {shown('total_tokens')}; кэш {shown('cached_tokens')}; "
                         f"reasoning {shown('reasoning_tokens')}; символов {shown('input_characters')}; "
                         f"max_tokens {shown('max_tokens')}; попытка {shown('attempt')}")
            if e.get('limits'):
                lines.append('  Лимиты API: ' + json.dumps(e['limits'], ensure_ascii=False))
        if damaged:
            lines.append(f'Не прочитано строк журнала: {damaged}. Итоги могут быть неполными.')
        if self.warning:
            lines.append(self.warning)
        return '\n'.join(lines)
