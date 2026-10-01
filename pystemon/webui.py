"""A small web page to look at, delete and control what pystemon collects.

Run it next to your config:

    PYSTEMON_WEB_PASSWORD=choose-something python -m pystemon.webui -c pystemon.yaml

It starts and stops the scraper (pystemon.py) as a child process, lists the saved
pastes in a tree (day by day), shows one, deletes some or all of them, and edits
the search terms and a few basic settings. Standard library plus PyYAML only.

Safety notes:
 * pastes can contain leaked passwords: a password is required unless you bind to 127.0.0.1
 * every change needs the password, a JSON content type and a custom header (no CSRF)
 * files are only read or deleted inside the configured alerts/archive folders
 * the page is meant to sit behind HTTPS (Nginx Proxy Manager); all links are relative so a
   path prefix works too
"""
import argparse
import collections
import gzip
import hmac
import http.server
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlparse, parse_qs

import yaml

logger = logging.getLogger('pystemon.web')

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'webui_static')
MAX_VIEW_BYTES = 1024 * 1024        # how much of one paste the viewer reads
MAX_LIST = 5000                     # newest files shown in the tree
MAX_DELETE = 20000                  # per request
MAX_BODY = 512 * 1024               # request body limit
MAX_HITS = 500                      # highlighted matches per paste
LOG_LINES = 300
MIME = {'.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
        '.css': 'text/css; charset=utf-8'}
# settings the form may change on a site, with the type the value must have
SITE_FIELDS = {'enable': bool, 'limit': int, 'lang': str, 'min-size': int, 'max-size': int,
               'mode': str, 'bind-ip': str, 'update-min': int, 'update-max': int}


# --------------------------------------------------------------------------- scraper control
class Scraper:
    """Starts and stops pystemon.py as a child process and keeps its last log lines."""

    def __init__(self, config_path, extra_args=None):
        self.config_path = config_path
        self.extra_args = extra_args or []
        self.proc = None
        self.started = None
        self.last_exit = None
        self.log = collections.deque(maxlen=LOG_LINES)
        self.lock = threading.RLock()

    def _script(self):
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'pystemon.py')

    def running(self):
        with self.lock:
            return self.proc is not None and self.proc.poll() is None

    def _reader(self, proc):
        for line in iter(proc.stdout.readline, b''):
            self.log.append(line.decode('utf-8', 'replace').rstrip())
        proc.stdout.close()
        code = proc.wait()
        with self.lock:
            if self.proc is proc:
                self.last_exit = code
        self.log.append('--- pystemon stopped (exit code {}) ---'.format(code))

    def start(self):
        with self.lock:
            if self.running():
                return False, 'already running'
            cmd = [sys.executable, '-u', self._script(), '-c', self.config_path] + self.extra_args
            self.log.append('--- starting: {} ---'.format(' '.join(cmd)))
            try:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             stdin=subprocess.DEVNULL, cwd=os.getcwd())
            except OSError as e:
                self.proc = None
                return False, 'could not start: {}'.format(e)
            self.started = time.time()
            self.last_exit = None
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
            return True, 'started'

    def stop(self, timeout=20):
        with self.lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return False, 'not running'
            self.log.append('--- stopping (asked politely) ---')
            proc.terminate()
        try:
            proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.log.append('--- did not stop in {}s, forcing ---'.format(timeout))
            proc.kill()
            proc.wait(5)
        return True, 'stopped'

    def reload(self):
        """Ask the running scraper to re-read its config (no restart)."""
        with self.lock:
            if not self.running():
                return False, 'not running'
            if hasattr(signal, 'SIGHUP'):
                self.proc.send_signal(signal.SIGHUP)
                return True, 'reload requested'
        ok, _ = self.stop()
        ok2, msg = self.start()
        return ok2, 'restarted' if ok2 else msg

    def status(self):
        with self.lock:
            run = self.running()
            return {'running': run, 'pid': self.proc.pid if run else None,
                    'uptime': int(time.time() - self.started) if run and self.started else 0,
                    'last_exit': self.last_exit}


# --------------------------------------------------------------------------- config helpers
def load_yaml(path):
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError('the config file is empty or not a mapping')
    return data


