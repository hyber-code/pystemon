"""tgwatch: a small read-only keyword watcher for PUBLIC Telegram channels and groups.

It logs in as your own (spare) Telegram account through Telethon, reads new messages from the
channels listed in the config, and saves the ones that match your search terms as text files,
in the same folder layout the web page already shows (channel / year / month / day / message).

It never posts, never joins private invite links, never sends messages and keeps a slow pace.
Credentials come from the environment (TG_API_ID, TG_API_HASH), never from the config file.

  python -m pystemon.tgwatch -c /etc/tgwatch/tgwatch.yaml login   (once, interactive)
  python -m pystemon.tgwatch -c /etc/tgwatch/tgwatch.yaml run
"""
import argparse
import asyncio
import glob
import json
import logging
import os
import re
import signal
import sys
import time

import yaml

from pystemon import stats

logger = logging.getLogger('tgwatch')

DEFAULT_POLL = 300        # seconds between rounds
PAUSE_BETWEEN = 4         # seconds between two channels (be gentle)
FIRST_RUN_BACKFILL = 20   # on the very first look at a channel, only the latest N messages
PER_ROUND_LIMIT = 200     # at most this many new messages per channel per round
MAX_FLOOD_WAIT = 900


def normalise_channel(text):
    """'@name', 'name', 'https://t.me/name', 't.me/s/name/123' -> 'name'. Private invites return ''."""
    t = str(text or '').strip()
    t = re.sub(r'^https?://', '', t, flags=re.I)
    t = re.sub(r'^(www\.)?(t|telegram)\.me/', '', t, flags=re.I)
    t = re.sub(r'^s/', '', t)
    t = t.lstrip('@')
    if t.lower().startswith(('joinchat/', 'addlist/', 'c/')):
        return ''
    t = t.split('/')[0].split('?')[0]
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', t):
        return ''   # invite links (+xxxx, joinchat) and junk are skipped on purpose
    return t


def compile_patterns(search):
    out = []
    for item in search or []:
        if not isinstance(item, dict) or not item.get('search'):
            continue
        out.append({
            're': re.compile(item['search'], re.IGNORECASE),
            'count': int(item.get('count') or 1),
            'exclude': re.compile(item['exclude'], re.IGNORECASE) if item.get('exclude') else None,
            'name': item.get('description') or item['search'],
        })
    return out


def match_text(patterns, text):
    """Names of the search terms that match this text (same rules as pystemon: count, exclude)."""
    hits = []
    for p in patterns:
        found = p['re'].findall(text)
        if len(found) >= p['count'] and not (p['exclude'] and p['exclude'].search(text)):
            hits.append(p['name'])
    return hits


def save_message(root, channel, msg_id, when, text, hits):
    """Write one matched message; returns the file path. `when` is a timezone-aware datetime."""
    local = when.astimezone()
    folder = os.path.join(root, channel, local.strftime('%Y'), local.strftime('%m'), local.strftime('%d'))
    os.makedirs(folder, exist_ok=True)
    if glob.glob(os.path.join(folder, '*_%d.txt' % msg_id)):
        return None   # already saved (found by search earlier, or watched later)
    path = os.path.join(folder, '%s_%d.txt' % (local.strftime('%H%M%S'), msg_id))
    body = 'Channel: @%s\nMessage: https://t.me/%s/%d\nTime: %s\nMatched: %s\n----\n%s\n' % (
        channel, channel, msg_id, local.strftime('%Y-%m-%d %H:%M:%S %Z'), ', '.join(hits), text)
    tmp = path + '.new'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(body)
    os.replace(tmp, path)
    return path


def load_state(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return {str(k): int(v) for k, v in d.items()}
    except (OSError, ValueError, TypeError):
        return {}


def save_state(path, state):
    tmp = path + '.new'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f)
    os.replace(tmp, path)


