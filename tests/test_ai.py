"""V5 AI boundaries. All credentials fictional; all network responses mocked."""
from copy import deepcopy
from dataclasses import replace,asdict
import io,json,time
from threading import Event
from urllib.error import HTTPError
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from tender_app.ai_config import AISettings,Profile,AIError,scrub,contains_secret,import_env
from tender_app.ai_client import ModelClient,ModelReplyError,choose_model,strict_json
from tender_app.ai_engine import Turn
from tender_app.ai_tools import Tools,snapshot,apply_proposal
from tender_app.execution_seed import create_demo_database
from tender_app.execution_repository import ExecutionRepository

KEY='gsk_FICTIONAL_TEST_KEY_123456'
class MemoryStore:
    def __init__(self):self.values={}
    def get(self,k):return self.values.get(k,'')
    def set(self,k,v):self.values[k]=v
    def delete(self,k):self.values.pop(k,None)

@pytest.fixture
def db(tmp_path):
    create_demo_database(tmp_path/'fictional.db');r=ExecutionRepository(tmp_path/'fictional.db')
    yield r;r.close()

def tools(db,question='',context=None,contacts=False):return Tools(*snapshot(db),question,context,contacts,(KEY,))

def test_keys_excluded_from_repr_and_config(tmp_path):
    store=MemoryStore();s=AISettings(tmp_path/'ai.json',store);p=Profile('groq',api_key=KEY)
    s.save('primary',p)
    assert KEY not in repr(p) and KEY not in s.path.read_text()
    assert AISettings(s.path,store).profile('primary').api_key==KEY
    s.delete_key('primary');assert not s.profile('primary').api_key

def test_session_key_not_reopened_or_old_key_reused(tmp_path):
    store=MemoryStore();s=AISettings(tmp_path/'ai.json',store)
    s.save('primary',Profile('groq',api_key=KEY))
    s.save('primary',Profile('openrouter',api_key='sk-or-FICTIONAL_NEW_KEY_123456'),True)
    assert s.profile('primary').provider=='openrouter'
    assert s.profile('primary').api_key.startswith('sk-or-')
    assert AISettings(s.path,store).profile('primary').api_key==''

def test_bad_config_preserved(tmp_path):
    p=tmp_path/'ai.json';p.write_text('{bad');s=AISettings(p,MemoryStore())
    assert s.warning
    with pytest.raises(AIError):s.save('primary',Profile())
    assert p.read_text()=='{bad'

@pytest.mark.parametrize('p',[Profile('invalid'),Profile(timeout=1),Profile(max_tokens=99999),Profile(model='bad\nmodel'),Profile(api_key='short'),Profile(proxy='http://host/no'),Profile(allow_paid='yes')])
def test_profile_validation(p):
    with pytest.raises(AIError):p.validate()

def test_proxy_password_not_persisted(tmp_path):
    s=AISettings(tmp_path/'ai.json',MemoryStore())
    with pytest.raises(AIError):s.save('primary',Profile(proxy='http://u:secret@localhost:8080'))
    assert not s.path.exists()

def test_env_explicit_import_no_environment_change(tmp_path,monkeypatch):
    p=tmp_path/'.env';p.write_text('GROQ_API_KEY="'+KEY+'"\nGROQ_MODEL=openai/gpt-oss-120b\n')
    import os
    before=dict(os.environ);assert import_env(p,'groq')==(KEY,'openai/gpt-oss-120b');assert dict(os.environ)==before
    with pytest.raises(AIError):import_env(p,'openrouter')
    p.write_text('GROQ_API_KEY=x\nGROQ_API_KEY=y')
    with pytest.raises(AIError):import_env(p,'groq')

def test_scrub_and_secret_detection():
    assert contains_secret(KEY,(KEY,));assert KEY not in scrub('hello '+KEY,(KEY,))
    assert 'sk-or-' not in scrub('sk-or-EXAMPLE_KEY_VALUE')
    assert '\x1b' not in scrub('hello\x1b[31m')

