"""Simplified model UI, metadata migration and secure key boundaries. No live API."""
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox,QPushButton,QLineEdit,QMessageBox
from tender_app.ai_config import AISettings,Profile,AIError
from tender_app.ai_models import ModelSettings
from tender_app.ai_client import ModelClient
from tender_app.ai_dialog import AISettingsDialog
from tests.test_ai import MemoryStore,KEY
from tests.test_ai_ui import aiwin,finish,Client


def test_free_saved_profile_cannot_route_to_paid_provider(tmp_path):
    settings=ModelSettings(tmp_path/'models.json',MemoryStore())
    ident=settings.save_model('openrouter','nvidia/nemotron-3.5-super-vl:free','sk-or-FICTIONAL_FREE_KEY_12345')
    assert settings.profile(ident).allow_paid is False
    assert ModelSettings(settings.path,settings.store).profile(ident).allow_paid is False
    paid=settings.save_model('openrouter','vendor/paid-model')
    assert settings.profile(paid).allow_paid is True


def test_connection_check_of_free_model_also_forbids_paid_routes(aiwin):
    dialog=AISettingsDialog(aiwin.ai_service,aiwin)
    dialog.edit_model(next(e['id'] for e in aiwin.ai_service.settings.entries() if e['provider']=='openrouter'))
    dialog.model.setText('nvidia/nemotron-3.5-super-vl:free')
    assert dialog.value().allow_paid is False
    dialog.changed=False;dialog.reject()


def test_installation_proxy_applies_to_profiles_and_connection_check(tmp_path, aiwin):
    proxy='http://127.0.0.1:3066'
    settings=ModelSettings(tmp_path/'proxy-models.json',MemoryStore(),proxy=proxy)
    settings.save_model('groq','fake/test-model',KEY)
    assert settings.profile().proxy==proxy
    settings.select(settings.active)
    assert ModelSettings(settings.path,settings.store,proxy=proxy).profile().proxy==proxy
    aiwin.ai_service.settings.proxy=proxy
    dialog=AISettingsDialog(aiwin.ai_service,aiwin)
    dialog.edit_model(aiwin.ai_service.settings.active)
    assert dialog.value().proxy==proxy
    dialog.changed=False;dialog.reject()

def test_migrate_v5_without_network_secret_copy_or_early_disk_write(tmp_path):
    path=tmp_path/'ai.json';store=MemoryStore();old=AISettings(path,store)
    old.save('primary',Profile('groq',model='openai/gpt-oss-120b',api_key=KEY))
    old.save('fallback',Profile('openrouter',model='vendor/example:free',api_key='sk-or-FICTIONAL_KEY_FOR_TESTS'))
    before=path.read_bytes();values=deepcopy(store.values)
    new=ModelSettings(path,store)
    assert not new.warning and len(new.entries())==2 and path.read_bytes()==before
    assert new.profile().provider=='groq' and new.profile().api_key==KEY
    assert store.values==values
    new.select(new.entries()[1]['id'])
    assert json.loads(path.read_text())['version']==2 and KEY not in path.read_text()
    assert ModelSettings(path,store).profile().provider=='openrouter'
    assert store.values==values

def test_migrate_empty_legacy_model_preserves_key_reference(tmp_path):
    path=tmp_path/'ai.json';store=MemoryStore();AISettings(path,store).save('primary',Profile('groq',api_key=KEY))
    new=ModelSettings(path,store);e=new.entries()[0]
    assert not e['model'] and new.profile().api_key==KEY
    new.save_model('groq','openai/gpt-oss-120b',ident=e['id'])
    assert new.profile().model=='openai/gpt-oss-120b' and new.profile().api_key==KEY

def test_registry_remembers_models_and_active_selection(tmp_path):
    s=ModelSettings(tmp_path/'ai.json',MemoryStore());a=s.save_model('groq','one/model',KEY);b=s.save_model('groq','another/model')
    assert s.profile(b).api_key==KEY and len(s.entries())==2
    assert s.profile().allow_paid is True and s.profile().allow_data_collection is None
    s.select(b);assert ModelSettings(s.path,s.store).active==b
    assert 'primary' not in s.path.read_text() and 'fallback' not in s.path.read_text()
    with pytest.raises(AIError):s.save_model('groq','one/model',KEY)
    with pytest.raises(AIError):s.select('not-a-model')
    assert s.active==b

def test_changing_provider_never_sends_old_key(tmp_path):
    s=ModelSettings(tmp_path/'ai.json',MemoryStore());a=s.save_model('groq','one/model',KEY)
    before=s.path.read_bytes()
    with pytest.raises(AIError):s.save_model('openrouter','vendor/model',ident=a)
    with pytest.raises(AIError):s.save_model('openrouter','vendor/model',KEY,ident=a)
    assert s.path.read_bytes()==before

def test_blank_key_preserved_and_shared_key_replaced(tmp_path):
    s=ModelSettings(tmp_path/'ai.json',MemoryStore());a=s.save_model('groq','a/model',KEY);b=s.save_model('groq','b/model')
    key='gsk_FICTIONAL_REPLACEMENT_KEY_1234';s.save_model('groq','a/model',key,a)
    assert s.profile(a).api_key==s.profile(b).api_key==key
    assert key not in s.path.read_text()
    s.save_model('groq','a/renamed',ident=a)
    assert s.profile(a).api_key==key