def validate_text(path, text):
    """Check a candidate config: YAML syntax, regexes, then pystemon's own parser."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError('config must be a YAML mapping')
    for item in data.get('search') or []:
        pat = item.get('search') if isinstance(item, dict) else None
        if not pat:
            raise ValueError('every search entry needs a "search:" pattern')
        re.compile(pat)
        if item.get('exclude'):
            re.compile(item['exclude'])
    from pystemon.config import PystemonConfig
    tmp = os.path.join(os.path.dirname(os.path.abspath(path)), '.pystemon-check.yaml')
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        PystemonConfig(tmp, False).reload()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return data


def write_config(path, text):
    """Save with a .bak copy of the previous file. Replace atomically where possible."""
    if os.path.exists(path):
        shutil.copyfile(path, path + '.bak')
    tmp = path + '.new'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        # e.g. a single file mounted into a container: write in place
        try:
            os.remove(tmp)
        except OSError:
            pass
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)


def public_view(data):
    """The parts of the config the form edits. Secrets are never sent."""
    sites = {}
    for name, cfg in (data.get('site') or {}).items():
        cfg = cfg or {}
        sites[name] = {k: cfg.get(k) for k in SITE_FIELDS}
        sites[name]['preset'] = cfg.get('preset')
    search = []
    for item in data.get('search') or []:
        if isinstance(item, dict):
            search.append({'search': str(item.get('search', '')), 'description': str(item.get('description') or ''),
                           'count': item.get('count'), 'exclude': item.get('exclude') or ''})
    return {'search': search, 'sites': sites,
            'network_ip': (data.get('network') or {}).get('ip') or '',
            'email_alert': bool((data.get('email') or {}).get('alert')),
            'logging_level': data.get('logging-level', 'INFO')}


def apply_form(data, form):
    """Merge the posted form into the parsed config (only the fields the form owns)."""
    if 'search' in form:
        new = []
        for item in form['search']:
            pat = str(item.get('search', '')).strip()
            if not pat:
                continue
            entry = {'search': pat}
            if str(item.get('description') or '').strip():
                entry['description'] = str(item['description']).strip()
            if item.get('count') not in (None, ''):
                entry['count'] = int(item['count'])
            if str(item.get('exclude') or '').strip():
                entry['exclude'] = str(item['exclude']).strip()
            new.append(entry)
        data['search'] = new
    for name, vals in (form.get('sites') or {}).items():
        site = (data.get('site') or {}).get(name)
        if site is None:
            raise ValueError('unknown site: {}'.format(name))
        for key, kind in SITE_FIELDS.items():
            if key not in vals:
                continue
            val = vals[key]
            if val in (None, ''):
                site.pop(key, None)
            elif kind is bool:
                site[key] = bool(val)
            elif kind is int:
                site[key] = int(val)
            else:
                site[key] = str(val).strip()
    if 'network_ip' in form:
        ip = str(form['network_ip'] or '').strip()
        if ip:
            data.setdefault('network', {})['ip'] = ip
        elif isinstance(data.get('network'), dict):
            data['network'].pop('ip', None)
            if not data['network']:
                data.pop('network')
    if 'email_alert' in form:
        data.setdefault('email', {})['alert'] = bool(form['email_alert'])
    return data


# --------------------------------------------------------------------------- paste files
class Store:
    """The alerts and archive folders from the config, with safe path handling."""

    def __init__(self, config_path):
        self.config_path = config_path
        self._favlock = threading.Lock()

    # -- favourites (starred pastes), kept in favourites.json next to the alerts folder
    def _fav_file(self):
        roots = self.roots()
        if not roots:
            return None
        return os.path.join(os.path.dirname(roots[0]['path']), 'favourites.json')

    @staticmethod
    def _rel(ident):
        return ident.split(':', 1)[1] if ':' in ident else ident

    def favourites(self):
        path = self._fav_file()
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            return set(str(x) for x in data) if isinstance(data, list) else set()
        except (OSError, ValueError, TypeError):
            return set()

    def _write_favs(self, favs):
        path = self._fav_file()
        if not path:
            raise ValueError('no storage folder found for favourites')
        tmp = path + '.new'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(sorted(favs), f)
        os.replace(tmp, path)

    def set_fav(self, ident, on):
        if not self.resolve(ident):
            raise ValueError('paste not found')
        rel = self._rel(ident)
        with self._favlock:
            favs = self.favourites()
            if on:
                favs.add(rel)
            else:
                favs.discard(rel)
            self._write_favs(favs)
        return on

    def roots(self):
        data = load_yaml(self.config_path)
        out = []
        for _, st in (data.get('storage') or {}).items():
            if not isinstance(st, dict) or 'FileStorage' not in str(st.get('storage-classname', '')):
                continue
            for key, label in (('dir', 'Matched'), ('dir-all', 'All pastes')):
                d = st.get(key)
                if d and os.path.isdir(d):
                    real = os.path.realpath(d)
                    if all(real != r['path'] for r in out):
                        out.append({'path': real, 'label': label})
        return out

    def resolve(self, ident):
        """'<root index>:<relative path>' to an absolute file path, or None if unsafe."""
        try:
            idx, rel = ident.split(':', 1)
            root = self.roots()[int(idx)]['path']
        except (ValueError, IndexError):
            return None
        full = os.path.realpath(os.path.join(root, rel))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return None
        return full, root

    def listing(self):
        favs = self.favourites()
        files = []
        for i, root in enumerate(self.roots()):
            for dirpath, _dirs, names in os.walk(root['path']):
                for n in names:
                    if n.endswith('.metadata'):
                        continue
                    full = os.path.join(dirpath, n)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    rel = os.path.relpath(full, root['path']).replace(os.sep, '/')
                    parts = rel.split('/')
                    # layout: site/YYYY/MM/DD/name
                    site = parts[0] if len(parts) > 4 else ''
                    day = '-'.join(parts[1:4]) if len(parts) > 4 else 'other'
                    files.append({'id': '{}:{}'.format(i, rel), 'root': i, 'site': site, 'day': day,
                                  'name': n, 'size': st.st_size, 'mtime': int(st.st_mtime), 'fav': rel in favs})
        files.sort(key=lambda f: f['mtime'], reverse=True)
        total = len(files)
        return {'roots': [r['label'] for r in self.roots()], 'files': files[:MAX_LIST], 'total': total}

    def read(self, ident):
        found = self.resolve(ident)
        if not found:
            return None
        path = found[0]
        opener = gzip.open if path.endswith('.gz') else open
        truncated = False
        try:
            with opener(path, 'rb') as f:
                raw = f.read(MAX_VIEW_BYTES + 1)
        except (OSError, EOFError) as e:
            return {'text': 'Cannot read this file: {}'.format(e), 'truncated': False, 'size': 0, 'hits': [], 'meta': ''}
        if len(raw) > MAX_VIEW_BYTES:
            raw, truncated = raw[:MAX_VIEW_BYTES], True
        meta = ''
        if os.path.isfile(path + '.metadata'):
            try:
                with open(path + '.metadata', 'rb') as f:
                    meta = f.read(20000).decode('utf-8', 'replace')
            except OSError:
                pass
        return {'text': raw.decode('utf-8', 'replace'), 'truncated': truncated,
                'size': os.path.getsize(path), 'meta': meta}

    def delete(self, idents):
        """Delete pastes, but never a starred one. Returns (deleted, kept_because_starred)."""
        removed = kept = 0
        favs = self.favourites()
        for ident in idents[:MAX_DELETE]:
            found = self.resolve(ident)
            if not found:
                continue
            if self._rel(ident) in favs:
                kept += 1
                continue
            path, root = found
            for p in (path, path + '.metadata'):
                try:
                    os.remove(p)
                    removed += 1 if p == path else 0
                except OSError:
                    pass
            d = os.path.dirname(path)      # tidy empty day/month/year folders
            while d != root and d.startswith(root + os.sep):
                try:
                    os.rmdir(d)
                except OSError:
                    break
                d = os.path.dirname(d)
        return removed, kept


def find_hits(text, data):
    """Highlight ranges for the configured search patterns (display only)."""
    hits, names = [], []
    flags = re.I
    for item in data.get('search') or []:
        try:
            rx = re.compile(item['search'], flags)
        except (re.error, KeyError, TypeError):
            continue
        found = False
        for m in rx.finditer(text):
            if m.end() > m.start():
                hits.append([m.start(), m.end()])
                found = True
            if len(hits) >= MAX_HITS:
                break
        if found:
            names.append(item.get('description') or item['search'])
    hits.sort()
    merged = []
    for s, e in hits:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged, names


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return ''


def public_ip():
    for url in ('https://api4.ipify.org', 'https://ifconfig.me/ip'):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'pystemon-web'})
            with urllib.request.urlopen(req, timeout=6) as r:
                ip = r.read(64).decode().strip()
            if re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', ip):
                return ip
        except Exception:
            continue
    return ''


# --------------------------------------------------------------------------- HTTP server
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'pystemon-web'
    sys_version = ''

    def log_message(self, fmt, *args):
        logger.debug('%s %s', self.address_string(), fmt % args)

    # -- plumbing
    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy',
                         "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                         "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        pw = self.server.password
        if not pw:
            return True
        import base64
        header = self.headers.get('Authorization', '')
        if header.startswith('Basic '):
            try:
                given = base64.b64decode(header[6:]).decode('utf-8', 'replace').split(':', 1)[1]
            except Exception:
                given = ''
            if hmac.compare_digest(given.encode(), pw.encode()):
                return True
            time.sleep(1)          # slow down guessing
        self._send(401, {'error': 'password required'}, extra={'WWW-Authenticate': 'Basic realm="pystemon", charset="UTF-8"'})
        return False

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            raise ValueError('request too large')
        return json.loads(self.rfile.read(n) or b'{}')

    def _csrf_ok(self):
        if (self.headers.get('Content-Type') or '').split(';')[0].strip() != 'application/json':
            return False
        if self.headers.get('X-Requested-With') != 'pystemon':
            return False
        origin = self.headers.get('Origin')
        if origin:
            host = self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or ''
            if urlparse(origin).netloc != host.split(',')[0].strip():
                return False
        return True

    # -- routes
    def do_GET(self):
        if not self._authorized():
            return
        url = urlparse(self.path)
        path = url.path
        qs = parse_qs(url.query)
        try:
            if path in ('/', '/index.html'):
                return self._static('index.html')
            if path in ('/app.js', '/app.css'):
                return self._static(path[1:])
            if path == '/api/status':
                return self._send(200, self._status())
            if path == '/api/pastes':
                return self._send(200, self.server.store.listing())
            if path == '/api/paste':
                res = self.server.store.read((qs.get('id') or [''])[0])
                if res is None:
                    return self._send(404, {'error': 'not found'})
                hits, names = find_hits(res['text'], load_yaml(self.server.config_path))
                res.update({'hits': hits, 'matched': names})
                return self._send(200, res)
            if path == '/api/config':
                with open(self.server.config_path, encoding='utf-8') as f:
                    raw = f.read()
                return self._send(200, {'form': public_view(load_yaml(self.server.config_path)), 'raw': raw})
            if path == '/api/publicip':
                return self._send(200, {'ip': public_ip()})
            self._send(404, {'error': 'not found'})
        except Exception as e:
            logger.error('GET %s failed: %s', path, e)
            self._send(500, {'error': str(e)})

    def do_POST(self):
        if not self._authorized():
            return
        if not self._csrf_ok():
            return self._send(403, {'error': 'blocked (missing or wrong request headers)'})
        path = urlparse(self.path).path
        sc = self.server.scraper
        try:
            body = self._body()
            if path == '/api/start':
                ok, msg = sc.start()
            elif path == '/api/stop':
                ok, msg = sc.stop()
            elif path == '/api/restart':
                sc.stop()
                ok, msg = sc.start()
            elif path == '/api/delete':
                ids = body.get('ids')
                if not isinstance(ids, list):
                    return self._send(400, {'error': 'ids must be a list'})
                n, kept = self.server.store.delete([str(i) for i in ids])
                return self._send(200, {'ok': True, 'deleted': n, 'kept_starred': kept})
            elif path == '/api/star':
                on = self.server.store.set_fav(str(body.get('id', '')), bool(body.get('on')))
                return self._send(200, {'ok': True, 'fav': on})
            elif path == '/api/config':
                text = body.get('raw')
                if text is None:
                    data = apply_form(load_yaml(self.server.config_path), body.get('form') or {})
                    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
                validate_text(self.server.config_path, text)
                write_config(self.server.config_path, text)
                applied = ''
                if body.get('apply', True) and sc.running():
                    applied = sc.reload()[1]
                return self._send(200, {'ok': True, 'applied': applied})
            else:
                return self._send(404, {'error': 'not found'})
            self._send(200, {'ok': ok, 'message': msg, 'status': self._status()})
        except Exception as e:
            self._send(400, {'error': str(e)})

    def _static(self, name):
        with open(os.path.join(STATIC_DIR, name), 'rb') as f:
            self._send(200, f.read(), MIME[os.path.splitext(name)[1]])

    def _status(self):
        st = self.server.scraper.status()
        port = self.server.server_address[1]
        st.update({'log': list(self.server.scraper.log)[-LOG_LINES:], 'lan_ip': local_ip(), 'port': port,
                   'config': os.path.abspath(self.server.config_path), 'time': int(time.time())})
        return st


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, config_path, password, scraper):
        super().__init__(addr, Handler)
        self.config_path = config_path
        self.password = password
        self.scraper = scraper
        self.store = Store(config_path)


def main(argv=None):
    p = argparse.ArgumentParser(description='Web page for pystemon (view, delete, configure, start/stop)')
    p.add_argument('-c', '--config', default='pystemon.yaml', help='pystemon config file')
    p.add_argument('--host', default='0.0.0.0', help='address to listen on (default all)')
    p.add_argument('--port', type=int, default=8080)
    p.add_argument('--no-autostart', action='store_true', help='do not start the scraper when this page starts')
    p.add_argument('--debug', action='store_true')
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    if not os.path.isfile(args.config):
        sys.exit('Configuration file not found: {}'.format(args.config))
    password = os.environ.get('PYSTEMON_WEB_PASSWORD', '')
    if not password and args.host not in ('127.0.0.1', 'localhost', '::1'):
        sys.exit('Set PYSTEMON_WEB_PASSWORD (pastes can hold leaked secrets), or use --host 127.0.0.1')
    scraper = Scraper(args.config)
    server = Server((args.host, args.port), args.config, password, scraper)

    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    if not args.no_autostart:
        scraper.start()
    logger.info('web page on http://%s:%d (password %s)', args.host, args.port, 'set' if password else 'NOT set')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scraper.stop()
        server.server_close()


if __name__ == '__main__':
    main()