def catalog():return {'data':[{'id':'example/free:free','pricing':{'prompt':'0','completion':'0'},'architecture':{'input_modalities':['text']},'supported_parameters':['response_format']},{'id':'paid/model','pricing':{'prompt':'1','completion':'2'}}]}

def test_catalog_no_implicit_paid_fallback():
    assert choose_model(Profile(),catalog())=='example/free:free'
    with pytest.raises(AIError):choose_model(Profile(model='paid/model'),catalog())
    assert choose_model(Profile(model='paid/model',allow_paid=True),catalog())=='paid/model'
    with pytest.raises(AIError):choose_model(Profile(model='missing'),catalog())

@pytest.mark.parametrize('text',['{"a":1,"a":2}','{"a":NaN}','not json'])
def test_strict_json(text):
    with pytest.raises(AIError):strict_json(text)

def test_no_redirect_authorization():
    from tender_app.ai_client import _NoRedirect
    assert _NoRedirect().redirect_request(None,None,302,'redirect',{},'https://other.invalid') is None


@pytest.mark.parametrize('proxy', ['', 'http://localhost:8080'])
def test_transport_uses_system_proxy_unless_explicitly_configured(monkeypatch, proxy):
    import tender_app.ai_client as module
    from urllib.request import ProxyHandler
    monkeypatch.setenv('https_proxy', 'http://localhost:19000')
    captured = []
    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError('https://example.invalid', 401, 'test', {}, io.BytesIO(b''))
    def opener(*handlers):
        captured.extend(h.proxies for h in handlers if isinstance(h, ProxyHandler))
        return Opener()
    monkeypatch.setattr(module, 'build_opener', opener)
    with pytest.raises(AIError):
        ModelClient(Profile('groq', api_key=KEY, proxy=proxy)).connect()
    assert captured[0]['https'] == (proxy or 'http://localhost:19000')

def test_transport_auth_failure_not_retried_or_leaked(monkeypatch):
    import tender_app.ai_client as module
    class Opener:
        def open(self,*a,**kw):raise HTTPError('https://example.invalid',401,'private '+KEY,{},io.BytesIO(KEY.encode()))
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    c=ModelClient(Profile('groq',api_key=KEY))
    with pytest.raises(AIError) as error:c.connect()
    assert c.requests==1 and KEY not in str(error.value)

def test_transport_retries_bounded_and_cancelled(monkeypatch):
    import tender_app.ai_client as module
    class Opener:
        def open(self,*a,**kw):raise TimeoutError(KEY)
    monkeypatch.setattr(module,'build_opener',lambda *a:Opener())
    c=ModelClient(Profile('groq',api_key=KEY));c.cancel.set()
    with pytest.raises(AIError):c.connect()
    assert c.requests==0

def test_read_tools_no_contacts_by_default_and_no_writes(db):
    before=snapshot(db);t=before[0][0];s=before[1][0]
    x=tools(db,'ДЕМО-101')
    result=json.loads(x.read('search',{'query':'ДЕМО-101'}));assert result['results']
    r=json.loads(x.read('card',{'kind':'tender','id':t.id}));assert 'contacts' not in r
    assert 'contacts' not in x.read('overview',{})
    with pytest.raises(AIError):x.read('contacts',{'id':t.id})
    assert json.loads(tools(db,contacts=True).read('contacts',{'id':t.id}))['contacts']==t.contacts
    assert json.loads(x.read('card',{'kind':'spec','id':s.id,'fields':['positions']}))['positions']
    assert snapshot(db)==before

@pytest.mark.parametrize('tool,args',[('sql',{'query':'DROP TABLE tenders'}),('read_file',{'path':'/etc/passwd'}),('shell',{'command':'x'}),('card',{'kind':'tender','id':True}),('overview',{'unknown':1})])
def test_unknown_tools_arguments_rejected(db,tool,args):
    with pytest.raises(AIError):tools(db).read(tool,args)

