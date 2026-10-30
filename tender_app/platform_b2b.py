"""Visible-browser B2B collection, adapted from Tensers without positions or contacts."""
import os
from pathlib import Path
import re
import tempfile
import time

from .platform_clients import PlatformCard, PlatformError, PlatformStopped, trusted_url
from .platform_files import MAX_FILE_BYTES, extract_archive, publish_file


class B2BClient:
    HOSTS = ('www.b2b-center.ru', 'b2b-center.ru')

    def __init__(self, profile, say=lambda text: None, stopped=lambda: False):
        self.profile = Path(profile)
        self.say, self.stopped = say, stopped
        self.runtime = self.context = self.page = None

    def close(self):
        try:
            if self.context:
                self.context.close()
        finally:
            if self.runtime:
                self.runtime.stop()

    def _open(self):
        from playwright.sync_api import sync_playwright
        candidates = [Path(os.environ.get(variable, '')) / vendor / 'Application' / executable
                      for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA')
                      for vendor, executable in (('Google/Chrome', 'chrome.exe'), ('Microsoft/Edge', 'msedge.exe'))]
        executable = next((p for p in candidates if p.is_file()), None)
        if executable is None:
            raise PlatformStopped('Для B2B нужен установленный Chrome или Edge.')
        self.profile.mkdir(parents=True, exist_ok=True)
        self.runtime = sync_playwright().start()
        try:
            self.context = self.runtime.chromium.launch_persistent_context(str(self.profile),
                executable_path=str(executable), headless=False, accept_downloads=True,
                viewport=None, args=['--start-maximized'])
            self.context.set_default_timeout(15000)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        except Exception:
            self.close()
            self.runtime = self.context = None
            raise PlatformStopped('Не удалось открыть отдельный профиль B2B. Закройте другое окно загрузки B2B.') from None

    def _check(self, response=None):
        if self.stopped():
            raise PlatformStopped('Обработка остановлена.')
        trusted_url(self.page.url, self.HOSTS)
        if response is not None and response.status in (401, 403, 429):
            raise PlatformStopped('B2B ограничил доступ. Обработка площадки остановлена.')
        text = self.page.locator('body').inner_text()[:20000]
        if re.search(r'доступ\s+ограничен|подтвердите.*не\s+робот|проверка\s+браузера|captcha|access denied', text, re.I):
            raise PlatformStopped('B2B требует проверки доступа в браузере. Обработка остановлена.')

    def _login(self, url):
        def authenticated():
            for page in reversed(self.context.pages):
                if (not page.is_closed() and urlsplit_host(page.url) in self.HOSTS
                        and page.locator('[data-xid="header-logout"]').count()):
                    return page
            return None
        logged_in = authenticated()
        if logged_in is None:
            self.say('B2B: войдите в открывшемся Chrome/Edge. Ожидание до 5 минут.')
            login = self.page.locator('a[href*="/auth/openid/authorize/"]').first
            if login.count() and login.is_visible():
                login.click()
            until = time.monotonic() + 300
            while time.monotonic() < until:
                if self.stopped() or not self.context.pages:
                    raise PlatformStopped('Вход B2B остановлен.')
                logged_in = authenticated()
                if logged_in is not None:
                    break
                time.sleep(0.25)
        if logged_in is None:
            raise PlatformStopped('Вход B2B не выполнен за 5 минут.')
        self.page = logged_in
        if self.page.url != url:
            response = self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
            self._check(response)

    def read(self, number, url):
        from .resource_sync import B2BNotFound, lookup_b2b
        # Keep the source client's five-second per-procedure pause.
        for _ in range(20):
            if self.stopped():
                raise PlatformStopped('Обработка остановлена.')
            time.sleep(0.25)
        if not url:
            try:
                url = lookup_b2b(number)
            except B2BNotFound as error:
                raise PlatformError(str(error)) from None
            except Exception:
                raise PlatformStopped('Поиск B2B недоступен, заблокирован или изменил протокол. Очередь остановлена.') from None
        url = trusted_url(url, self.HOSTS)
        if not self.context:
            self._open()
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
        self._check(response)
        new_layout = '/app/market-next/' in self.page.url
        self.page.locator('[data-xid="trade-id"]' if new_layout else 'table').first.wait_for(state='visible', timeout=60000)
        data = self.page.evaluate(NEW_CARD_JS if new_layout else LEGACY_CARD_JS)
        # Classic helper derives its number from URL; verify heading independently.
        if new_layout:
            identity = data.get('number', '')
        else:
            identity = self.page.locator('h1,h2,title').all_text_contents()
            identity = ' '.join(identity)
        if not re.search(r'(?<!\d)' + re.escape(number) + r'(?!\d)', identity):
            raise PlatformStopped('B2B: номер карточки не подтверждён. Обработка остановлена.')
        sections = data.get('sections', {})
        # Dates may be outside h5 sections in the new header: read labelled text only.
        from .platform_deadlines import sections_from_text
        sections.update(sections_from_text(self.page.locator('body').inner_text()))
        return PlatformCard(number, trusted_url(self.page.url, self.HOSTS), sections, data.get('documents', []))

    def download(self, card, folder, say):
        self._login(card.url)
        self._check()
        new_layout = '/app/market-next/' in self.page.url
        self.page.locator('[data-xid="trade-id"]' if new_layout else 'table').first.wait_for(state='visible', timeout=60000)
        data = self.page.evaluate(NEW_CARD_JS if new_layout else LEGACY_CARD_JS)
        card.documents = data.get('documents', [])
        paths = []
        button = self.page.locator('[data-xid="download-trade-docs-button"]').first
        if not button.count():
            button = self.page.get_by_role('button', name='Скачать все документы', exact=True).first
        if button.count() and button.is_visible():
            actions = [(button, 'B2B-' + card.number + '-документы.zip')]
        else:
            actions = []
            for document in card.documents:
                trusted_url(document['url'], self.HOSTS)
                actions.append((self.page.get_by_role('link', name=document['name'], exact=True).first,
                                document.get('name') or 'Документ'))
            if not actions:
                say('В карточке нет доступной документации организатора.')
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for locator, fallback in actions:
            self._check()
            try:
                href = locator.get_attribute('href')
                if href:
                    from urllib.parse import urljoin
                    trusted_url(urljoin(self.page.url, href), self.HOSTS)
                for _ in range(6):
                    if self.stopped():
                        raise PlatformStopped('Загрузка остановлена.')
                    time.sleep(0.25)
                with self.page.expect_download(timeout=120000) as pending:
                    locator.click()
                download = pending.value
                with tempfile.TemporaryDirectory(prefix='.download-', dir=folder) as temporary:
                    staging = Path(temporary) / 'document.part'
                    download.save_as(str(staging))
                    if self.stopped():
                        raise PlatformStopped('Загрузка остановлена.')
                    if not staging.stat().st_size or staging.stat().st_size > MAX_FILE_BYTES:
                        raise PlatformError('B2B вернул пустой или слишком большой документ.')
                    path = publish_file(staging, folder, download.suggested_filename or fallback)
                if path in paths:
                    continue
                paths.append(path)
                say('Файл сохранён: ' + path.name)
                yield path
                try:
                    warning = extract_archive(path)
                    if warning:
                        say(warning)
                except Exception:
                    say('Не удалось распаковать архив: ' + path.name + '. Оригинал сохранён.')
            except PlatformStopped:
                raise
            except Exception:
                say('Не удалось скачать документ B2B. Уже полученные файлы сохранены.')


