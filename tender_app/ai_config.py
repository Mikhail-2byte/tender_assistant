"""AI profiles without secrets; Windows Credential Manager or explicit session-only keys."""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field, asdict
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from urllib.parse import urlsplit

class AIError(ValueError):
    """Only static, safe-to-display diagnostics."""

TOKEN = re.compile(r'(?:sk-or-[A-Za-z0-9_-]+|gsk_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{12,})')
def scrub(text, secrets=()):
    text=str(text)
    for secret in secrets:
        if secret:text=text.replace(secret,'[секрет скрыт]')
    text=TOKEN.sub('[секрет скрыт]',text)
    return ''.join(c for c in text if c in '\n\t' or (ord(c)>=32 and not 127<=ord(c)<=159))

def contains_secret(text, secrets=()):
    return bool(TOKEN.search(text) or any(k and k in text for k in secrets))

@dataclass(frozen=True)
class Profile:
    provider: str = 'openrouter'
    model: str = ''
    proxy: str = field(default='',repr=False)
    timeout: int = 30
    max_tokens: int = 2048
    allow_paid: bool = False
    allow_data_collection: bool | None = False
    allow_contacts: bool = False
    api_key: str = field(default='',repr=False)
    @property
    def base_url(self):
        return {'openrouter':'https://openrouter.ai/api/v1','groq':'https://api.groq.com/openai/v1'}[self.provider]
    def validate(self):
        if self.provider not in ('openrouter','groq'):raise AIError('Выберите OpenRouter или Groq.')
        if self.model and not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}',self.model):raise AIError('Некорректный идентификатор модели.')
        if contains_secret(self.model) or contains_secret(self.proxy) or (self.api_key and (self.api_key in self.model or self.api_key in self.proxy)):raise AIError('Похоже, секрет введён вне поля ключа. Профиль не сохранён.')
        if type(self.timeout) is not int or not 5<=self.timeout<=120:raise AIError('Таймаут: от 5 до 120 секунд.')
        if type(self.max_tokens) is not int or not 128<=self.max_tokens<=4096:raise AIError('Ответ: от 128 до 4096 токенов.')
        if (any(type(x) is not bool for x in (self.allow_paid,self.allow_contacts)) or (self.allow_data_collection is not None and type(self.allow_data_collection) is not bool)):raise AIError('Некорректные разрешения профиля.')
        if self.api_key and (len(self.api_key)<12 or any(ord(c)<33 or ord(c)>126 for c in self.api_key)):raise AIError('Некорректный формат ключа; значение скрыто.')
        if (self.api_key.startswith('gsk_') and self.provider!='groq') or (self.api_key.startswith('sk-or-') and self.provider!='openrouter'):raise AIError('Формат ключа относится к другому провайдеру. Ключ не отправлен.')
        if self.proxy:
            try:
                p=urlsplit(self.proxy)
                if p.scheme not in ('http','https') or not p.hostname or not p.port or p.path not in ('','/') or p.query or p.fragment:raise ValueError()
            except ValueError:raise AIError('Прокси: нужен HTTP(S)-адрес с портом, без пути. Значение скрыто.') from None
        return self

class WindowsSecrets:
    """Generic credentials, scoped to current Windows user; no plaintext fallback."""
    def _api(self):
        if os.name!='nt':raise AIError('Защищённое хранилище Windows недоступно. Выберите «Только на этот сеанс».')
        class FILETIME(ctypes.Structure):_fields_=[('low',wintypes.DWORD),('high',wintypes.DWORD)]
        class CREDENTIAL(ctypes.Structure):
            _fields_=[('Flags',wintypes.DWORD),('Type',wintypes.DWORD),('TargetName',wintypes.LPWSTR),('Comment',wintypes.LPWSTR),('LastWritten',FILETIME),('CredentialBlobSize',wintypes.DWORD),('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),('Persist',wintypes.DWORD),('AttributeCount',wintypes.DWORD),('Attributes',ctypes.c_void_p),('TargetAlias',wintypes.LPWSTR),('UserName',wintypes.LPWSTR)]
        api=ctypes.WinDLL('Advapi32.dll',use_last_error=True)
        api.CredWriteW.argtypes=[ctypes.POINTER(CREDENTIAL),wintypes.DWORD];api.CredWriteW.restype=wintypes.BOOL
        api.CredReadW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(CREDENTIAL))];api.CredReadW.restype=wintypes.BOOL
        api.CredDeleteW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD];api.CredDeleteW.restype=wintypes.BOOL
        api.CredFree.argtypes=[ctypes.c_void_p];api.CredFree.restype=None
        return api,CREDENTIAL
    def get(self,target):
        api,C=self._api();p=ctypes.POINTER(C)()
        if not api.CredReadW(target,1,0,ctypes.byref(p)):
            if ctypes.get_last_error()==1168:return ''
            raise AIError('Не удалось прочитать ключ из хранилища Windows.')
        try:
            return ctypes.string_at(p.contents.CredentialBlob,p.contents.CredentialBlobSize).decode('utf-8')
        except UnicodeError:raise AIError('Не удалось прочитать сохранённый ключ.') from None
        finally:api.CredFree(p)
    def set(self,target,value):
        api,C=self._api();raw=value.encode('utf-8');blob=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
        c=C();c.Type=1;c.TargetName=target;c.CredentialBlobSize=len(raw);c.CredentialBlob=ctypes.cast(blob,ctypes.POINTER(ctypes.c_ubyte));c.Persist=2;c.UserName='TenderAssistant'
        if not api.CredWriteW(ctypes.byref(c),0):raise AIError('Не удалось сохранить ключ в Windows. Можно выбрать хранение только на сеанс.')
    def delete(self,target):
        api,_=self._api()
        if not api.CredDeleteW(target,1,0) and ctypes.get_last_error()!=1168:raise AIError('Не удалось удалить ключ из Windows.')

