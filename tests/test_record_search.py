"""Business document numbers are independent of SQLite identifiers."""
import json
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from tender_app.agent_chat import AgentPage
from tender_app.ai_engine import Turn
from tender_app.ai_tools import Tools, payload
from tender_app.execution_models import Position, Specification
from tender_app.global_search import search_records
from tender_app.models import Tender


@pytest.fixture
def records():
    tender = Tender(id=80, number='004392212', customer='Демо-завод', title='Ролик')
    correct = Specification(id=13, tender_id=80, number='9 от 23.04.2026', archived=True,
        positions=[Position(id=37, name='Ролик Р-1', supplier_document='ДЕМО-TC прил.234 от 14/05/2026')])
    wrong = Specification(id=9, tender_id=80, number='8 от 23/03/2026',
        positions=[Position(id=25, name='Вал', supplier_document='Прил.1234 от 23.04.2026')])
    return [tender], [wrong, correct]


@pytest.mark.parametrize('query', ['приложение 234', 'прил.234', 'ПРИЛ. № 234', 'прил. 234'])
def test_application_aliases_agree_across_all_searches(records, query):
    tenders, specs = records
    found = search_records(*records, query)
    assert [(r.spec_id, r.position_id) for r in found] == [(13, 37)]
    result = json.loads(Tools(*records, 'Найди ' + query).read('search', {'query': query}))
    assert result['total'] == result['shown'] == 1
    assert result['results'][0]['spec_id'] == 13
    assert result['results'][0]['id'] == 37
    repository = SimpleNamespace(all=lambda: tenders, specifications=lambda: specs)
    reply, links = AgentPage.local_reply(SimpleNamespace(repository=repository), 'Найди ' + query)
    assert len(links) == 1 and links[0]['id'] == 13 and links[0]['position_id'] == 37
    assert '234' in reply and '9 от 23.04.2026' in reply


def test_specification_business_number_and_duplicates(records):
    found = search_records(*records, 'спецификация 9')
    assert [r.spec_id for r in found] == [13]
    records[1].append(replace(records[1][-1], id=44))
    assert [r.spec_id for r in search_records(*records, 'спецификация № 9')] == [13, 44]
    assert not search_records(*records, 'приложение 23')  # Date is not a document number.
    assert not search_records(*records, 'приложение 2364')
    assert search_records(*records, '234')[0].position_id == 37


def test_ai_search_keeps_contacts_private(records):
    records[0][0].contacts = 'Секретный-контакт'
    tools = Tools(*records, 'Секретный-контакт')
    assert json.loads(tools.read('search', {'query': 'Секретный-контакт'}))['total'] == 0
    tools.allow_contacts = True
    assert json.loads(tools.read('search', {'query': 'Секретный-контакт'}))['total'] == 1


def test_multiple_documents_paging_and_long_snippet(records):
    document = 'ДЕМО ' * 80 + 'прил.234 от 14/05/2026; Приложение 66'
    records[1][-1].positions[0].supplier_document = document
    records[1].extend(replace(records[1][-1], id=100+i) for i in range(18))
    tools = Tools(*records, 'приложение 234')
    first = json.loads(tools.read('search', {'query': 'приложение 234'}))
    second = json.loads(tools.read('search', {'query': 'приложение 234', 'offset': 16}))
    assert first['total'] == second['total'] == 19
    assert first['shown'] == 16 and second['shown'] == 3
    assert first['next_offset'] == 16 and second['next_offset'] is None
    assert 'прил.234' in first['results'][0]['snippet']
    assert len(search_records(*records, 'приложение 66')) == 19


def test_result_html_escapes_saved_content(records):
    from tender_app.search_presentation import result_html
    records[0][0].customer = '<img src="https://invalid.example/">'
    html = result_html(search_records(*records, 'приложение 234')[0], 'приложение 234')
    assert '<img ' not in html and '&lt;img' in html
    assert '<b><u>234</u></b>' in html


def test_chat_preserves_exact_document_rank_when_results_are_limited(records):
    tenders, specs = records
    tenders.extend(Tender(id=200+i, number=f'ДЕМО-234-{i}', customer='Демо') for i in range(25))
    repository = SimpleNamespace(all=lambda: tenders, specifications=lambda: specs)
    _, links = AgentPage.local_reply(SimpleNamespace(repository=repository), 'Найди 234')
    assert links[0]['position_id'] == 37
    assert sum(link['kind'] == 'tender' for link in links) == 10


class Client:
    model = 'fake'

    def __init__(self, actions):
        self.actions = iter(actions)
        self.cancel = Event()
        self.messages = []

    def begin_turn(self):
        pass

    def connect(self):
        pass

    def complete(self, messages):
        self.messages = list(messages)
        return payload(next(self.actions))


def test_agent_rejects_guessed_id_and_reads_application_field(records):
    question = 'Что по приложению 234?'
    client = Client([
        dict(kind='read', tool='card', args=dict(kind='spec', id=9)),
        dict(kind='read', tool='search', args=dict(query='234')),
        dict(kind='answer', text='Данных нет'),
        dict(kind='read', tool='card', args=dict(kind='position', id=37, spec_id=13, fields=['supplier_document'])),
        dict(kind='answer', text='Приложение 234: спецификация № 9 от 23.04.2026'),
    ])
    tools = Tools(*records, question, context={'kind': 'spec', 'id': 9})
    result = Turn(client, tools).run(question)
    assert 'спецификация № 9' in result['text']
    assert len(result['links']) == 1 and 'match' in result['links'][0]
    assert any('Операция отклонена' in m['content'] for m in client.messages)
    assert any('supplier_document' in m['content'] and 'Ответ отклонён' in m['content'] for m in client.messages)


def test_agent_cannot_read_unverified_existing_id(records):
    client = Client([
        dict(kind='read', tool='card', args=dict(kind='spec', id=9)),
        dict(kind='answer', text='Уточните номер'),
    ])
    Turn(client, Tools(*records, 'Прочитай карточку')).run('Прочитай карточку')
    assert any('Операция отклонена' in m['content'] for m in client.messages)
    assert not any('8 от 23/03/2026' in m['content'] for m in client.messages)