def urlsplit_host(url):
    from urllib.parse import urlsplit
    return urlsplit(url).hostname


NEW_CARD_JS = r"""
() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const title = clean(document.title.split(/ — Тендер №/)[0]);
  const sections = {};
  for (const h of document.querySelectorAll('h5')) {
    const key = clean(h.textContent);
    const block = h.closest('[data-xid]') || h.parentElement;
    if (!key || !block) continue;
    const clone = block.cloneNode(true);
    clone.querySelectorAll('h5,svg').forEach(x => x.remove());
    const value = clean(clone.textContent);
    if (value && !sections[key]) sections[key] = value;
  }
  const docsRoot = document.querySelector('[data-xid="delivery-widget-organizer-docs"]');
  const documents = [...(docsRoot || document.createElement('div')).querySelectorAll('a[href*="download.html"]')]
    .map(a => {
      const url = new URL(a.getAttribute('href'), location.href);
      return {name: clean(a.textContent), url: url.href,
              checksum: url.searchParams.get('checksum') || '',
              size: clean(a.parentElement && a.parentElement.textContent).replace(clean(a.textContent), '').trim()};
    });
  const contactHeading = [...document.querySelectorAll('h4')]
    .find(h => clean(h.textContent) === 'Контакты');
  const contactBlock = contactHeading && (contactHeading.parentElement?.parentElement || contactHeading.parentElement);
  const header = document.querySelector('.trade-header-title, h1');
  const tradeId = document.querySelector('[data-xid="trade-id"]');
  const stage = document.querySelector('[data-xid="trade-stages"]');
  return {title, number: clean(tradeId && tradeId.textContent), url: location.href,
          category: clean(header?.parentElement?.previousElementSibling?.textContent),
          status: clean(stage && stage.textContent), sections,
          contacts: clean(contactBlock && contactBlock.textContent), documents};
}
"""

LEGACY_CARD_JS = r"""
() => {
  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
  const sections = {};
  for (const row of document.querySelectorAll('tr')) {
    const cells = [...row.children].filter(x => /^(TD|TH)$/.test(x.tagName));
    if (cells.length !== 2) continue;
    const key = clean(cells[0].textContent).replace(/:$/, '');
    const value = clean(cells[1].textContent);
    if (key && value && key.length < 200 && !sections[key]) sections[key] = value;
  }
  const documents = [...document.querySelectorAll('a[href*="download.html"]')]
    .map(a => { const url = new URL(a.getAttribute('href'), location.href);
      return {name: clean(a.textContent), url: url.href,
              checksum: url.searchParams.get('checksum') || '', size: ''}; });
  const title = clean(document.title.split(/ — Тендер №/)[0]);
  const match = location.href.match(/tender-(\d+)/) || location.href.match(/[?&]id=(\d+)/);
  const contacts = Object.entries(sections).filter(([k]) => /контакт/i.test(k))
    .map(([,v]) => v).join(' ');
  return {title, number: match ? match[1] : '', url: location.href,
          category: sections['Категория'] || '', status: sections['Статус'] || '',
          sections, contacts, documents};
}
"""
