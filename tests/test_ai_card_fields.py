"""Projection reduces outbound data without treating omitted facts as missing values."""
import json
from copy import deepcopy
from threading import Event

import pytest

from scripts.ai_benchmark_cases import CASES, fixture
from tender_app.ai_config import AIError
from tender_app.ai_tools import Tools, payload
from tender_app.ai_engine import Turn


def tools():
    tenders, specs = fixture(CASES[0])
    specs[0].risk = 'PRIVATE_RISK' * 200
    specs[0].positions[0].working_note = 'PRIVATE_NOTE' * 200
    return Tools(tenders, specs, 'Когда поставка?')


def test_timing_card_excludes_positions_and_unrequested_details():
    t = tools()
    raw = t.read('card', {'kind':'spec', 'id':701})
    data = json.loads(raw)
    assert data['due'] == '2026-10-27' and data['eta'] == '2026-11-03'
    assert data['tender_id'] == 1 and data['positions_total'] == 2
    assert not {'positions','risk','contract','title'} & data.keys()
    assert 'PRIVATE_' not in raw and 'fields' in data['notice']


def test_explicit_fields_and_followup_preserve_values():
    t = tools()
    data = json.loads(t.read('card', {'kind':'spec','id':701,'fields':['due']}))
    assert set(data) == {'id','number','tender_id','due','notice'}
    followup = json.loads(t.read('card', {'kind':'spec','id':701,'fields':['risk','positions'],
                                          'position_fields':['supplier','placed','remaining']}))
    assert followup['risk'].startswith('PRIVATE_RISK')
    assert followup['positions_total'] == followup['positions_shown'] == 2
    assert followup['positions'][0]['supplier'] == 'СтендМех'
    assert followup['positions'][1]['placed'] is None
    assert 'working_note' not in followup['positions'][0]
    assert set(followup['positions'][0]) == {'id','name','supplier','placed','remaining'}


def test_positions_default_to_identity_and_single_position_can_be_read():
    t = tools()
    data = json.loads(t.read('card', {'kind':'spec','id':701,'fields':['positions']}))
    assert set(data['positions'][0]) == {'id','name'}
    data = json.loads(t.read('card', {'kind':'position','id':811,'spec_id':701,
                                    'fields':['supplier','ordered','due']}))
    assert data['spec_id'] == 701 and data['supplier'] == 'СтендМех'
    assert data['ordered'] == '17' and data['due'] is None


@pytest.mark.parametrize('args', [
    {'fields':[]}, {'fields':'due'}, {'fields':['contacts']}, {'fields':[{}]},
    {'fields':['positions'],'position_fields':['contacts']},
    {'fields':['due'],'position_fields':['supplier']},
    {'fields':['positions'],'position_fields':[]},
])
def test_invalid_projection_cannot_bypass_allowlists(args):
    with pytest.raises(AIError):tools().read('card', {'kind':'spec','id':701,**args})


def test_history_does_not_accept_projection_arguments():
    with pytest.raises(AIError):tools().read('history', {'kind':'spec','id':701,'fields':['due']})


def test_projected_timing_is_smaller_than_full_card_with_long_notes():
    t = tools()
    short = t.read('card', {'kind':'spec','id':701,'fields':['due','revised','eta','stage']})
    full = t.read('card', {'kind':'spec','id':701,'fields':['due','revised','eta','stage','risk','positions'],
                         'position_fields':['supplier','working_note']})
    assert len(short) < len(full) / 5
    assert 'PRIVATE_' not in short


def test_turn_sends_only_requested_card_fields_to_model():
    class Client:
        model = 'fake/model'
        cancel = Event()
        def begin_turn(self):pass
        def connect(self):pass
        def complete(self,messages):
            self.seen = deepcopy(messages)
            if not any('untrusted_tool_data' in m['content'] for m in messages):
                return payload({'kind':'read','tool':'card','args':
                                {'kind':'spec','id':701,'fields':['due','eta']}})
            return payload({'kind':'answer','text':'Срок прочитан'})
    client = Client()
    selected = tools()
    selected.context = {'kind': 'spec', 'id': 701}
    Turn(client,selected).run('Когда поставка?')
    results = [json.loads(m['content'])['untrusted_tool_data'] for m in client.seen
               if m['role']=='user' and 'untrusted_tool_data' in m['content']]
    assert len(results) == 1
    assert json.loads(results[0])['due'] == '2026-10-27'
    assert 'PRIVATE_' not in results[0] and 'positions' not in results[0]