def read_config(path):
    with open(path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError('config must be a YAML mapping')
    return cfg


def alerts_root(cfg):
    for _, st in (cfg.get('storage') or {}).items():
        if isinstance(st, dict) and 'FileStorage' in str(st.get('storage-classname', '')) and st.get('dir'):
            return st['dir']
    raise ValueError('no FileStorage with a "dir:" found under storage:')


DISCOVER_INTERVAL = 3600   # seconds between discovery rounds
DISCOVER_PAUSE = 8         # seconds between two Telegram searches
MAX_DISCOVER_TERMS = 10
SEEN_PER_CHANNEL = 300


def discovery_query(item):
    """Plain words to hand to Telegram's search for one search term, or '' if the term cannot be used that way."""
    q = str(item.get('query') or '').strip()
    if q:
        return q
    plain = str(item.get('search') or '').replace('\\.', '.')
    return plain if re.fullmatch(r'[\w][\w .\-]{2,60}', plain) else ''


def discovered_path(cfg):
    return os.path.join(os.path.dirname(alerts_root(cfg).rstrip('/')), 'discovered.json')


def load_discovered(path):
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault('channels', {})
            d.setdefault('seen', {})
            return d
    except (OSError, ValueError):
        pass
    return {'channels': {}, 'seen': {}}


def save_discovered(path, disc):
    tmp = path + '.new'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(disc, f)
    os.replace(tmp, path)


def _note_channel(disc, name, title, via, now):
    e = disc['channels'].setdefault(name, {'title': title or name, 'hits': 0, 'first': now, 'last': now, 'via': [], 'dismissed': False})
    e['title'] = title or e.get('title') or name
    e['last'] = now
    if via not in e['via']:
        e['via'].append(via)
    return e


def process_post_results(cfg, patterns, term, result, disc, now):
    """Messages found by Telegram's public post search. Only those our own rules confirm are kept. Returns saved count."""
    root = alerts_root(cfg)
    chats = {getattr(c, 'id', None): c for c in getattr(result, 'chats', [])}
    saved = 0
    for m in getattr(result, 'messages', []):
        chat = chats.get(getattr(getattr(m, 'peer_id', None), 'channel_id', None))
        name = getattr(chat, 'username', None)
        text = getattr(m, 'message', '') or ''
        if not name or not text:
            continue
        hits = match_text(patterns, text)
        stats.record(bool(hits))
        if not hits:
            continue
        seen = disc['seen'].setdefault(name, [])
        if m.id in seen:
            continue
        seen.append(m.id)
        del seen[:-SEEN_PER_CHANNEL]
        e = _note_channel(disc, name, getattr(chat, 'title', ''), 'posts', now)
        e['hits'] += 1
        if save_message(root, name, m.id, m.date, text, hits):
            saved += 1
            logger.info('Found hit for %s in @%s/%d (via Telegram search for "%s")', ', '.join(hits), name, m.id, term)
    return saved


def process_name_results(result, disc, now):
    """Public channels and groups whose name matched the search words."""
    n = 0
    for chat in getattr(result, 'chats', []):
        name = getattr(chat, 'username', None)
        if name and (getattr(chat, 'broadcast', False) or getattr(chat, 'megagroup', False)):
            _note_channel(disc, name, getattr(chat, 'title', ''), 'name', now)
            n += 1
    return n


def handle_messages(cfg, patterns, channel, messages, state):
    """messages: iterable of (id, datetime, text), oldest first. Saves matches, returns (checked, matched)."""
    root = alerts_root(cfg)
    checked = matched = 0
    for msg_id, when, text in messages:
        state[channel] = max(state.get(channel, 0), msg_id)
        if not text:
            continue
        checked += 1
        hits = match_text(patterns, text)
        stats.record(bool(hits))
        if hits:
            if save_message(root, channel, msg_id, when, text, hits) is None:
                continue
            matched += 1
            logger.info('Found hit for %s in @%s/%d', ', '.join(hits), channel, msg_id)
    return checked, matched


async def make_client(session_path):
    from telethon import TelegramClient
    api_id = os.environ.get('TG_API_ID', '').strip()
    api_hash = os.environ.get('TG_API_HASH', '').strip()
    if not api_id.isdigit() or not api_hash:
        raise SystemExit('TG_API_ID / TG_API_HASH are not set. Fill /etc/tgwatch/credentials.env first.')
    return TelegramClient(session_path, int(api_id), api_hash)


async def do_login(cfg):
    client = await make_client(cfg_session(cfg))
    await client.start()   # asks for phone, the code from the Telegram app, and a 2FA password if set
    me = await client.get_me()
    print('Logged in as %s. The session is saved; you can now start the service.' % (me.username or me.first_name))
    os.chmod(cfg_session(cfg) + '.session', 0o600)
    await client.disconnect()


def cfg_session(cfg):
    return (cfg.get('telegram') or {}).get('session') or '/var/lib/tgwatch/tg'


async def discover_round(client, cfg, patterns, stop):
    """Ask Telegram's own search for each term: public posts (kept if our rules confirm) and channel names."""
    from telethon import errors
    from telethon.tl import functions, types
    path = discovered_path(cfg)
    disc = load_discovered(path)
    now = int(time.time())
    interval = int(cfg.get('discover-interval') or DISCOVER_INTERVAL)
    if now - int(disc.get('last_round', 0)) < interval:
        return
    terms = []
    for item in cfg.get('search') or []:
        q = discovery_query(item) if isinstance(item, dict) else ''
        if q and q.lower() not in [t.lower() for t in terms]:
            terms.append(q)
    if not terms:
        logger.info('Discovery: no search term can be used as plain Telegram search words (add a "query:" to a term).')
    saved = found = 0
    for q in terms[:MAX_DISCOVER_TERMS]:
        if stop.is_set():
            break
        try:
            res = await client(functions.messages.SearchGlobalRequest(
                q=q, filter=types.InputMessagesFilterEmpty(), min_date=None, max_date=None,
                offset_rate=0, offset_peer=types.InputPeerEmpty(), offset_id=0, limit=50))
            saved += process_post_results(cfg, patterns, q, res, disc, now)
            await asyncio.sleep(DISCOVER_PAUSE)
            res = await client(functions.contacts.SearchRequest(q=q, limit=20))
            found += process_name_results(res, disc, now)
        except errors.FloodWaitError as e:
            wait = min(int(e.seconds) + 5, MAX_FLOOD_WAIT)
            logger.warning('Telegram asked us to slow down during discovery: waiting %ds', wait)
            await asyncio.sleep(wait)
            break
        except Exception as e:
            logger.warning('Discovery search for "%s" failed: %s', q, e)
        await asyncio.sleep(DISCOVER_PAUSE)
    disc['last_round'] = now
    save_discovered(path, disc)
    logger.info('Discovery round done: %d search words, %d new matches saved, %d channels by name', len(terms[:MAX_DISCOVER_TERMS]), saved, found)


async def do_run(config_path):
    from telethon import errors
    cfg = read_config(config_path)
    client = await make_client(cfg_session(cfg))
    await client.connect()
    if not await client.is_user_authorized():
        logger.error('Not logged in. Run the login step once: tgwatch-login')
        await client.disconnect()
        sys.exit(2)
    wake = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGHUP, wake.set)   # config saved in the page
    stop = asyncio.Event()

    def _stop():
        stop.set()
        wake.set()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, _stop)
    logger.info('tgwatch started')
    try:
        while not stop.is_set():
            cfg = read_config(config_path)
            patterns = compile_patterns(cfg.get('search'))
            channels = [c for c in (normalise_channel(x) for x in cfg.get('channels') or []) if c]
            state_path = os.path.join(os.path.dirname(alerts_root(cfg).rstrip('/')), 'state.json')
            state = load_state(state_path)
            if not channels:
                logger.info('No channels listed yet. Add some in the page (Settings).')
            if not patterns:
                logger.info('No search terms yet. Add some in the page (Settings).')
            for ch in channels if patterns else []:
                if stop.is_set():
                    break
                try:
                    first = ch not in state
                    if first:
                        msgs = await client.get_messages(ch, limit=FIRST_RUN_BACKFILL)
                        msgs = list(reversed(msgs))
                    else:
                        msgs = [m async for m in client.iter_messages(ch, min_id=state[ch], limit=PER_ROUND_LIMIT, reverse=True)]
                    rows = [(m.id, m.date, (m.message or '')) for m in msgs]
                    n, hit = handle_messages(cfg, patterns, ch, rows, state)
                    if first and not rows:
                        state[ch] = 0
                    save_state(state_path, state)
                    logger.info('@%s: %d new messages checked, %d matched', ch, n, hit)
                except errors.FloodWaitError as e:
                    wait = min(int(e.seconds) + 5, MAX_FLOOD_WAIT)
                    logger.warning('Telegram asked us to slow down: waiting %ds', wait)
                    await asyncio.sleep(wait)
                except (errors.ChannelPrivateError, errors.UsernameNotOccupiedError, errors.UsernameInvalidError, ValueError) as e:
                    logger.warning('@%s skipped (%s). Is it a public channel?', ch, type(e).__name__)
                except Exception as e:   # keep going on odd network errors
                    logger.warning('@%s failed: %s', ch, e)
                await asyncio.sleep(PAUSE_BETWEEN)
            if patterns and cfg.get('discover', True) and not stop.is_set():
                try:
                    await discover_round(client, cfg, patterns, stop)
                except Exception as e:
                    logger.warning('Discovery failed: %s', e)
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), timeout=int(cfg.get('poll-interval') or DEFAULT_POLL))
            except asyncio.TimeoutError:
                pass
    finally:
        logger.info('tgwatch stopping')
        await client.disconnect()


def main(argv=None):
    p = argparse.ArgumentParser(description='Read-only Telegram keyword watcher')
    p.add_argument('-c', '--config', default='tgwatch.yaml')
    p.add_argument('command', choices=['run', 'login'])
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'login':
        asyncio.run(do_login(read_config(args.config)))
    else:
        asyncio.run(do_run(args.config))


if __name__ == '__main__':
    main()