def test_proposal_preview_confirm_once_and_audit(db):
    t=db.all()[0];x=tools(db,'Измени статус на Победа',{'kind':'tender','id':t.id})
    before=snapshot(db);p=x.propose('update',{'kind':'tender','id':t.id,'changes':{'status':'Победа'}})
    assert snapshot(db)==before and 'Ещё НЕ записано' in p.preview
    apply_proposal(db,p);assert p.state=='done'
    saved=next(ti for ti in db.all() if ti.id==t.id);assert saved.status=='Победа' and 'через агента' in saved.comment
    after=snapshot(db)
    with pytest.raises(AIError):apply_proposal(db,p)
    assert snapshot(db)==after

def test_stale_proposal_cannot_overwrite(db):
    t=db.all()[0];p=tools(db,'Статус Победа',{'kind':'tender','id':t.id}).propose('update',{'kind':'tender','id':t.id,'changes':{'status':'Победа'}})
    db.save(replace(t,contacts='Ручная правка'))
    with pytest.raises(AIError):apply_proposal(db,p)
    assert next(r for r in db.all() if r.id==t.id).contacts=='Ручная правка'
    assert p.state=='stale'

def test_cancelled_proposal_never_written(db):
    t=db.all()[0];p=tools(db,'Комментарий текст',{'kind':'tender','id':t.id}).propose('comment',{'kind':'tender','id':t.id,'text':'текст'})
    p.state='cancelled';before=snapshot(db)
    with pytest.raises(AIError):apply_proposal(db,p)
    assert snapshot(db)==before

def test_no_target_guessing_or_values_from_model(db):
    t=db.all()[0]
    with pytest.raises(AIError):tools(db,'обнови').propose('update',{'kind':'tender','id':t.id,'changes':{'status':'Победа'}})
    with pytest.raises(AIError):tools(db,'Статус В работе',{'kind':'tender','id':t.id}).propose('update',{'kind':'tender','id':t.id,'changes':{'status':'Победа'}})
    with pytest.raises(AIError):tools(db,'Произвольный',{'kind':'tender','id':t.id}).propose('update',{'kind':'tender','id':t.id,'changes':{'folder_path':'/tmp'}})

def test_ambiguous_number_requires_selection(db):
    t=db.all()[0];other=replace(db.all()[1],number=t.number);db.save(other)
    with pytest.raises(AIError):tools(db,t.number+' статус Победа').propose('update',{'kind':'tender','id':t.id,'changes':{'status':'Победа'}})

def test_spec_position_comments_and_quantities(db):
    s=next(s for s in db.specifications() if not s.archived and not s.completed);p0=s.positions[0]
    x=tools(db,'Комментарий: Проверить дату',{'kind':'spec','id':s.id})
    p=x.propose('comment',{'kind':'spec','id':s.id,'text':'Проверить дату'});apply_proposal(db,p)
    assert any(note=='Проверить дату' for _,note in next(r for r in db.specifications() if r.id==s.id).comments)
    assert db.registry_comment(('specification',s.id))=='Проверить дату'
    question=p0.name+' отгружено 0'
    x=tools(db,question,{'kind':'spec','id':s.id})
    p=x.propose('update',{'kind':'position','id':p0.id,'spec_id':s.id,'changes':{'customer_shipped':'0'}});apply_proposal(db,p)
    saved=next(r for r in db.specifications() if r.id==s.id).positions[0];assert saved.customer_shipped==0
    with pytest.raises(ValueError):tools(db,p0.name+' отгружено 999999',{'kind':'spec','id':s.id}).propose('update',{'kind':'position','id':p0.id,'spec_id':s.id,'changes':{'customer_shipped':'999999'}})

def test_archived_edits_rejected(db):
    s=next(s for s in db.specifications() if s.archived)
    with pytest.raises(AIError):tools(db,'комментарий текст',{'kind':'spec','id':s.id}).propose('comment',{'kind':'spec','id':s.id,'text':'текст'})

