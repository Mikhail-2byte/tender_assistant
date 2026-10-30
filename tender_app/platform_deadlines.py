"""Only labelled submission deadlines with explicit clock timezone are authoritative."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re

from .models import MOSCOW


@dataclass(frozen=True)
class PlatformDeadline:
    raw: str = ''
    day: str | None = None
    clock: str | None = None
    zone: str | None = None
    warning: str = ''


SUBMISSION_LABEL = re.compile(
    r'(?:дата\s+(?:и\s+время\s+)?окончания\s+(?:подачи|при[её]ма)\s+(?:заявок|предложений)'
    r'|окончание\s+(?:подачи|при[её]ма)\s+(?:заявок|предложений)'
    r'|срок\s+(?:окончания\s+)?(?:подачи|при[её]ма)\s+(?:заявок|предложений)'
    r'|при[её]м\s+(?:заявок|предложений)\s+до)', re.I)


def parse_deadline(raw):
    text = re.sub(r'\s+', ' ', raw).strip()
    unknown = PlatformDeadline(raw=text, warning='Не удалось подтвердить дату, время или часовой пояс срока площадки.')
    months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
              'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
    normalized = text
    for index, month in enumerate(months, 1):
        normalized = re.sub(r'\b(\d{1,2})\s+' + month + r'\s+(\d{4})\b',
                            lambda match: f'{int(match[1]):02d}.{index:02d}.{match[2]}', normalized, flags=re.I)
    # ISO timestamps (API values are never passed here without a labelled DOM match).
    iso = re.search(r'\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?', normalized)
    try:
        if iso:
            moment = datetime.fromisoformat(iso[0].replace('Z', '+00:00'))
        else:
            numeric = re.search(r'\b(\d{1,2})[./](\d{1,2})[./](\d{4})(?:[\s,]*(?:г\.?[\s,]*)?(?:в\s*)?(\d{1,2}):(\d{2})(?::\d{2})?)?', normalized)
            if not numeric:
                return unknown
            day, month, year, hour, minute = numeric.groups()
            if hour is None:
                if re.search(r'\d{1,2}:\d{2}', text):
                    return unknown
                return PlatformDeadline(text, date(int(year), int(month), int(day)).isoformat())
            moment = datetime(int(year), int(month), int(day), int(hour), int(minute))
        if moment.tzinfo is None:
            if re.search(r'\b(?:МСК|MSK)\b|московск', text, re.I):
                zone = MOSCOW
            else:
                offset = re.search(r'(?:UTC|GMT)\s*([+-])\s*(\d{1,2})(?::(\d{2}))?', text, re.I)
                if not offset:
                    return unknown
                sign, hours, minutes = offset.groups()
                hours, minutes = int(hours), int(minutes or 0)
                if hours > 14 or minutes > 59:
                    return unknown
                zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if sign == '+' else -1)))
            moment = moment.replace(tzinfo=zone)
        moment = moment.astimezone(MOSCOW)
        return PlatformDeadline(text, moment.date().isoformat(), moment.strftime('%H:%M'), 'Europe/Moscow')
    except ValueError:
        return unknown


def labelled_deadline(sections):
    candidates = [(str(label), str(value)) for label, value in sections.items()
                  if SUBMISSION_LABEL.search(str(label))]
    if not candidates:
        return PlatformDeadline(warning='Срок подачи не найден однозначно в карточке площадки.')
    deadlines = [parse_deadline(label + ': ' + value) for label, value in candidates]
    keys = {(item.day, item.clock, item.zone) for item in deadlines}
    if len(deadlines) > 1 and (len(keys) != 1 or any(item.warning for item in deadlines)):
        return PlatformDeadline(warning='В карточке площадки указаны разные сроки подачи; нужна проверка.')
    return deadlines[0]


def sections_from_text(text):
    """Read only a date immediately following a submission label, never other dates."""
    result = {}
    for match in SUBMISSION_LABEL.finditer(text):
        tail = text[match.end():match.end() + 180]
        value = re.match(r'\s*[:—–-]?\s*((?:\d{2}[./]\d{2}[./]\d{4}|\d{4}-\d{2}-\d{2})[^\n]{0,80})', tail)
        if value:
            label = match[0]
            continuation = tail[value.end():].splitlines()
            extra = [line.strip() for line in continuation if line.strip()][:2]
            content = value[1]
            for line in extra:
                if re.match(r'^(?:\d{1,2}:\d{2}|\(?\s*(?:по\s+)?(?:МСК|MSK|UTC|GMT)|московск)', line, re.I):
                    content += ' ' + line
                else:
                    break
            if label in result and result[label] != content:
                result[label + ' (другое значение)'] = content
            else:
                result[label] = content
    return result
