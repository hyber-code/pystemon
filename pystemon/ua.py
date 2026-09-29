import logging.handlers
import random
import requests
import threading
import traceback
from requests.adapters import HTTPAdapter

from pystemon.exception import PystemonKillReceived

logger = logging.getLogger('pystemon')

# Upper bound for one downloaded page/paste, in bytes. Pastes are written by
# strangers, so never read an unbounded amount into memory.
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
# Retry-After is honoured, but never for longer than this many seconds.
MAX_RETRY_AFTER = 3600
# Exponential backoff for server errors: BASE * 2^(n-1), capped.
BACKOFF_BASE = 5
BACKOFF_CAP = 300


class PystemonTooLarge(Exception):
    pass


# https://requests.readthedocs.io/en/master/user/advanced/#transport-adapters
class PystemonAdapter(HTTPAdapter):
    def __init__(self, ip_addr='', *args, **kwargs):
        self._source_address = ip_addr
        super(PystemonAdapter, self).__init__(*args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs['source_address'] = (self._source_address, 0)
        super(PystemonAdapter, self).init_poolmanager(connections, maxsize, block, **pool_kwargs)


def parse_retry_after(value, default=60):
    """Seconds to wait according to a Retry-After header (delta-seconds form)."""
    try:
        wait = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(1, min(wait, MAX_RETRY_AFTER))


def backoff_seconds(attempt):
    """attempt is 1 for the first retry."""
    return min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (max(1, attempt) - 1)))