def test_session_key_not_resurrected_from_windows(tmp_path):
    store=MemoryStore();s=ModelSettings(tmp_path/'ai.json',store);a=s.save_model('groq','a/model',KEY)
    s.save_model('groq','a/model','gsk_FICTIONAL_SESSION_KEY_12345',a,True)
    assert s.profile().api_key.endswith('12345')
    assert ModelSettings(s.path,store).profile().api_key==''
    assert KEY in store.values.values()  # stale protected key is not silently restored

def test_credential_metadata_cannot_reference_other_installation(tmp_path):
    s=ModelSettings(tmp_path/'ai.json',MemoryStore());s.save_model('groq','a/model',KEY)
    value=json.loads(s.path.read_text());value['models'][0]['credential']='TenderAssistant/AI/other/primary'
    s.path.write_text(json.dumps(value));before=s.path.read_bytes();new=ModelSettings(s.path,s.store)
    assert new.warning
    with pytest.raises(AIError):new.save_model('groq','a/model',KEY)
    assert new.path.read_bytes()==before

def test_failed_atomic_save_restores_protected_key(tmp_path,monkeypatch):
    s=ModelSettings(tmp_path/'ai.json',MemoryStore());a=s.save_model('groq','a/model',KEY);before=s.path.read_bytes()
    monkeypatch.setattr(s,'_commit',lambda v:(_ for _ in ()).throw(AIError('test')))
    with pytest.raises(AIError):s.save_model('groq','a/model','gsk_FICTIONAL_NEW_KEY_12345',a)
    assert s.profile().api_key==KEY and s.path.read_bytes()==before

def test_selected_model_no_catalog_no_account_policy_overrides(monkeypatch):
    p=Profile('openrouter',model='vendor/selected-model',api_key='sk-or-FICTIONAL_KEY_12345',allow_paid=True,allow_data_collection=None)
    c=ModelClient(p);seen=[]
    monkeypatch.setattr(c,'_request',lambda path,payload=None:seen.append((path,payload)) or {'choices':[{'message':{'content':'{"kind":"answer","text":"ok"}'},'finish_reason':'stop'}]})
    assert c.connect()==p.model and not seen
    c.complete([{'role':'user','content':'Тест'}]);body=seen[0][1]
    assert seen[0][0]=='/chat/completions' and body['model']==p.model
    assert body['provider']=={'allow_fallbacks':False,'require_parameters':True}
    assert 'max_price' not in body['provider'] and 'data_collection' not in body['provider']

def test_only_simple_fields_no_settings_buttons_near_chat(aiwin):
    w=aiwin;d=AISettingsDialog(w.ai_service,w);d.edit_model(w.ai_service.settings.active);d.show();QTest.qWait(10)
    assert not d.findChildren(QCheckBox)
    assert len(d.findChildren(QLineEdit))==2
    texts=[b.text() for b in d.settings_pages.widget(0).findChildren(QPushButton)]
    assert 'Проверить подключение' in texts
    assert not any(any(s in text for s in ('Загрузить','Импорт','Удалить ключ','профиль','Основная','Резервная')) for text in texts)
    for panel,state in w.ai_service.sources.items():
        assert state['select'].parentWidget()==panel.composer
        assert not any(b.text() in ('Настройки AI','AI…','Основная','Резервная') for b in panel.findChildren(QPushButton))
    assert w.ai_settings_action in w.application_menu.actions()
    assert w.application_menu.actions()[0].text()=='Настройки…'
    d.changed=False;d.reject()

def test_model_selection_syncs_and_is_persisted(aiwin):
    w=aiwin;service=w.ai_service;choice=next(e for e in service.settings.entries() if e['provider']=='openrouter')
    select=service.sources[w.agent_page.chat]['select'];select.setCurrentIndex(select.findData(choice['id']))
    assert service.settings.active==choice['id']
    assert all(state['select'].currentData()==choice['id'] for state in service.sources.values())
    assert ModelSettings(service.settings.path).active==choice['id']
    assert all('Основная' not in select.itemText(i) and 'Резервная' not in select.itemText(i) for i in range(select.count()))

def test_settings_check_is_real_response_but_does_not_save(aiwin):
    w=aiwin;d=AISettingsDialog(w.ai_service,w);d.edit_model(w.ai_service.settings.active);d.show()
    before=w.ai_service.settings.path.read_bytes();d.model.setText('new/unsaved-model');d.network();finish(w)
    assert 'Подключение работает' in d.result.text() and d.changed
    assert w.ai_service.settings.path.read_bytes()==before
    d.changed=False;d.reject()

def test_provider_switch_clears_unsaved_key_and_id(aiwin):
    d=AISettingsDialog(aiwin.ai_service,aiwin);d.edit_model(aiwin.ai_service.settings.active)
    d.key.setText('gsk_FICTIONAL_UNSAVED_KEY_12345');d.provider.setCurrentIndex(d.provider.findData('openrouter'))
    assert d.key.text()=='' and d.model.text()==''
    assert d.saved['key'].startswith('sk-or-')
    d.changed=False;d.reject()


def test_long_model_name_does_not_force_helper_width(aiwin):
    w=aiwin;s=w.ai_service.settings
    ident=s.save_model('groq','vendor/'+('a'*165),KEY,session_only=True);s.select(ident);w.ai_service.refresh_states()
    w.sync_minimum_width()
    for state in w.ai_service.sources.values():assert state['select'].minimumWidth()==0
