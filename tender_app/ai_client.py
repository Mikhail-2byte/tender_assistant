"""Один синхронный OpenAI-совместимый клиент; только явный запуск, без SDK."""

from decimal import Decimal, InvalidOperation
import json
import math
import re
import time
from email.utils import parsedate_to_datetime
from threading import Event
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import (build_opener, HTTPRedirectHandler, ProxyHandler, Request)

from .ai_config import Profile as ModelConfig, AIError as ModelError
from .ai_usage import token_usage
from .models import now_moscow

MAX_RESPONSE = 8*1024*1024
RETRY_CODES = {408,429,500,502,503,504}
JSON_VALIDATION_HINT = 'Ответ модели не прошёл JSON-проверку API (HTTP 400, json_validate_failed).'


def _retry_after(headers):
    value = headers.get('Retry-After') if headers is not None else None
    if not isinstance(value, str):return None
    try:
        if re.fullmatch(r'\d+(?:\.\d+)?', value.strip()):
            seconds = float(value)
        else:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        return max(0, math.ceil(seconds)) if math.isfinite(seconds) else None
    except (ValueError, TypeError, OverflowError):return None
ACTION_SCHEMA = {
    'type':'object',
    'properties':{
        'kind':{'type':'string','enum':['answer','read','propose']},
        'tool':{'type':['string','null']},
        'args_json':{'type':['string','null'],
                     'description':'Для read/propose: JSON-строка объекта args; для answer: null.'},
        'text':{'type':['string','null']},
    },
    'required':['kind','tool','args_json','text'],
    'additionalProperties':False,
}


