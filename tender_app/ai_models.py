"""User-named provider/model connections; V5 migration never copies plaintext secrets."""
import json
from pathlib import Path
import re
from uuid import uuid4, uuid5, NAMESPACE_URL
from .ai_config import Profile, AIError, AISettings, WindowsSecrets

PROVIDERS={'openrouter':'OpenRouter','groq':'Groq'}

class KeyStorageError(AIError):
    pass

class ModelSettings:
    def __init__(self,path,store=None,proxy=''):
        self.proxy=Profile(proxy=proxy).validate().proxy
        self.path=Path(path);self.store=store or WindowsSecrets();self.session={};self.warning=''
        self.data={'version':2,'installation':uuid4().hex,'models':[],'active':None}
        if not self.path.exists():return
        try:
            if self.path.stat().st_size>65536:raise ValueError()
            value=json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('version')==1:
                self._migrate(value)
            else:
                self._validate(value);self.data=value
        except (OSError,ValueError,TypeError,AttributeError,KeyError):
            self.warning='Настройки моделей повреждены или имеют неизвестный формат. Исходный файл не изменён.'
    def _migrate(self,value):
        old=AISettings(self.path,self.store)
        if old.warning:raise ValueError()
        entries=[]
        for name in ('primary','fallback'):
            if name not in value['profiles']:continue
            p=old.profile(name,False)
            ident=uuid5(NAMESPACE_URL,value['installation']+'/'+name).hex
            entries.append({'id':ident,'provider':p.provider,'model':p.model,
                'credential':old.target(name),'key_mode':value.get('key_modes',{}).get(name,'none')})
        migrated={'version':2,'installation':value['installation'],'models':entries,'active':entries[0]['id'] if entries else None}
        self._validate(migrated);self.data=migrated
    def _validate(self,value):
        if set(value)!={'version','installation','models','active'} or value['version']!=2:raise ValueError()
        if not re.fullmatch('[a-f0-9]{32}',value['installation']):raise ValueError()
        if not isinstance(value['models'],list) or len(value['models'])>50:raise ValueError()
        ids=set()
        for e in value['models']:
            if set(e)!={'id','provider','model','credential','key_mode'}:raise ValueError()
            if not re.fullmatch('[a-f0-9]{32}',e['id']) or e['id'] in ids:raise ValueError()
            ids.add(e['id'])
            Profile(provider=e['provider'],model=e['model']).validate()
            if e['key_mode'] not in ('none','windows','session'):raise ValueError()
            prefix='TenderAssistant/AI/'+value['installation']+'/'
            if not isinstance(e['credential'],str) or not e['credential'].startswith(prefix):raise ValueError()
            suffix=e['credential'][len(prefix):]
            if suffix not in ('primary','fallback') and not re.fullmatch('[a-f0-9]{32}',suffix):raise ValueError()
        if value['active'] is not None and value['active'] not in ids:raise ValueError()
    def entries(self):return [dict(e) for e in self.data['models']]
    @property
    def active(self):return self.data['active']
    def entry(self,ident):
        return next((dict(e) for e in self.data['models'] if e['id']==ident),None)
    def key(self,e):
        if e['credential'] in self.session:return self.session[e['credential']]
        if e['key_mode']=='windows':
            try:return self.store.get(e['credential'])
            except AIError:return ''
        return ''
    def profile(self,ident=None):
        e=self.entry(ident or self.active)
        if not e:return Profile(proxy=self.proxy,allow_paid=True,allow_data_collection=None,allow_contacts=True)
        return Profile(provider=e['provider'],model=e['model'],api_key=self.key(e),proxy=self.proxy,
                       allow_paid=not(e['provider']=='openrouter' and e['model'].endswith(':free')),
                       allow_data_collection=None,allow_contacts=True).validate()
    def secrets(self):return tuple({self.key(e) for e in self.entries()}- {''})
    def _commit(self,value):
        if self.warning:raise AIError(self.warning)
        self._validate(value);self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(self.path)
        except OSError:raise AIError('Не удалось сохранить настройки. Проверьте доступ к папке.') from None
        finally:
            try:tmp.unlink(missing_ok=True)
            except OSError:pass
        self.data=value
    def select(self,ident):
        if not self.entry(ident):raise AIError('Выберите сохранённую модель.')
        self._commit({**self.data,'active':ident})
    def save_model(self,provider,model,key='',ident=None,session_only=False):
        if self.warning:raise AIError(self.warning)
        model=model.strip();key=key.strip()
        if not model:raise AIError('Укажите точный ID модели.')
        Profile(provider=provider,model=model,api_key=key).validate()
        old=self.entry(ident) if ident else None
        if ident and old is None:raise AIError('Модель не найдена.')
        # Blank field preserves a saved key only for the same provider.
        shared=old if old and old['provider']==provider else next((e for e in self.entries() if e['provider']==provider and self.key(e)),None)
        if not key and shared:key=self.key(shared)
        if not key:raise AIError('Введите API-ключ выбранного провайдера.')
        Profile(provider=provider,model=model,api_key=key).validate()
        duplicate=next((e for e in self.entries() if e['provider']==provider and e['model']==model and e['id']!=ident),None)
        if duplicate:raise AIError('Эта модель уже добавлена. Выберите её в списке.')
        if not old and len(self.data['models'])>=50:raise AIError('Достигнут лимит 50 моделей.')
        ident=ident or uuid4().hex
        target=shared['credential'] if shared else 'TenderAssistant/AI/'+self.data['installation']+'/'+uuid4().hex
        mode='session' if session_only else 'windows'
        entry={'id':ident,'provider':provider,'model':model,'credential':target,'key_mode':mode}
        entries=[entry if e['id']==ident else dict(e) for e in self.entries()]
        if not old:entries.append(entry)
        # All models sharing the same credential see replacement keys consistently.
        for e in entries:
            if e['credential']==target:e['key_mode']=mode
        value={**self.data,'models':entries,'active':self.active or ident}
        previous=None;changed=False
        try:
            if not session_only:
                try:previous=self.store.get(target);self.store.set(target,key);changed=True
                except AIError:raise KeyStorageError('Хранилище Windows недоступно. Ключ не сохранён.') from None
            self._commit(value)
        except (AIError,OSError):
            if changed:
                try:
                    if previous:self.store.set(target,previous)
                    else:self.store.delete(target)
                except AIError:raise AIError('Ошибка сохранения и восстановления ключа. Проверьте хранилище Windows; значение скрыто.') from None
            raise
        if session_only:self.session[target]=key
        else:self.session.pop(target,None)
        return ident