class FakeClient:
    def __init__(self,replies):self.replies=iter(replies);self.cancel=Event();self.model='fake/model';self.seen=[];self.connects=0
    def begin_turn(self):pass
    def connect(self):self.connects+=1
    def complete(self,messages):self.seen.append(deepcopy(messages));return next(self.replies)

def test_engine_history_tools_and_no_write(db):
    c=FakeClient([json.dumps({'kind':'read','tool':'search','args':{'query':'ДЕМО-101'}}),json.dumps({'kind':'answer','text':'Сведения по найденной записи'})])
    x=tools(db,'Найди ДЕМО-101');before=snapshot(db)
    r=Turn(c,x,[{'role':'user','text':'Ранее '+KEY}]).run('Найди ДЕМО-101')
    assert r['links'] and snapshot(db)==before
    assert KEY not in json.dumps(c.seen)
    assert any('untrusted_tool_data' in m['content'] for m in c.seen[-1])


def test_engine_supplies_selected_card_as_verified_context(db):
    spec=next(s for s in db.specifications() if not s.archived)
    context={'kind':'spec','id':spec.id}
    client=FakeClient([json.dumps({'kind':'read','tool':'card','args':context}),
                       json.dumps({'kind':'answer','text':'Карточка прочитана'})])
    result=Turn(client,tools(db,'Прочитай эту спецификацию',context)).run('Прочитай эту спецификацию')
    assert result['links'] and result['links'][0]['id']==spec.id
    assert any(m['role']=='system' and 'card(kind,id)' in m['content']
               and json.dumps(context,ensure_ascii=False) in m['content'] for m in client.seen[0])


def test_engine_explains_quoted_id_and_accepts_corrected_read(db):
    spec=next(s for s in db.specifications() if not s.archived)
    context={'kind':'spec','id':spec.id}
    client=FakeClient([
        json.dumps({'kind':'read','tool':'card','args':{'kind':'spec','id':str(spec.id)}}),
        json.dumps({'kind':'read','tool':'card','args':context}),
        json.dumps({'kind':'answer','text':'Карточка прочитана'})])
    before=snapshot(db)
    result=Turn(client,tools(db,'Прочитай эту спецификацию',context)).run('Прочитай эту спецификацию')
    error=json.loads(client.seen[1][-1]['content'])['untrusted_tool_data']
    assert 'целые JSON-числа без кавычек' in error
    assert result['links'][0]['id']==spec.id and snapshot(db)==before

def test_engine_secret_never_reaches_network(db):
    c=FakeClient([])
    with pytest.raises(AIError):Turn(c,tools(db)).run(KEY)
    assert c.connects==0

def test_engine_malformed_reply_corrects_once(db):
    c=FakeClient(['bad','bad'])
    with pytest.raises(AIError):Turn(c,tools(db)).run('Привет')
    assert len(c.seen)==2

def test_engine_cannot_confirm_or_execute_code(db):
    c=FakeClient([json.dumps({'kind':'propose','tool':'confirm','args':{}})]*6)
    before=snapshot(db)
    with pytest.raises(AIError):Turn(c,tools(db)).run('Выполни')
    assert snapshot(db)==before and len(c.seen)==6

def test_engine_api_auth_error_not_corrective_retry(db):
    class Client(FakeClient):
        def complete(self,messages):self.seen.append(messages);raise AIError('API отклонил ключ.')
    c=Client([])
    with pytest.raises(AIError):Turn(c,tools(db)).run('Привет')
    assert len(c.seen)==1

def test_engine_cancel_before_turn(db):
    c=FakeClient([]);c.cancel.set()
    with pytest.raises(AIError):Turn(c,tools(db)).run('Привет')
    assert not c.seen