class ModelReplyError(ModelError):
    """Пустой/обрезанный ответ: разрешена одна корректирующая попытка в диалоге."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None  # Не передавать Authorization неизвестному redirect-хосту.


def _bad_request_hint(error):
    """Только известные коды: message/failed_generation могут содержать секреты."""
    fallback = 'API отклонил формат запроса/контекст (HTTP 400; причина не распознана).'
    hints = {
        'tool_use_failed': 'API отклонил вызов инструмента, сгенерированный моделью (HTTP 400, tool_use_failed).',
        'json_validate_failed': JSON_VALIDATION_HINT,
        'context_length_exceeded': 'API отклонил слишком большой контекст (HTTP 400, context_length_exceeded).',
    }
    try:
        raw = error.read(16385)
        if len(raw) > 16384:
            return fallback
        data = strict_json(raw.decode('utf-8'))
        detail = data.get('error') if isinstance(data,dict) else None
        return hints.get(detail.get('code'),fallback) if isinstance(detail,dict) else fallback
    except (OSError,ValueError,TypeError):
        return fallback


def strict_json(text: str):
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result
    try:
        return json.loads(text,object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError,TypeError,RecursionError) as error:
        raise ModelError("Модель/API вернули некорректный JSON; текст ответа не выводится.") from error


def _zero_prices(model):
    prices = model.get('pricing',{})
    try:
        for key in ('prompt','completion'):
            value = Decimal(prices[key])
            if not value.is_finite() or value!=0:
                return False
        request = Decimal(prices.get('request','0'))
        return request.is_finite() and request==0
    except (KeyError,TypeError,InvalidOperation,AttributeError):
        return False


def choose_model(config: ModelConfig, catalog) -> str:
    if not isinstance(catalog,dict) or not isinstance(catalog.get('data'),list):
        raise ModelError("Каталог моделей не распознан.")
    models = {m['id']:m for m in catalog['data']
              if isinstance(m,dict) and isinstance(m.get('id'),str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}',m['id']) and m.get('active',True) is not False}
    if config.model:
        if config.model not in models:
            raise ModelError("Указанная модель отсутствует в доступном каталоге; fallback отключён.")
        if config.provider=='openrouter' and not config.allow_paid and not _zero_prices(models[config.model]):
            raise ModelError("Модель не подтверждена как бесплатная. Платный fallback запрещён; явное разрешение — LLM_ALLOW_PAID=true.")
        return config.model
    if config.provider=='groq':
        # Подготовленные текстовые варианты; API каталога проверяет наличие.
        for name in ('openai/gpt-oss-120b','openai/gpt-oss-20b','llama-3.3-70b-versatile','llama-3.1-8b-instant'):
            if name in models:
                return name
        raise ModelError("Подготовленные текстовые модели Groq недоступны; задайте LLM_MODEL явно.")
    candidates = [name for name,model in models.items()
                  if name.endswith(':free') and _zero_prices(model)
                  and isinstance(model.get('supported_parameters'),list)
                  and 'response_format' in model['supported_parameters']
                  and isinstance(model.get('architecture'),dict)
                  and isinstance(model['architecture'].get('input_modalities'),list)
                  and 'text' in model['architecture']['input_modalities']]
    if not candidates:
        raise ModelError("Нет доступной бесплатной текстовой JSON-модели. Платная модель не подставлена.")
    return sorted(candidates)[0]


class ModelClient:
    def __init__(self,config: ModelConfig):
        self.config = config.validate()
        self.cancel = Event()
        self.deadline = time.monotonic() + 180
        self.model = config.model
        self.requests = 0
        self.available_models = []
        self.limit = 12  # На вопрос, включая retry; отдельно лимит 6 шагов диалога.
        self.usage_events = []
        self.usage_sink = None
        self.purpose = 'dialog'
        self.turn_id = uuid4().hex
        self.progress = lambda text: None

    def _read_response(self, response, deadline):
        chunks=[];size=0;last_second=-1
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError()
            # urllib's HTTPResponse exposes its socket here. Update the remaining
            # timeout so upstream keep-alive whitespace cannot reset the budget.
            socket=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
            if socket is not None:socket.settimeout(remaining)
            chunk=response.read1(min(65536,MAX_RESPONSE+1-size))
            if not chunk:break
            chunks.append(chunk);size+=len(chunk)
            if size>MAX_RESPONSE:raise ModelError('Слишком большой ответ API.')
            if getattr(response,'length',None)==0:break
            if self.cancel.is_set():raise ModelError('Запрос остановлен.')
            elapsed=int(self.config.timeout-remaining)
            if elapsed!=last_second:
                self.progress(f'Жду ответ модели · {max(0,elapsed)} с из {self.config.timeout}')
                last_second=elapsed
        return b''.join(chunks)

    def _record_usage(self, event, headers, data):
        event.update(token_usage(data))
        event['limits'] = {}
        for name in ('retry-after', 'x-ratelimit-limit-requests', 'x-ratelimit-limit-tokens',
                     'x-ratelimit-remaining-requests', 'x-ratelimit-remaining-tokens',
                     'x-ratelimit-reset-requests', 'x-ratelimit-reset-tokens'):
            value = headers.get(name) if headers is not None else None
            if isinstance(value, str) and re.fullmatch(r'[0-9hms. :+-]{1,100}', value):
                event['limits'][name] = value
        self.usage_events.append(event)
        if self.usage_sink:
            self.usage_sink(event)

    def _request(self,path,payload=None):
        for attempt in range(2):
            if self.cancel.is_set():raise ModelError("Запрос остановлен.")
            if time.monotonic() >= self.deadline:raise ModelError("Достигнут общий лимит ожидания 180 секунд.")
            if self.requests>=self.limit:
                raise ModelError("Исчерпан лимит API-запросов на вопрос.")
            self.requests += 1
            config = self.config
            proxy = ProxyHandler({'https':config.proxy}) if config.proxy else ProxyHandler()
            opener = build_opener(proxy,_NoRedirect())
            request = Request(config.base_url+path,
                              data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode(),
                              headers={'Authorization':'Bearer '+config.api_key,'Content-Type':'application/json',
                                       'User-Agent':'tender-assistant/5.1'},
                              method='GET' if payload is None else 'POST')
            retry = False
            retry_delay = None
            attempt_deadline = min(self.deadline,time.monotonic()+config.timeout)
            data = None; response_headers = None
            event = {'time': now_moscow().isoformat(), 'turn_id': self.turn_id,
                     'provider': config.provider, 'model': self.model, 'purpose': self.purpose,
                     'endpoint': path, 'attempt': attempt+1, 'http_status': None,
                     'status': 'transport_error',
                     'input_characters': sum(len(m['content']) for m in payload.get('messages', [])) if payload else 0,
                     'max_tokens': payload.get('max_tokens') if payload else None}
            try:
                with opener.open(request,timeout=min(config.timeout, max(1,self.deadline-time.monotonic()))) as response:
                    response_headers = getattr(response, 'headers', None)
                    event.update(http_status=getattr(response, 'status', 200), status='response_error')
                    self.progress(f'Жду ответ модели · не более {config.timeout} с')
                    raw = self._read_response(response,attempt_deadline)
                data = strict_json(raw.decode('utf-8'))
                if not isinstance(data,dict):
                    raise ModelError("API вернул ошибку или неверную структуру ответа; детали скрыты.")
                if data.get('error'):
                    detail=data['error'];code=detail.get('code') if isinstance(detail,dict) else None
                    code=code if type(code) is int else None
                    event.update(status='api_error',api_error_code=code)
                    retry=code in RETRY_CODES
                    retry_delay=_retry_after(response_headers)
                    message='Сервер модели временно недоступен.' if retry else 'API вернул ошибку; детали скрыты.'
                else:
                    event['status'] = 'ok'
                    if self.cancel.is_set():raise ModelError("Запрос остановлен.")
                    return data
            except HTTPError as error:
                code = error.code
                response_headers = error.headers
                event.update(http_status=code, status='http_error')
                retry_delay=_retry_after(response_headers)
                hint = _bad_request_hint(error) if code == 400 else None
                error.close()
                if code==400 and hint==JSON_VALIDATION_HINT:
                    # Только ошибка генерации: один corrective retry контролирует Conversation.
                    # Не повторяем неизвестные 400/auth/payment и не выводим failed_generation.
                    raise ModelReplyError(hint) from None
                retry = code in RETRY_CODES
                message = {400:'API отклонил формат запроса/контекст.',401:'API отклонил ключ.',
                           402:'Недостаточно средств или требуется тариф.',403:'Доступ запрещён провайдером; проверьте доступность и настройки сети.',
                           404:'Модель или адрес недоступны.',429:'Лимит запросов провайдера.'}.get(code,f'Ошибка API HTTP {code}.')
                if hint:
                    message = hint
            except (URLError,TimeoutError,OSError):
                retry,message = True,'API недоступен или истёк таймаут; сетевые детали скрыты.'
                if event['http_status'] is not None:
                    retry=False;message='Истёк срок ожидания ответа модели. Расход может быть учтён провайдером; автоматического повтора нет.'
            except UnicodeError:
                raise ModelError('API вернул ответ не в UTF-8.') from None
            finally:
                self._record_usage(event, response_headers, data)
            if retry and attempt==0:
                delay=retry_delay if retry_delay is not None else (5 if event.get('http_status')==429 or event.get('api_error_code')==429 else 1)
                if delay>30 or delay>=self.deadline-time.monotonic():
                    raise ModelError(f'Провайдер просит подождать {delay} с. Повторите вопрос позже; автоматический повтор не выполнен.')
                self.progress(f'Провайдер временно недоступен · повтор через {delay} с')
                if self.cancel.wait(delay):raise ModelError("Запрос остановлен.")
                continue
            raise ModelError(message) from None
        raise ModelError('API не ответил.')

    def connect(self):
        # Explicit configured models use the account's routing and billing policy.
        # No model download, discovery or silent substitution is needed.
        if self.config.model and self.config.allow_data_collection is None:
            self.model=self.config.model;self.available_models=[self.model]
            return self.model
        catalog = self._request('/models')
        self.model = choose_model(self.config,catalog)
        self.available_models = sorted({m['id'] for m in catalog['data']
                                       if isinstance(m,dict) and isinstance(m.get('id'),str)
                                       and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}',m['id'])
                                       and m.get('active',True) is not False})[:500]
        # Проверка параметров из недоверенного каталога перед последующей отправкой.
        if not self.model or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,180}',self.model):
            raise ModelError('Некорректное имя модели в каталоге.')
        return self.model

    def begin_turn(self):
        self.requests = 0
        self.usage_events = []
        self.turn_id = uuid4().hex
        self.deadline = time.monotonic() + 180

    def complete(self,messages):
        if not self.model:
            raise ModelError('Модель ещё не выбрана.')
        payload = {'model':self.model,'messages':messages,'temperature':0,'stream':False,
                   'max_tokens':self.config.max_tokens,'response_format':{'type':'json_object'}}
        structured = self.config.provider=='groq' and self.model in ('openai/gpt-oss-20b','openai/gpt-oss-120b')
        if structured:
            payload['response_format'] = {'type':'json_schema','json_schema':{
                'name':'local_action','strict':True,'schema':ACTION_SCHEMA}}
            payload['messages'] = [*messages,{'role':'system','content':
                'Формат передачи ответа задаёт JSON Schema: обязательны kind, tool, args_json, text. '
                'Для answer: text — ответ, tool и args_json — null. Для read/propose: text — null, '
                'tool — одно имя операции; объект args из примеров передай JSON-строкой в args_json. '
                'Пример чтения: {"kind":"read","tool":"tasks","args_json":"{}","text":null}. '
                'Это обычный JSON-ответ, нативных function calls нет.'}]
        elif self.config.provider=='groq':
            payload['tool_choice'] = 'none'  # Наши операции передаются текстовым JSON, не API tools.
        if self.config.provider=='openrouter':
            payload['provider'] = {'allow_fallbacks':False,'require_parameters':True}
            if self.config.allow_data_collection is not None:
                payload['provider']['data_collection']='allow' if self.config.allow_data_collection else 'deny'
            if not self.config.allow_paid:
                payload['provider']['max_price'] = {'prompt':0,'completion':0,'request':0}
        data = self._request('/chat/completions',payload)
        try:
            choice = data['choices'][0]
            content = choice['message']['content']
            if choice.get('finish_reason') not in ('stop',None) or not isinstance(content,str) or not content.strip():
                raise ValueError()
            if structured:
                action = strict_json(content)
                if not isinstance(action,dict) or set(action)!={'kind','tool','args_json','text'}:
                    raise ValueError()
                if action['kind']=='answer' and action['tool'] is None and action['args_json'] is None:
                    if not isinstance(action['text'],str) or not action['text'].strip():
                        raise ValueError()
                    action = {'kind':'answer','text':action['text']}
                elif (action['kind'] in ('read','propose') and isinstance(action['tool'],str)
                      and isinstance(action['args_json'],str) and action['text'] is None):
                    args = strict_json(action['args_json'])
                    if not isinstance(args,dict):
                        raise ValueError()
                    action = {'kind':action['kind'],'tool':action['tool'],'args':args}
                else:
                    raise ValueError()
                content = json.dumps(action,ensure_ascii=False)
        except (KeyError,IndexError,TypeError,ValueError) as error:
            raise ModelReplyError('Неполный/пустой ответ модели. Изменения не выполнялись.') from error
        return content
