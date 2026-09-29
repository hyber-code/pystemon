'''
Ready-made settings for sites that have their own rules.

A site in pystemon.yaml can say

    preset: pastebin
    mode: scrape          # or: api

and only add what is specific to the user (bind-ip, filters ...). Every key the
user writes still overrides the preset, so nothing here is hard to change.

mode: scrape   follows Pastebin's documented rules for the Pro scraping API
               (https://pastebin.com/doc_scraping_api):
               * one whitelisted IP: no proxies, bind to that IP
               * at most 1 request per second, poll the list about once a minute
               * limit at most 250 (default 50)
               * never fetch /raw/* pages from the whitelisted IP
mode: api      for the day a full API is allowed. Today it starts from the same
               values; set download-url, archive-url, limit, throttling ... in
               the site block to match the new rules. Limits above are then NOT
               enforced, so the person changing the mode is in charge.
'''
import difflib
import logging.handlers

from pystemon.exception import PystemonConfigException
from pystemon.pastebin_syntaxes import PASTEBIN_SYNTAXES

logger = logging.getLogger('pystemon')

PASTEBIN_MAX_LIMIT = 250
PASTEBIN_MIN_INTERVAL = 60      # seconds between two polls of the list
PASTEBIN_MIN_THROTTLING = 1000  # milliseconds between two requests


def _check_syntaxes(conf):
    '''Warn about syntax names Pastebin does not list (a typo silently gives an empty feed).'''
    values = []
    if conf.get('lang'):
        values.append(('lang', str(conf['lang'])))
    for key in ('syntax-include', 'syntax-exclude'):
        for v in conf.get(key) or []:
            values.append((key, str(v)))
    for key, v in values:
        if v.lower() not in PASTEBIN_SYNTAXES:
            hint = difflib.get_close_matches(v.lower(), PASTEBIN_SYNTAXES, n=3)
            logger.warning("preset pastebin: {} '{}' is not in Pastebin's syntax list{}".format(
                key, v, " (did you mean: {}?)".format(', '.join(hint)) if hint else ''))


def _pastebin(conf, mode):
    strict = (mode == 'scrape')
    out = {}
    _check_syntaxes(conf)
    limit = int(conf.get('limit', 100))
    if strict and limit > PASTEBIN_MAX_LIMIT:
        logger.warning("preset pastebin: limit {} is above the documented maximum, using {}".format(limit, PASTEBIN_MAX_LIMIT))
        limit = PASTEBIN_MAX_LIMIT
    if limit < 1:
        raise PystemonConfigException("preset pastebin: limit must be at least 1")
    url = 'https://scrape.pastebin.com/api_scraping.php?limit={}'.format(limit)
    if conf.get('lang'):
        url += '&lang={}'.format(str(conf['lang']).lower())
    out['archive-url'] = url
    out['archive-format'] = 'json'
    out['archive-json-key'] = 'key'
    out['download-url'] = 'https://scrape.pastebin.com/api_scrape_item.php?i={id}'
    # the link people click in alerts; it is never downloaded by pystemon
    out['public-url'] = 'https://pastebin.com/{id}'
    if conf.get('metadata', False):
        out['metadata-url'] = 'https://scrape.pastebin.com/api_scrape_item_meta.php?i={id}'
    out['update-min'] = 60
    out['update-max'] = 75
    out['throttling'] = 1000
    out['use-proxy'] = False
    out['max-size'] = 1024 * 1024
    return out


PRESETS = {
    'pastebin': {'scrape': _pastebin, 'api': _pastebin},
}


def apply_preset(site_name, conf):
    """Return conf merged over the preset it asks for (or conf itself)."""
    preset = conf.get('preset')
    if not preset:
        return dict(conf)
    mode = str(conf.get('mode', 'scrape')).lower()
    if preset not in PRESETS:
        raise PystemonConfigException("site {}: unknown preset '{}' (known: {})".format(
            site_name, preset, ', '.join(sorted(PRESETS))))
    if mode not in PRESETS[preset]:
        raise PystemonConfigException("site {}: unknown mode '{}' for preset {} (known: {})".format(
            site_name, mode, preset, ', '.join(sorted(PRESETS[preset]))))
    merged = PRESETS[preset][mode](conf, mode)
    merged.update({k: v for k, v in conf.items()})
    # keep the URLs the preset built from limit/lang unless the user wrote them
    for key in ('archive-url', 'download-url', 'public-url', 'metadata-url', 'archive-format', 'archive-json-key'):
        if key not in conf and key in merged:
            merged[key] = PRESETS[preset][mode](conf, mode)[key]
    if preset == 'pastebin' and mode == 'scrape':
        # the documented rules cannot be loosened by accident
        if merged.get('use-proxy'):
            raise PystemonConfigException("site {}: the Pastebin scraping API only accepts your whitelisted IP, "
                                          "use-proxy must be no".format(site_name))
        if int(merged.get('update-min', 0)) < PASTEBIN_MIN_INTERVAL:
            logger.warning("site {}: update-min raised to {}s (Pastebin caches the list and asks for about one poll a minute)".format(
                site_name, PASTEBIN_MIN_INTERVAL))
            merged['update-min'] = PASTEBIN_MIN_INTERVAL
        if int(merged.get('update-max', 0)) < merged['update-min']:
            merged['update-max'] = merged['update-min'] + 15
        if int(merged.get('throttling', 0)) < PASTEBIN_MIN_THROTTLING:
            logger.warning("site {}: throttling raised to {}ms (at most 1 request per second)".format(
                site_name, PASTEBIN_MIN_THROTTLING))
            merged['throttling'] = PASTEBIN_MIN_THROTTLING
    if mode == 'api':
        logger.warning("site {}: mode api starts from the scrape endpoints; set the URLs and limits "
                       "of the new API in the site block".format(site_name))
    return merged