class AISettings:
    def __init__(self,path,store=None):
        self.path=Path(path);self.store=store or WindowsSecrets();self.session={};self.warning=''
        self.data={'version':1,'installation':uuid4().hex,'profiles':{},'key_modes':{}}
        if self.path.exists():
            try:
                if self.path.stat().st_size>65536:raise ValueError()
                data=json.loads(self.path.read_text(encoding='utf-8'))
                if data.get('version')!=1 or not re.fullmatch('[a-f0-9]{32}',data.get('installation','')) or not isinstance(data.get('profiles'),dict):raise ValueError()
                if not isinstance(data.get('key_modes',{}),dict) or any(k not in ('primary','fallback') or v not in ('windows','session','none') for k,v in data.get('key_modes',{}).items()):raise ValueError()
                for name,p in data['profiles'].items():
                    if name not in ('primary','fallback') or not isinstance(p,dict) or 'api_key' in p:raise ValueError()
                    Profile(**p).validate()
                self.data=data
            except (OSError,ValueError,TypeError,AttributeError):self.warning='Настройки AI повреждены или имеют неизвестный формат. Исходный файл не изменён.'
    def target(self,name):return 'TenderAssistant/AI/'+self.data['installation']+'/'+name
    def profile(self,name,with_key=True):
        p=Profile(**self.data['profiles'].get(name,{}))
        if not with_key:return p
        key=self.session.get(name)
        if key is None and self.data.get('key_modes',{}).get(name)!='windows':key=''
        if key is None:
            try:key=self.store.get(self.target(name))
            except AIError:key=''
        return Profile(**(asdict(p)|{'api_key':key})).validate()
    def save(self,name,profile,session_only=False):
        profile.validate()
        if self.warning:raise AIError(self.warning)
        if name not in ('primary','fallback'):raise AIError('Неизвестный профиль.')
        p=asdict(profile);key=p.pop('api_key')
        # Proxy credentials are also secrets: never persist URL userinfo.
        if profile.proxy and urlsplit(profile.proxy).username is not None:raise AIError('Прокси с логином/паролем не сохраняется. Используйте прокси без учётных данных.')
        data={**self.data,'profiles':{**self.data['profiles'],name:p},'key_modes':{**self.data.get('key_modes',{}),name:('session' if session_only else 'windows' if key else 'none')}}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        staging=self.path.with_suffix('.tmp')
        old_key=None;credential_changed=False
        try:
            staging.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            if not session_only and key:
                old_key=self.store.get(self.target(name))
                self.store.set(self.target(name),key);credential_changed=True
            staging.replace(self.path)
        except (OSError,AIError):
            if credential_changed:
                try:
                    if old_key:self.store.set(self.target(name),old_key)
                    else:self.store.delete(self.target(name))
                except AIError:raise AIError('Ошибка сохранения и восстановления ключа. Проверьте этот профиль в хранилище Windows; ключ не выводится.') from None
            raise AIError('Не удалось сохранить профиль. Проверьте папку/хранилище или выберите «Только на сеанс».') from None
        finally:staging.unlink(missing_ok=True)
        self.data=data
        if session_only:self.session[name]=key
        else:self.session.pop(name,None)
    def delete_key(self,name):
        self.store.delete(self.target(name));self.session.pop(name,None)
    def secrets(self):return tuple(self.profile(n).api_key for n in ('primary','fallback'))

def import_env(path,provider):
    """Explicit import only. No eval, no shell, no mutation of process environment."""
    path=Path(path)
    if not path.is_file() or path.stat().st_size>65536:raise AIError('Нужен UTF-8 .env до 64 КиБ.')
    try:lines=path.read_text(encoding='utf-8-sig').splitlines()
    except (OSError,UnicodeError):raise AIError('Не удалось прочитать .env.') from None
    values={}
    for line in lines:
        line=line.strip()
        if not line or line.startswith('#'):continue
        if line.startswith('export '):line=line[7:].lstrip()
        name,sep,value=line.partition('=');name=name.strip();value=value.strip()
        if not sep or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',name) or name in values:raise AIError('Некорректные или повторные строки .env; значения скрыты.')
        if value.startswith(('"',"'")):
            close=value.find(value[0],1)
            if close<0 or (value[close+1:].strip() and not value[close+1:].lstrip().startswith('#')):raise AIError('Некорректные кавычки .env.')
            value=value[1:close]
        else:value=re.split(r'\s+#',value,maxsplit=1)[0].rstrip()
        values[name]=value
    names=('OPENROUTER_API_KEY','OPENROUTER_KEY') if provider=='openrouter' else ('GROQ_API_KEY',)
    keys={values[n] for n in names if values.get(n)}
    if not keys and values.get('LLM_API_KEY'):
        inferred=values.get('LLM_PROVIDER') or ('openrouter' if values['LLM_API_KEY'].startswith('sk-or-') else 'groq' if values['LLM_API_KEY'].startswith('gsk_') else '')
        if inferred!=provider:raise AIError('Провайдер общего ключа не совпадает. Выберите провайдера явно.')
        keys.add(values['LLM_API_KEY'])
    if len(keys)!=1:raise AIError('В .env нет единственного ключа выбранного провайдера.')
    key=keys.pop();Profile(provider=provider,api_key=key).validate()
    model=values.get('OPENROUTER_MODEL' if provider=='openrouter' else 'GROQ_MODEL',values.get('LLM_MODEL',''))
    Profile(provider=provider,api_key=key,model=model).validate()
    return key,model
