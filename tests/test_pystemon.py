import http.server
import logging
import threading
import time
import socketserver

import pytest

from pystemon.exception import PystemonConfigException
from pystemon.pastiesearch import parse_flags
from pystemon.presets import apply_preset
from pystemon.ua import PystemonUA, backoff_seconds, parse_retry_after
from pystemon.config import SiteConfig
import re


# ---------------------------------------------------------------- helpers
class Mock(http.server.BaseHTTPRequestHandler):
    hits = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        Mock.hits[self.path] = Mock.hits.get(self.path, 0) + 1
        n = Mock.hits[self.path]
        if self.path == '/ok':
            self._send(200, b'hello')
        elif self.path == '/limited':          # 429 once, then fine
            if n == 1:
                self._send(429, b'', {'Retry-After': '2'})
            else:
                self._send(200, b'after-wait')
        elif self.path == '/missing':
            self._send(404, b'nope')
        elif self.path == '/big':
            self._send(200, b'x' * 200000)
        elif self.path == '/whoami':
            self._send(200, self.client_address[0].encode())
        else:
            self._send(404, b'')

    def _send(self, code, body, headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope='module')
def server():
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Mock)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield 'http://127.0.0.1:%d' % httpd.server_address[1]
    httpd.shutdown()


def ua(**kw):
    return PystemonUA('[test]', None, **kw)


# ---------------------------------------------------------------- HTTP client
def test_download_ok(server):
    r = ua().download_url(server + '/ok')
    assert r is not None and r.content == b'hello'


def test_429_waits_for_retry_after(server):
    Mock.hits.clear()
    start = time.time()
    r = ua().download_url(server + '/limited')
    assert r is not None and r.content == b'after-wait'
    assert time.time() - start >= 1.9          # honoured Retry-After: 2
    assert Mock.hits['/limited'] == 2          # and did not hammer the server


def test_404_gives_up_quickly(server):
    Mock.hits.clear()
    r = ua(retries_client=2).download_url(server + '/missing')
    assert r is None
    assert Mock.hits['/missing'] == 2


def test_size_cap(server):
    assert ua(max_bytes=1000).download_url(server + '/big') is None
    assert len(ua(max_bytes=1000000).download_url(server + '/big').content) == 200000


def test_source_ip_is_used(server):
    r = ua(ip_addr='127.0.0.1').download_url(server + '/whoami')
    assert r.content == b'127.0.0.1'


def test_retry_helpers():
    assert parse_retry_after('30') == 30
    assert parse_retry_after('junk', default=7) == 7
    assert parse_retry_after('999999') == 3600
    assert [backoff_seconds(i) for i in (1, 2, 3, 10)] == [5, 10, 20, 300]


# ---------------------------------------------------------------- regex flags
def test_flags_are_parsed_not_evaluated():
    assert parse_flags(re, 're.I | re.M') == re.I | re.M
    assert parse_flags(re, 'IGNORECASE') == re.I
    for bad in ("__import__('os').system('id')", 're.I; 1', 'open("x")', 're.NOPE'):
        with pytest.raises(ValueError):
            parse_flags(re, bad)


# ---------------------------------------------------------------- presets
def site(**kw):
    conf = {'enable': True, 'preset': 'pastebin'}
    conf.update(kw)
    return SiteConfig('pastebin.com_pro', conf)


def test_pastebin_scrape_defaults():
    s = site()
    assert s.archive_format == 'json'
    assert 'api_scraping.php?limit=100' in s.archive_url
    assert s.download_url.startswith('https://scrape.pastebin.com/api_scrape_item.php')
    assert s.metadata_url is None
    assert s.use_proxy is False
    assert s.throttling == 1000 and s.update_min == 60
    assert not s.public_url.endswith('/raw/{id}') and '/raw/' not in s.public_url


def test_pastebin_scrape_rules_are_enforced():
    s = site(limit=500, **{'update-min': 10, 'update-max': 20, 'throttling': 100})
    assert 'limit=250' in s.archive_url
    assert s.update_min == 60 and s.update_max >= 60 and s.throttling == 1000
    with pytest.raises(PystemonConfigException):
        site(**{'use-proxy': True})


def test_pastebin_options():
    s = site(lang='php', metadata=True, **{'syntax-exclude': ['Text'], 'bind-ip': '1.2.3.4'})
    assert '&lang=php' in s.archive_url
    assert s.metadata_url and 'api_scrape_item_meta.php' in s.metadata_url
    assert s.listing_filter['syntax-exclude'] == ['text']
    assert s.bind_ip == '1.2.3.4'


def test_api_mode_is_not_clamped_and_can_be_overridden():
    s = site(mode='api', limit=500, **{'download-url': 'https://api.example/get/{id}', 'throttling': 100})
    assert 'limit=500' in s.archive_url
    assert s.download_url == 'https://api.example/get/{id}'
    assert s.throttling == 100


def test_unknown_preset_or_mode():
    with pytest.raises(PystemonConfigException):
        apply_preset('x', {'preset': 'nope'})
    with pytest.raises(PystemonConfigException):
        apply_preset('x', {'preset': 'pastebin', 'mode': 'nope'})


def test_pastebin_web_scraping_conflicts(tmp_path):
    from pystemon.config import PystemonConfig
    cfg = tmp_path / 'c.yaml'
    cfg.write_text('''
storage:
  archive: {storage-classname: FileStorage, save: no, save-all: no}
search:
  - search: 'x'
site:
  pastebin.com_pro: {enable: yes, preset: pastebin}
  pastebin.com:
    enable: yes
    archive-url: 'https://pastebin.com/archive'
    archive-regex: 'x'
    download-url: 'https://pastebin.com/raw/{id}'
''')
    with pytest.raises(Exception) as e:
        c = PystemonConfig(str(cfg), False)
        c.reload()
    assert 'Disable one of them' in str(e.value) or 'scrapes pastebin.com' in str(e.value)


