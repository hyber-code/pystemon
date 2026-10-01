"""Tiny hourly counters (pastes checked / matched) shared between the scraper and the web page.

Enabled only when PYSTEMON_STATS_FILE is set. Writes are batched (at most every 30 s) and never raise.
"""
import json
import os
import threading
import time

_lock = threading.Lock()
_pending = {}
_last_flush = [0.0]


def _bucket(ts=None):
    return time.strftime('%Y-%m-%dT%H', time.localtime(ts or time.time()))


def record(matched):
    path = os.environ.get('PYSTEMON_STATS_FILE')
    if not path:
        return
    with _lock:
        c = _pending.setdefault(_bucket(), [0, 0])
        c[0] += 1
        c[1] += 1 if matched else 0
        if time.time() - _last_flush[0] < 30:
            return
        _last_flush[0] = time.time()
        data = load(path)
        for k, (n, m) in _pending.items():
            old = data.get(k, [0, 0])
            data[k] = [old[0] + n, old[1] + m]
        _pending.clear()
        for k in sorted(data)[:-200]:
            del data[k]
        try:
            with open(path + '.new', 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(path + '.new', path)
        except OSError:
            pass


def load(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return {str(k): [int(v[0]), int(v[1])] for k, v in d.items()}
    except (OSError, ValueError, TypeError, IndexError):
        return {}


def last_hours(path, hours=24):
    """(checked, matched) over the last N hours, from the file only."""
    cutoff = _bucket(time.time() - hours * 3600)
    checked = matched = 0
    for k, (n, m) in load(path).items():
        if k > cutoff:
            checked += n
            matched += m
    return checked, matched