class PystemonUA():

    def __init__(self, name, proxies_list, user_agents_list=None,
                 retries_client=2, retries_server=8,
                 throttler=None, ip_addr=None,
                 connection_timeout=3.05, read_timeout=10,
                 max_bytes=DEFAULT_MAX_BYTES):
        self.name = "user-agent" + name
        self.user_agents_list = user_agents_list or []
        self.proxies_list = proxies_list
        self.retries_client = retries_client
        self.retries_server = retries_server
        self.throttler = throttler
        self.ip_addr = ip_addr
        self.connection_timeout = connection_timeout
        self.read_timeout = read_timeout
        self.max_bytes = max_bytes
        self.condition = threading.Condition()
        self.kill_received = False
        logger.debug("{} initialized".format(self.name))

    def stop(self):
        with self.condition:
            self.kill_received = True
            self.condition.notify_all()

    def get_bound_session(self):
        session = requests.Session()
        if self.ip_addr:
            logger.debug("{}: binding to source IP '{}'".format(self.name, self.ip_addr))
            session.mount('http://', PystemonAdapter(self.ip_addr))
            session.mount('https://', PystemonAdapter(self.ip_addr))
        return session

    def get_random_user_agent(self):
        if self.user_agents_list:
            return random.choice(self.user_agents_list)
        return 'pystemon'

    def _read_capped(self, response):
        """Read the body, refusing anything bigger than max_bytes."""
        chunks = []
        total = 0
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if self.max_bytes and total > self.max_bytes:
                response.close()
                raise PystemonTooLarge("more than {} bytes".format(self.max_bytes))
            chunks.append(chunk)
        response._content = b''.join(chunks)
        response._content_consumed = True

    def __parse_http__(self, url, session, random_proxy):
        """Turn one HTTP exchange into a decision for download_url()."""
        logger.debug("{}: Parsing response for url '{}'".format(self.name, url))
        response = session.get(url, stream=True, timeout=(self.connection_timeout, self.read_timeout))
        status = response.status_code
        if status < 400:
            try:
                self._read_capped(response)
            except PystemonTooLarge as e:
                logger.warning("{}: {} is too large ({}), skipping".format(self.name, url, e))
                return {'abort': True}
            return {'response': response}
        # error statuses: decide whether and how long to wait before retrying
        if random_proxy and self.proxies_list:
            self.proxies_list.failed_proxy(random_proxy)
        if status == 429:
            wait = parse_retry_after(response.headers.get('Retry-After'))
            logger.warning("{}: 429 (too many requests) for {}, waiting {}s as requested".format(self.name, url, wait))
            return {'loop_server': True, 'wait': wait}
        if status in (404, 410):
            logger.warning("{}: {} for {}".format(self.name, status, url))
            return {'loop_client': True, 'wait': 5}
        if status == 403:
            try:
                body = response.text[:2000].lower()
            except Exception:
                body = ''
            if 'slow down' in body or 'blocked' in body:
                wait = parse_retry_after(response.headers.get('Retry-After'), default=300)
                logger.warning("{}: blocked / slow down message for {}, waiting {}s".format(self.name, url, wait))
                return {'loop_server': True, 'wait': wait}
            logger.warning("{}: 403 for {}, aborting".format(self.name, url))
            return {'abort': True}
        if status >= 500:
            logger.warning("{}: server error {} for {}".format(self.name, status, url))
            return {'loop_server': True, 'backoff': True}
        logger.warning("{}: HTTP {} for {}, aborting".format(self.name, status, url))
        return {'abort': True}

    def __download_url__(self, url, session, random_proxy):
        try:
            with self.condition:
                if self.kill_received:
                    raise PystemonKillReceived("download request cancelled")
            return self.__parse_http__(url, session, random_proxy)
        except PystemonKillReceived as e:
            logger.debug("{}: {}".format(self.name, e))
            return {'abort': True}
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            logger.warning("{}: connection problem for {}: {}".format(self.name, url, e))
            if random_proxy and self.proxies_list:
                self.proxies_list.failed_proxy(random_proxy)
            return {'loop_server': True, 'backoff': True}
        except Exception as e:
            logger.warning("{}: unexpected error for {}: {}".format(self.name, url, e))
            logger.debug(traceback.format_exc())
            if random_proxy and self.proxies_list:
                self.proxies_list.failed_proxy(random_proxy)
            return {'loop_server': True, 'backoff': True}

    def download_url(self, url, data=None, cookie=None, wait=0):
        response = None
        loop_client = 0
        loop_server = 0
        logger.debug("{}: download_url: about to fetch url '{}'".format(self.name, url))
        while (response is None) and (loop_client < self.retries_client) and (loop_server < self.retries_server):
            try:
                if self.throttler is not None and self.throttler.is_alive():
                    # wait until the throttler allows us to download
                    self.throttler.wait()
                session = self.get_bound_session()
                random_proxy = None
                if self.proxies_list:
                    random_proxy = self.proxies_list.get_random_proxy()
                    if random_proxy:
                        session.proxies = {'http': random_proxy, 'https': random_proxy}
                session.headers.update({'User-Agent': self.get_random_user_agent(), 'Accept-Charset': 'utf-8'})
                if cookie:
                    session.headers.update({'Cookie': cookie})
                if data:
                    session.headers.update(data)
            except Exception as e:
                logger.error("ERROR: unable to initialize session, aborting: {}".format(e))
                return None
            if wait > 0:
                logger.debug("{}: waiting {}s before retrying {}".format(self.name, wait, url))
                with self.condition:
                    if self.kill_received:
                        break
                    self.condition.wait(wait)
            if (loop_client > 0) or (loop_server > 0):
                logger.warning("{name}: retry client={lc}/{tc}, server={ls}/{ts} for {url}".format(
                    name=self.name, lc=loop_client, tc=self.retries_client,
                    ls=loop_server, ts=self.retries_server, url=url))
            res = self.__download_url__(url, session, random_proxy)
            response = res.get('response', None)
            if res.get('abort', False):
                break
            if res.get('loop_client', False):
                loop_client += 1
            if res.get('loop_server', False):
                loop_server += 1
            if res.get('backoff', False):
                wait = backoff_seconds(loop_server)
            else:
                wait = res.get('wait', 0)

        if response is None:
            if loop_client >= self.retries_client:
                logger.error("{}: too many client errors, giving up on {}".format(self.name, url))
            elif loop_server >= self.retries_server:
                logger.error("{}: too many server errors, giving up on {}".format(self.name, url))
            else:
                logger.error("{}: giving up on {}".format(self.name, url))
        return response