# ---------------------------------------------------------------- listing
class FakeSite:
    pass


def listing(**flt):
    import types
    from pystemon.pastiesite import PastieSite
    ns = types.SimpleNamespace(name='test', archive_json_key='key', listing_filter=flt)
    return types.SimpleNamespace(ids_from_json=lambda text: PastieSite.ids_from_json(ns, text))


JSON = ('[{"key":"aaaa1111","size":"100","syntax":"text"},'
        '{"key":"bbbb2222","size":"5000000","syntax":"php"},'
        '{"key":"cccc3333","size":"50","syntax":"python"}]')


def test_json_listing_and_filters():
    assert listing().ids_from_json(JSON) == ['aaaa1111', 'bbbb2222', 'cccc3333']
    assert listing(**{'max-size': 1000}).ids_from_json(JSON) == ['aaaa1111', 'cccc3333']
    assert listing(**{'min-size': 60}).ids_from_json(JSON) == ['aaaa1111', 'bbbb2222']
    assert listing(**{'syntax-exclude': ['text']}).ids_from_json(JSON) == ['bbbb2222', 'cccc3333']
    assert listing(**{'syntax-include': ['python']}).ids_from_json(JSON) == ['cccc3333']
    assert listing().ids_from_json('not json') == []
    assert listing().ids_from_json('[{"nokey":1}, 5]') == []


# ---------------------------------------------------------------- email
def test_email_with_binary_content_is_still_sent(monkeypatch):
    from pystemon import sendmail as sm
    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **k): sent['init'] = (a, k)
        def starttls(self, **k): sent['tls'] = k
        def login(self, *a): pass
        def sendmail(self, f, to, msg): sent['msg'] = msg
        def quit(self): sent['quit'] = True

    monkeypatch.setattr(sm.smtplib, 'SMTP', FakeSMTP)

    class P:
        id = 'i'; public_url = 'http://x/i\r\nBcc: evil@example.com'
        pastie_content = b'\xff\xfe caf\xe9'
        matches = []
        site = type('S', (), {'name': 's'})()
        def matches_to_text(self): return '[m]'
        def matches_to_regex(self): return '[m]'

    m = sm.PystemonSendmail('a@x', 'b@x', '[p] {subject}', tls=True)
    m.send_pastie_alert(P())
    assert 'msg' in sent and sent['quit']
    assert 'context' in sent['tls']                       # certificate is verified
    header_block = sent['msg'].split('\n\n', 1)[0]
    assert not [l for l in header_block.splitlines() if l.lower().startswith('bcc:')]   # no header injection


# ---------------------------------------------------------------- shutdown
def test_throttler_stops_promptly_while_idle():
    from pystemon.throttler import ThreadThrottler
    th = ThreadThrottler('x', 60000)
    th.daemon = True
    th.start()
    time.sleep(0.3)
    start = time.time()
    th.stop()
    th.join(5)
    assert not th.is_alive()
    assert time.time() - start < 3


def test_throttler_spaces_requests():
    from pystemon.throttler import ThreadThrottler
    th = ThreadThrottler('x', 500)
    th.daemon = True
    th.start()
    start = time.time()
    for _ in range(3):
        th.wait()
    elapsed = time.time() - start
    th.stop()
    th.join(5)
    assert elapsed >= 0.9            # 3 requests, 500 ms apart


# ---------------------------------------------------------------- syntax names
def test_syntax_list_and_typo_warning(caplog):
    from pystemon.pastebin_syntaxes import PASTEBIN_SYNTAXES
    assert {'php', 'python', 'cpp', 'csharp', 'text', 'dos', 'html5'} <= PASTEBIN_SYNTAXES
    assert len(PASTEBIN_SYNTAXES) > 200
    with caplog.at_level(logging.WARNING, logger='pystemon'):
        site(lang='pyhton', **{'syntax-exclude': ['php', 'nonsense']})
    text = caplog.text
    assert "lang 'pyhton'" in text and 'python' in text     # typo caught, suggestion given
    assert "'nonsense'" in text and "'php'" not in text


def test_lang_is_sent_lowercase():
    assert '&lang=php' in site(lang='PHP').archive_url


def test_filestorage_save_all_off_keeps_only_matches(tmp_path):
    """save-all: no must not write non-matching pastes, even when dir-all is set."""
    from types import SimpleNamespace
    from pystemon.storage.filestorage import FileStorage

    def paste(pid, matched):
        site = SimpleNamespace(site='example', name='example')
        return SimpleNamespace(site=site, id=pid, filename=pid, pastie_content=b'data',
                               pastie_metadata=None, matched=matched)

    storage = FileStorage(name='t', **{'dir': str(tmp_path / 'alerts'), 'dir-all': str(tmp_path / 'archive'),
                                        'save': True, 'save-all': False, 'compress': False})
    assert storage.archive_dir is None
    storage.__save_pastie__(paste('miss', False))
    storage.__save_pastie__(paste('hit', True))
    saved = [p.name for p in tmp_path.rglob('*') if p.is_file()]
    assert saved == ['hit']

    everything = FileStorage(name='t', **{'dir': str(tmp_path / 'a2'), 'dir-all': str(tmp_path / 'b2'),
                                           'save': True, 'save-all': True, 'compress': False})
    everything.__save_pastie__(paste('miss2', False))
    assert [p.name for p in (tmp_path / 'b2').rglob('*') if p.is_file()] == ['miss2']
