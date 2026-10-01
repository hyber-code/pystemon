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


def test_webui_starred_pastes_are_protected(tmp_path):
    """A starred paste survives delete; unstarring makes it deletable; stars persist in a file."""
    from pystemon.webui import Store
    alerts = tmp_path / 'data' / 'alerts' / 'site' / '2026' / '10' / '01'
    alerts.mkdir(parents=True)
    (alerts / 'keep').write_bytes(b'a')
    (alerts / 'drop').write_bytes(b'b')
    cfg = tmp_path / 'p.yaml'
    cfg.write_text("storage:\n  archive:\n    storage-classname: FileStorage\n    save: yes\n    dir: '%s'\n" % (tmp_path / 'data' / 'alerts'))
    store = Store(str(cfg))
    files = {f['name']: f for f in store.listing()['files']}
    assert not files['keep']['fav']
    store.set_fav(files['keep']['id'], True)
    assert Store(str(cfg)).listing()['files'][0]['fav'] or any(f['fav'] for f in Store(str(cfg)).listing()['files'])
    removed, kept = store.delete([files['keep']['id'], files['drop']['id']])
    assert (removed, kept) == (1, 1)
    assert (alerts / 'keep').exists() and not (alerts / 'drop').exists()
    store.set_fav(files['keep']['id'], False)
    assert store.delete([files['keep']['id']]) == (1, 0)
    try:
        store.set_fav('0:site/2026/10/01/missing', True)
        assert False, 'should refuse a missing paste'
    except ValueError:
        pass


def test_webui_ip_ack_roundtrip(tmp_path):
    """The confirmed whitelisted IP is stored next to the favourites and validated."""
    from pystemon.webui import Store
    (tmp_path / 'data' / 'alerts').mkdir(parents=True)
    cfg = tmp_path / 'p.yaml'
    cfg.write_text("storage:\n  archive:\n    storage-classname: FileStorage\n    save: yes\n    dir: '%s'\n" % (tmp_path / 'data' / 'alerts'))
    store = Store(str(cfg))
    assert store.ip_ack() == ''
    store.set_ip_ack('1.2.3.4')
    assert Store(str(cfg)).ip_ack() == '1.2.3.4'
    for bad in ('x', '1.2.3', '<script>'):
        try:
            store.set_ip_ack(bad)
            assert False, 'should refuse ' + bad
        except ValueError:
            pass


def test_stats_counts_checked_and_matched(tmp_path, monkeypatch):
    """Hourly counters record every checked paste and the matches among them."""
    from pystemon import stats
    f = tmp_path / 'stats.json'
    monkeypatch.setenv('PYSTEMON_STATS_FILE', str(f))
    monkeypatch.setattr(stats, '_last_flush', [0.0])
    stats.record(False)
    monkeypatch.setattr(stats, '_last_flush', [0.0])
    stats.record(True)
    assert stats.last_hours(str(f), 24) == (2, 1)
    assert stats.last_hours(str(tmp_path / 'missing.json'), 24) == (0, 0)


def test_tgwatch_channel_names_and_matching(tmp_path):
    """Channel links normalise to names (invite links are refused); matching follows pystemon rules; hits are saved and not repeated."""
    import datetime
    from pystemon import tgwatch
    n = tgwatch.normalise_channel
    assert n('@Some_Chan') == 'Some_Chan' and n('https://t.me/Some_Chan/123') == 'Some_Chan' and n('t.me/s/Some_Chan') == 'Some_Chan'
    assert n('https://t.me/+AbCdEf123') == '' and n('https://t.me/joinchat/xyz') == '' and n('hi') == ''
    patterns = tgwatch.compile_patterns([
        {'search': 'iptv', 'description': 'iptv'},
        {'search': r'mega\.nz', 'count': 2, 'description': 'mega x2'},
        {'search': 'free', 'exclude': 'scam'},
    ])
    assert tgwatch.match_text(patterns, 'New IPTV list') == ['iptv']
    assert tgwatch.match_text(patterns, 'mega.nz/a and mega.nz/b') == ['mega x2']
    assert tgwatch.match_text(patterns, 'mega.nz/a only') == []
    assert tgwatch.match_text(patterns, 'free stuff') == ['free'] and tgwatch.match_text(patterns, 'free scam') == []
    alerts = tmp_path / 'data' / 'alerts'
    cfg = {'storage': {'a': {'storage-classname': 'FileStorage', 'dir': str(alerts)}}}
    when = datetime.datetime(2026, 10, 1, 9, 12, tzinfo=datetime.timezone.utc)
    state = {}
    checked, hit = tgwatch.handle_messages(cfg, patterns, 'chan', [(10, when, 'old iptv'), (11, when, 'nothing'), (12, when, '')], state)
    assert (checked, hit) == (2, 1) and state == {'chan': 12}
    files = list(alerts.rglob('*.txt'))
    assert len(files) == 1 and 'chan' in files[0].parts
    assert 'https://t.me/chan/10' in files[0].read_text() and 'Matched: iptv' in files[0].read_text()


