"""Read-only registry queries shared by local commands and model tools."""
import re
from datetime import timedelta

from .models import ACTIVE, deadline_key, now_moscow


def required_query(text):
    text=text.casefold()
    if re.search(r'сколько|количеств|посчитай',text) and 'спецификац' in text:
        return 'specifications'
    if re.search(r'пода[тчв]|податься',text) and re.search(r'сегодня|завтра|срок|какие|список',text):
        if not re.search(r'запиш|добав|замет|коммент',text):
            return 'tenders'
    return None


def submission_day(text):
    words = re.findall(r"[а-яёa-z]+", text.casefold())
    days = [day for day in ("сегодня", "завтра", "послезавтра") if day in words]
    if len(days) != 1:
        return None
    # Customer filters, dates, notes and mixed requests belong to the model tools.
    vocabulary = {'что','какие','какой','каким','каких','нам','мне','у','нас','нужно','надо',
                  'необходимо','на','в','по','с','со','и','к','до','срок','сроки','сроками',
                  'статус','статусы','статусами','последними','текущими','перечисли','покажи',
                  'список','сегодня','завтра','послезавтра','тендер','тендера','тендеры','тендеров',
                  'податься','подать','подавать','подачи','подачу','подача'}
    if any(word not in vocabulary for word in words) or re.search(r'\d', text):
        return None
    if len(words) == 1 or (any(w.startswith(("пода", "подат")) for w in words)
                           and not any(w.startswith(("запиш", "добав", "замет", "коммент", "спецификац", "постав")) for w in words)):
        return days[0]
    return None


def submissions(tenders, day, now=None):
    now = now or now_moscow()
    target = (now.date() + timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[day])).isoformat()
    return target, sorted((t for t in tenders if t.status in ACTIVE and t.deadline_date == target),
                          key=lambda t: deadline_key(t, now))
