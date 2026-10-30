"""Unified agent queries, using fictional data and mocked model replies only."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from threading import Event
import json

import pytest

from tender_app.agent_queries import submission_day, submissions
from tender_app.agent_chat import AgentPage
from tender_app.ai_engine import Turn
from tender_app.ai_tools import Tools, apply_proposal, snapshot
from tender_app.execution_models import Specification
from tender_app.execution_repository import ExecutionRepository
from tender_app.execution_seed import create_demo_database
from tender_app.models import Tender


@pytest.fixture
def records(monkeypatch):
    now=lambda:datetime(2026,10,27,23,30)
    monkeypatch.setattr('tender_app.agent_queries.now_moscow',now)
    tenders=[Tender(id=1,number='ТЕСТ-1',customer='Акрон ПАО',status='Податься',
                    deadline_date='2026-10-27',deadline_time='14:00'),
             Tender(id=2,number='ТЕСТ-2',customer='Акрон ПАО',status='Переторжка',
                    deadline_date='2026-10-28',deadline_time='15:00'),
             Tender(id=3,number='ТЕСТ-3',customer='Учебный завод',status='Подался',
                    deadline_date='2026-10-28'),
             Tender(id=4,number='ТЕСТ-4',customer='Учебный завод',status='Податься',
                    deadline_date='2026-10-28')]
    specs=[Specification(id=i,number=f'СП-{i}',tender_id=1,
                         archived=i==13,completed=i==12) for i in range(1,14)]
    specs.append(Specification(id=14,number='СП-14',tender_id=3))
    return tenders,specs


@pytest.mark.parametrize('text,day',[
    ('что нужно податься сегодня ?', 'сегодня'),
    ('Какие тендера мне нужно подать на завтра?', 'завтра'),
    ('Покажи подачи послезавтра', 'послезавтра'),
    ('Подачи сегодня', 'сегодня'),
    ('По ТЕСТ-1 запиши в заметки: необходимо податься завтра', None),
    ('Какие тендеры подать завтра по Акрону?', None),
    ('Сколько спецификаций по акрону сегодня?', None),
    ('Подачи сегодня и завтра', None),
    ('Когда подать тендер ТЕСТ-1 завтра?', None),
])
def test_submission_intent(text,day):
    assert submission_day(text)==day


def test_submission_dates_statuses_and_missing_time(records):
    tenders,_=records
    target,items=submissions(tenders,'сегодня')
    assert target=='2026-10-27' and [t.id for t in items]==[1]
    target,items=submissions(tenders,'завтра')
    assert target=='2026-10-28' and [t.id for t in items]==[2,4]
    assert submissions(tenders,'послезавтра')[1]==[]


def test_tools_separate_tenders_and_specifications_and_count_all_pages(records):
    before=deepcopy(records);tools=Tools(*records,'Сколько выигранных спецификаций по акрону?')
    result=json.loads(tools.read('specifications',{'customer':'Акрон'}))
    assert result['total']==13 and result['shown']==10 and result['next_offset']==10
    assert result['counts']=={'active':11,'completed':1,'archived':1}
    result2=json.loads(tools.read('specifications',{'customer':'Акрон','offset':10}))
    assert result2['shown']==3 and result2['next_offset'] is None
    assert {r['id'] for r in result['results']}.isdisjoint(r['id'] for r in result2['results'])
    assert json.loads(tools.read('specifications',{'customer':'Акрон','scope':'active'}))['total']==11
    result=json.loads(tools.read('tenders',{'day':'завтра'}))
    assert result['total']==2
    assert result['results'][0]['status']=='Переторжка'
    assert '15:00' in result['results'][0]['deadline']
    assert 'время не указано' in result['results'][1]['deadline']
    assert all(link['kind']=='tender' for link in tools.links)
    assert json.loads(tools.read('tenders',{'day':'2026-10-28','status':'Все'}))['total']==3
    assert json.loads(tools.read('tenders',{'day':'завтра','customer':'Акрон'}))['total']==1
    assert json.loads(tools.read('specifications',{'customer':'Нет такого'}))['total']==0
    assert records==before


def test_overview_has_no_unrelated_record_buttons(records):
    tools=Tools(*records,'Сводка')
    tools.read('overview',{})
    assert tools.links==[]


def test_customer_directory_is_paginated(records):
    tenders,specs=records
    tenders=[replace(tenders[0],id=i,customer=f'Заказчик {i:03}') for i in range(1,42)]
    tools=Tools(tenders,[],'Кто заказчик?')
    first=json.loads(tools.read('customers',{}))
    last=json.loads(tools.read('customers',{'offset':first['next_offset']}))
    assert first['total']==41 and len(first['customers'])==30
    assert len(last['customers'])==11 and last['next_offset'] is None


@pytest.mark.parametrize('tool,args',[
    ('tenders',{'status':'bad'}),('tenders',{'day':'bad'}),
    ('specifications',{'scope':'bad'}),('specifications',{'offset':True}),
    ('customers',{'offset':-1}),('tenders',{'customer':None}),
])
def test_invalid_query_arguments_rejected(records,tool,args):
    from tender_app.ai_config import AIError
    with pytest.raises((AIError,ValueError)):
        Tools(*records,'Вопрос').read(tool,args)


def test_natural_submission_question_bypasses_model(app,tmp_path,records):
    class Repository:
        def all(self):return records[0]
        def specifications(self):return records[1]
    page=AgentPage(Repository(),tmp_path/'chat.json')
    called=[];page.ai_submit=called.append
    try:
        page.chat.input.setPlainText('Какие тендера мне нужно подать на завтра?')
        page.send_message()
        reply=page.history.active['messages'][-1]
        assert not called and 'ТЕСТ-2' in reply['text'] and 'ТЕСТ-4' in reply['text']
        assert 'ТЕСТ-3' not in reply['text'] and '28.10.2026' in reply['text']
        assert all(link['kind']=='tender' for link in reply['links'])
    finally:
        page.close()


class Client:
    model='mock';cancel=Event()
    def __init__(self,actions):self.actions=iter(actions);self.seen=[]
    def begin_turn(self):pass
    def connect(self):pass
    def complete(self,messages):
        self.seen.append(deepcopy(messages))
        return json.dumps(next(self.actions),ensure_ascii=False)


def test_model_can_query_customer_counts_without_selected_card(records):
    client=Client([{'kind':'read','tool':'customers','args':{}},
                   {'kind':'read','tool':'specifications','args':{'customer':'Акрон'}},
                   {'kind':'answer','text':'Всего 13 спецификаций: 11 активных, 1 завершённая, 1 архивная.'}])
    result=Turn(client,Tools(*records,'Сколько спецификаций по окрону?')).run('Сколько спецификаций по окрону?')
    assert '13' in result['text'] and len(result['links'])==10
    data=json.loads(json.loads(client.seen[-1][-1]['content'])['untrusted_tool_data'])
    assert data['total']==13


@pytest.mark.parametrize('question,tool,args',[
    ('Какие тендеры подать завтра?','tenders',{'day':'завтра'}),
    ('Сколько спецификаций по Акрону?','specifications',{'customer':'Акрон'}),
])
def test_engine_rejects_answer_without_relevant_query(records,question,tool,args):
    client=Client([{'kind':'answer','text':'Выдуманный ответ'},
                   {'kind':'read','tool':tool,'args':args},
                   {'kind':'answer','text':'Ответ после чтения'}])
    result=Turn(client,Tools(*records,question)).run(question)
    assert result['text']=='Ответ после чтения'
    assert 'Ответ отклонён' in client.seen[1][-1]['content']


def test_engine_rejects_guessed_proposal_id(records):
    question='По тендеру ТЕСТ-1 запиши в заметки: запросить информацию'
    proposal={'kind':'propose','tool':'comment','args':{'kind':'tender','id':1,'text':'запросить информацию'}}
    client=Client([proposal,{'kind':'read','tool':'search','args':{'query':'ТЕСТ-1'}},proposal])
    result=Turn(client,Tools(*records,question)).run(question)
    assert len(client.seen)==3 and result['proposal'].identifier==1


def test_note_proposal_appends_literal_text_with_timestamp_only_after_confirmation(tmp_path,monkeypatch):
    create_demo_database(tmp_path/'test.db')
    repo=ExecutionRepository(tmp_path/'test.db')
    try:
        tender=repo.all()[0];before=snapshot(repo)
        note='необходимо запросить у Яна информацию'
        question=f'По тендеру {tender.number} запиши в заметки: {note}'
        client=Client([{'kind':'read','tool':'search','args':{'query':tender.number}},
                       {'kind':'propose','tool':'comment','args':{'kind':'tender','id':tender.id,'text':note}}])
        result=Turn(client,Tools(*snapshot(repo),question)).run(question)
        assert snapshot(repo)==before and result['proposal'].text==note
        monkeypatch.setattr('tender_app.ai_tools.now_moscow',lambda:datetime(2026,10,27,11,15))
        apply_proposal(repo,result['proposal'])
        saved=next(t for t in repo.all() if t.id==tender.id)
        assert saved.comment==tender.comment+'\n\n2026-10-27T11:15:00+03:00\n'+note
        assert snapshot(repo)[1]==before[1]
    finally:
        repo.close()