def test_webui_telegram_mode_form(tmp_path, monkeypatch):
    """Telegram mode: channels are validated and normalised through the same save path as search terms."""
    from pystemon import webui
    monkeypatch.setitem(webui.WEB, 'mode', 'telegram')
    base = {'storage': {'a': {'storage-classname': 'FileStorage', 'dir': str(tmp_path)}}}
    data = webui.apply_form(dict(base), {'channels': ['https://t.me/Alpha_One', '@alpha_one', 'beta_two', ''], 'search': []})
    assert data['channels'] == ['@Alpha_One', '@beta_two']
    try:
        webui.apply_form(dict(base), {'channels': ['https://t.me/+secretinvite']})
        assert False, 'invite link must be refused'
    except ValueError:
        pass
    webui.validate_text(str(tmp_path / 'x.yaml'), webui.yaml.safe_dump(data))
    assert webui.public_view(data)['channels'] == ['@Alpha_One', '@beta_two']


def test_tgwatch_discovery_keeps_only_rule_confirmed_posts(tmp_path):
    """Telegram's fuzzy post search is filtered by our own rules; channels are listed once, repeats are not saved twice."""
    import datetime
    from types import SimpleNamespace as NS
    from pystemon import tgwatch
    assert tgwatch.discovery_query({'search': 'iptv'}) == 'iptv'
    assert tgwatch.discovery_query({'search': r'mega\.nz'}) == 'mega.nz'
    assert tgwatch.discovery_query({'search': r'(a|b)\.com'}) == ''
    assert tgwatch.discovery_query({'search': r'(a|b)\.com', 'query': 'a b'}) == 'a b'
    alerts = tmp_path / 'data' / 'alerts'
    cfg = {'storage': {'a': {'storage-classname': 'FileStorage', 'dir': str(alerts)}}}
    patterns = tgwatch.compile_patterns([{'search': 'iptv', 'description': 'iptv'}])
    when = datetime.datetime(2026, 10, 1, 9, 0, tzinfo=datetime.timezone.utc)
    chats = [NS(id=1, username='good_chan', title='Good'), NS(id=2, username=None, title='No name'), NS(id=3, username='other', title='Other')]
    msgs = [NS(id=5, date=when, message='fresh IPTV list', peer_id=NS(channel_id=1)),
            NS(id=6, date=when, message='unrelated', peer_id=NS(channel_id=3)),
            NS(id=7, date=when, message='iptv but private', peer_id=NS(channel_id=2))]
    res = NS(messages=msgs, chats=chats)
    disc = tgwatch.load_discovered(str(tmp_path / 'none.json'))
    assert tgwatch.process_post_results(cfg, patterns, 'iptv', res, disc, 100) == 1
    assert tgwatch.process_post_results(cfg, patterns, 'iptv', res, disc, 200) == 0   # repeat: nothing new
    assert list(disc['channels']) == ['good_chan'] and disc['channels']['good_chan']['hits'] == 1
    name_res = NS(chats=[NS(id=9, username='iptv_deals', title='IPTV deals', broadcast=True, megagroup=False), NS(id=10, username=None, title='x', broadcast=True, megagroup=False)])
    assert tgwatch.process_name_results(name_res, disc, 300) == 1 and 'iptv_deals' in disc['channels']
    # a message saved by search is not saved again when the channel is watched later
    n, hit = tgwatch.handle_messages(cfg, patterns, 'good_chan', [(5, when, 'fresh IPTV list')], {})
    assert hit == 0 and len(list(alerts.rglob('*.txt'))) == 1


def test_webui_discovered_list_and_dismiss(tmp_path):
    from pystemon import tgwatch
    from pystemon.webui import Store
    (tmp_path / 'data' / 'alerts').mkdir(parents=True)
    cfg = tmp_path / 'p.yaml'
    cfg.write_text("storage:\n  a:\n    storage-classname: FileStorage\n    dir: '%s'\n" % (tmp_path / 'data' / 'alerts'))
    disc = {'channels': {n: {'title': n, 'hits': h, 'via': ['posts'], 'last': 1} for n, h in (('aaa_chan', 1), ('bbb_chan', 5), ('ccc_chan', 2))}, 'seen': {}}
    tgwatch.save_discovered(str(tmp_path / 'data' / 'discovered.json'), disc)
    store = Store(str(cfg))
    assert [c['name'] for c in store.discovered(['ccc_chan'])] == ['bbb_chan', 'aaa_chan']   # watched ones are hidden, most hits first
    store.dismiss_channel('bbb_chan')
    assert [c['name'] for c in store.discovered([])] == ['ccc_chan', 'aaa_chan']