def test_known_key_wrong_provider_rejected():
    with pytest.raises(AIError):Profile('openrouter',api_key=KEY).validate()
    with pytest.raises(AIError):Profile('groq',api_key='sk-or-FICTIONAL_WRONG_123456').validate()

def test_credential_rollback_on_settings_write_failure(tmp_path,monkeypatch):
    from pathlib import Path
    store=MemoryStore();s=AISettings(tmp_path/'ai.json',store);s.save('primary',Profile('groq',api_key=KEY));original=s.path.read_bytes()
    monkeypatch.setattr(Path,'replace',lambda *a:(_ for _ in ()).throw(OSError('private error')))
    with pytest.raises(AIError):s.save('primary',Profile('groq',api_key='gsk_FICTIONAL_NEW_12345678'))
    assert s.path.read_bytes()==original and store.get(s.target('primary'))==KEY

def test_secret_mistyped_into_model_or_proxy_never_saved(tmp_path):
    s=AISettings(tmp_path/'ai.json',MemoryStore())
    with pytest.raises(AIError):s.save('primary',Profile('groq',model=KEY,api_key=KEY))
    with pytest.raises(AIError):s.save('primary',Profile('groq',proxy='http://'+KEY+':8080',api_key=KEY))
    assert not s.path.exists()

def test_unsafe_catalog_identifiers_not_selected():
    c=catalog();c['data'][0]['id']='<img>:free'
    with pytest.raises(AIError):choose_model(Profile(),c)

def test_position_name_ambiguous_across_specs_requires_parent(db):
    specs=[s for s in db.specifications() if not s.archived and not s.completed]
    a,b=specs[:2];b.positions[0].name=a.positions[0].name;db.save_specification(b)
    with pytest.raises(AIError):tools(db,a.positions[0].name+' комментарий текст').propose('comment',{'kind':'position','id':a.positions[0].id,'spec_id':a.id,'text':'текст'})
    p=tools(db,a.number+' '+a.positions[0].name+' комментарий текст').propose('comment',{'kind':'position','id':a.positions[0].id,'spec_id':a.id,'text':'текст'})
    assert p.spec_id==a.id

def test_proposal_numeric_values_not_part_of_larger_number(db):
    s=next(s for s in db.specifications() if not s.archived and not s.completed);p=s.positions[0]
    with pytest.raises(AIError):tools(db,p.name+' отгружено 10',{'kind':'spec','id':s.id}).propose('update',{'kind':'position','id':p.id,'spec_id':s.id,'changes':{'customer_shipped':'0'}})

def test_tender_reference_not_partial_number(db):
    t=db.all()[0]
    with pytest.raises(AIError):tools(db,t.number+'9 статус Победа').propose('update',{'kind':'tender','id':t.id,'changes':{'status':'Победа'}})

def test_openrouter_payload_privacy_and_zero_price():
    client=ModelClient(Profile('openrouter',model='example/free:free',api_key='sk-or-FICTIONAL_PAYLOAD_12345'));seen=[]
    def request(path,payload=None):
        seen.append(payload);return {'choices':[{'finish_reason':'stop','message':{'content':'{"kind":"answer","text":"ok"}'}}]}
    client._request=request;client.complete([{'role':'user','content':'test'}])
    assert seen[0]['provider']=={'data_collection':'deny','allow_fallbacks':False,'require_parameters':True,'max_price':{'prompt':0,'completion':0,'request':0}}
    assert 'api_key' not in seen[0] and seen[0]['stream'] is False

def test_groq_structured_envelope_normalized():
    client=ModelClient(Profile('groq',model='openai/gpt-oss-120b',api_key=KEY));seen=[]
    def request(path,payload=None):
        seen.append(payload);return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'kind':'read','tool':'overview','args_json':'{}','text':None})}}]}
    client._request=request;r=json.loads(client.complete([{'role':'user','content':'test'}]))
    assert r=={'kind':'read','tool':'overview','args':{}} and seen[0]['response_format']['type']=='json_schema'
