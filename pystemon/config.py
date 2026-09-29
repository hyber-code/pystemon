import logging.handlers
import yaml
import importlib
import threading

try:
    from queue import Queue
except ImportError:
    from Queue import Queue

from pystemon.sendmail import PystemonSendmail
from pystemon.storage import PastieStorage
from pystemon.proxy import ProxyList
from pystemon.pastiesearch import PastieSearch
from pystemon.exception import PystemonConfigException
from pystemon.presets import apply_preset

logger = logging.getLogger('pystemon')

class SiteConfig():
    def __init__(self, name, config):
        self.name = name
        self._queue = None
        self.preset = config.get('preset')
        self.mode = config.get('mode')
        config = apply_preset(name, config)
        self.download_url = config['download-url']
        self.archive_url = config['archive-url']
        self.archive_format = str(config.get('archive-format', 'regex')).lower()
        if self.archive_format not in ('regex', 'json'):
            raise PystemonConfigException("site {}: archive-format must be regex or json".format(name))
        self.archive_json_key = config.get('archive-json-key', 'key')
        # the regex is only needed when the archive page is HTML
        self.archive_regex = config.get('archive-regex')
        if self.archive_format == 'regex' and not self.archive_regex:
            raise PystemonConfigException("site {}: archive-regex is required".format(name))
        self.use_proxy = bool(config.get('use-proxy', True))
        self.bind_ip = config.get('bind-ip')
        self.max_bytes = int(config.get('max-size', 5 * 1024 * 1024))
        self.listing_filter = {
            'min-size': int(config.get('min-size', 0) or 0),
            'max-size': int(config.get('max-size', 0) or 0) if self.preset else 0,
            'syntax-include': [str(x).lower() for x in config.get('syntax-include', [])],
            'syntax-exclude': [str(x).lower() for x in config.get('syntax-exclude', [])],
        }
        self.throttling = config.get('throttling', 0)
        self.public_url= config.get('public-url')
        self.metadata_url = config.get('metadata-url')
        self.update_min = config.get('update-min', 10)
        self.update_max = config.get('update-max', 30)
        self.pastie_classname = config.get('pastie-classname')

    @property
    def queue(self):
        if self._queue is None:
            logger.debug("{}: initializing with empty Queue".format(repr(self)))
            self._queue = Queue()
        return self._queue

    @queue.setter
    def queue(self, q):
        logger.debug("{}: inheriting queue of size={}".format(repr(self), q.qsize()))
        self._queue = q

    def __str__(self):
        return '''SiteConfig[{}]:
        download url: {}
        archive url:  {}
        public url:   {}
        metadata url: {}
        pastie class: {}
        '''.format(self.name, self.download_url, self.archive_url,
                self.public_url, self.metadata_url, self.pastie_classname)

    def __repr__(self):
        return "SiteConfig[{}]".format(self.name)

    def __eq__(self, other):
        res = False
        try:
            res = ( isinstance(other, SiteConfig) and (self.download_url == other.download_url)
                    and
                    (self.archive_url == other.archive_url)
                    and
                    (self.public_url == other.public_url)
                    and
                    (self.metadata_url == other.metadata_url)
                    and
                    (self.archive_format == other.archive_format)
                    and
                    (self.use_proxy == other.use_proxy)
                    and
                    (self.bind_ip == other.bind_ip)
                    and
                    (self.pastie_classname == other.pastie_classname) )
        except Exception as e:
            logger.error("Unable to compare SiteConfig instances: {}".format(e))
            pass
        return res

    def __hash(self):
        return self.name.__hash__()

# TODO verify validity of all config parameters
class PystemonConfig():
    def __init__(self, configfile, debug):
        self.debug = debug
        self.lock = threading.Lock()
        self._configfile = configfile
        self._yamlconfig = None
        self._pidfile = None
        self._ip_addr = None
        self._sendmail = None
        self._user_agents_list = None
        self._storage_engines = None
        self._proxies_list = None
        self._re_module = None
        self._save_thread = False
        self._patterns = []
        self._threads = 1
        self._sites = []
        self._save_dir = None
        self._archive_dir = None
        self._compress = False
        self._reload_count = 0
        self._max_throttling = 0
        self._preload()

    def is_same_as(self, other):
        # TODO check if config changed
        res = False
        try:
            res = self._configfile == other._configfile
        except Exception:
            pass
        return res

    @property
    def pidfile(self):
        with self.lock:
            return self._pidfile

    @property
    def save_thread(self):
        with self.lock:
            return self._save_thread

    @property
    def save_dir(self):
        with self.lock:
            return self._save_dir

    @property
    def archive_dir(self):
        with self.lock:
            return self._archive_dir

    @property
    def compress(self):
        with self.lock:
            return self._compress

    @property
    def threads(self):
        with self.lock:
            return self._threads

    @property
    def ip_addr(self):
        with self.lock:
            return self._ip_addr

    @property
    def sendmail(self):
        with self.lock:
            return self._sendmail

    @property
    def user_agents_list(self):
        with self.lock:
            return self._user_agents_list

    @property
    def storage_engines(self):
        with self.lock:
            return self._storage_engines

    @property
    def proxies_list(self):
        with self.lock:
            return self._proxies_list

    @property
    def configfile(self):
        with self.lock:
            return self._configfile

    @property
    def re_module(self):
        with self.lock:
            return self._re_module

    @property
    def patterns(self):
        with self.lock:
            return self._patterns

    @property
    def sites(self):
        with self.lock:
            return self._sites

    @property
    def max_throttling(self):
        with self.lock:
            return self._max_throttling

    def reload(self):
        try:
            with self.lock:
                if self._reload_count:
                    logger.debug("reloading configuration file '{0}'".format(self._configfile))
                    self._yamlconfig = None
                else:
                    logger.debug("loading configuration file '{0}'".format(self._configfile))
                self._reload_count = self._reload_count + 1
                self._preload()
                config = self._reload()
                self._ip_addr = config.get('ip_addr')
                self._sendmail = config.get('sendmail')
                self._save_thread = config.get('save_thread')
                self._user_agents_list = config.get('user_agents_list')
                self._storage_engines = config.get('storage_engines')
                self._save_dir = config.get('save_dir')
                self._archive_dir = config.get('archive_dir')
                self._compress = config.get('compress')
                self._proxies_list = config.get('proxies_list')
                self._re_module = config.get('re_module')
                self._patterns = config.get('patterns')
                self._sites = config.get('sites')
                self._threads = config.get('threads')
                self._pidfile = config.get('pidfile')
                self._max_throttling = 0
                for site in self._sites:
                    if self._max_throttling < site.throttling:
                        self._max_throttling = site.throttling
        except PystemonConfigException:
            raise
        except Exception as e:
            raise PystemonConfigException('Unable to parse configuration: {}'.format(e))
        logger.debug("configuration loaded")
        return True

    def _preload(self):
        if self._yamlconfig is None:
            logger.debug("pre-loading config file '{}'".format(self._configfile))
            self._yamlconfig = self._load_yamlconfig(self._configfile)
            try:
                self._pidfile = self._yamlconfig['pid']['filename']
            except KeyError:
                pass

    def _reload(self):
        logger.debug("parsing yaml configuration from file '{}'".format(self._configfile))
        config = {}
        yamlconfig = self._yamlconfig
        try:
            if yamlconfig['proxy']['random']:
                config['proxies_list'] = ProxyList(yamlconfig['proxy']['file'])
        except KeyError:
            pass

        config['save_thread'] = yamlconfig.get('save-thread', False)

        uaconfig = yamlconfig.get('user-agent', {})
        if uaconfig.get('random', False):
            try:
                config['user_agents_list'] = self._load_user_agents_from_file(yamlconfig['user-agent']['file'])
            except KeyError:
                raise PystemonConfigException('random user-agent requested but no file provided')

        # source IP to use for all downloads (a site can override it with bind-ip)
        config['ip_addr'] = (yamlconfig.get('network') or {}).get('ip') or None
        if config['ip_addr'] is None:
            logger.debug("Using default IP address")

        config['sendmail'] = self._load_email(yamlconfig)
        res = self._load_storage_engines(yamlconfig)
        config['storage_engines'] = res['engines']
        config['save_dir'] = res['save_dir']
        config['archive_dir'] = res['archive_dir']
        config['compress'] = res['compress']
        config['re_module'] = self._load_regex_engine(yamlconfig)
        config['patterns'] = self._compile_regex(yamlconfig, config['re_module'])
        try:
            config['threads'] = int(yamlconfig.get('threads', 1))
            if config['threads'] < 1:
                raise Exception("minimum acceptable value is 1")
        except Exception as e:
            logger.error("invalid threads value specified: {0}".format(e))
            config['threads'] = 1
            pass

        config['sites'] = self._load_sites(yamlconfig)

        if not self.debug and 'logging-level' in yamlconfig:
            if yamlconfig['logging-level'] in ['NOTSET', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']:
                logger.setLevel(logging.getLevelName(yamlconfig['logging-level']))
            else:
                logger.error("logging level \"%s\" is invalid" % (yamlconfig['logging-level']))

        logger.debug("yaml configuration parsed")
        return config

    def _recent_pyyaml(self):
        res = False
        try:
            version = yaml.__version__.split('.')
            if int(version[0]) >= 5:
                if int(version[1]) >= 1:
                    res = True
        except Exception as e:
            logger.debug("unable to parse PyYaml version: {}".format(e))
        return res

    def _load_yamlconfig(self, configfile):
        yamlconfig = None
        try:
            if self._recent_pyyaml():
                # https://github.com/yaml/pyyaml/wiki/PyYAML-yaml.load(input)-Deprecation
                # only for 5.1+
                yamlconfig = yaml.load(open(configfile), Loader=yaml.SafeLoader)
            else:
                yamlconfig = yaml.safe_load(open(configfile))
        except yaml.YAMLError as exc:
            logger.error("Error in configuration file {0}:".format(configfile))
            if hasattr(exc, 'problem_mark'):
                mark = exc.problem_mark
                raise PystemonConfigException("error position: (%s:%s)" % (mark.line + 1, mark.column + 1))
        for includes in yamlconfig.get("includes", []):
            try:
                logger.debug("loading include '{0}'".format(includes))
                yamlconfig.update(yaml.safe_load(open(includes)))
            except Exception as e:
                raise PystemonConfigException("failed to load '{0}': {1}".format(includes, e))
        return yamlconfig


    def _load_user_agents_from_file(self, filename):
        user_agents_list = []
        logger.debug('Loading user-agent from file "{file}" ...'.format(file=filename))
        with open(filename) as f:
            for line in f:
                line = line.strip()
                if line:
                    user_agents_list.append(line)
        if not len(user_agents_list) > 0:
            raise PystemonConfigException("found zero valid UserAgents")
        logger.debug("Found {count} UserAgents in file '{file}'".format(file=filename, count=len(user_agents_list)))
        return user_agents_list

    def _load_email(self, yamlconfig):
        sendmail = None
        email=yamlconfig.get('email', {})
        if email.get('alert'):
            logger.debug('loading email configuration')
            sendmail = PystemonSendmail(email['from'], email['to'], email['subject'],
                    server=email.get('server', '127.0.0.1'),
                    port=email.get('port', 25),
                    tls=email.get('tls', False),
                    username=email.get('username'),
                    password=email.get('password'),
                    size_limit=email.get('size-limit', 1024*1024))
            logger.debug("alert emails will be sent to '{0}' from '{1}' via '{2}'".format(sendmail.mailto, sendmail.mailfrom, sendmail.server))
        return sendmail

    def _load_storage_engines(self, yamlconfig):
        # initialize storage backends
        storage_engines = []
        storage_yamlconfig = yamlconfig.get('storage', {})
        save_dir = None
        archive_dir = None
        storage_file = None
        compress = False
        # file storage is the default and should be initialized first to set save_dir and archive_dir
        try:
            storage_file = PastieStorage.load_storage('archive', **storage_yamlconfig.pop('archive'))
            if storage_file is not None:
                save_dir = storage_file.save_dir
                archive_dir = storage_file.archive_dir
                compress = storage_file.compress
                storage_engines.append(storage_file)
        except KeyError:
            raise PystemonConfigException('archive was not found under storage, old pystemon.yaml config?')

        for storage in storage_yamlconfig.keys():
            engine = PastieStorage.load_storage(storage, save_dir=save_dir, archive_dir=archive_dir,
                    **storage_yamlconfig[storage])
            if engine is not None:
                storage_engines.append(engine)
        return {'save_dir': save_dir, 'archive_dir': archive_dir, 'compress': compress, 'engines': storage_engines}

    def _load_regex_engine(self, yamlconfig):
        # load the regular expression engine
        engine = yamlconfig.get('engine', 're')
        re_module = None
        if not engine in ['re', 'regex']:
            raise PystemonConfigException("only 're' or 'regex' supported, not '{0}'".format(engine))
        try:
            logger.debug("Loading regular expression engine '{0}'".format(engine))
            re_module=importlib.import_module(engine)
            if engine == 'regex':
                logger.debug("Setting regex DEFAULT_VERSION to VERSION1")
                re_module.DEFAULT_VERSION = re_module.VERSION1
        except ImportError:
            raise PystemonConfigException("unable to import module '{0}'".format(engine))
        return re_module

    def _compile_regex(self, yamlconfig, re_module):
        patterns = []
        # compile all search patterns
        strict = yamlconfig.get('strict_regex', False)
        regexes = yamlconfig['search']
        logger.debug("compiling {} regexes ...".format(len(regexes)))
        for regex in regexes:
            try:
                search = regex['search']
                ps = PastieSearch(re_module, regex)
                patterns.append(ps)
            except KeyError:
                if strict:
                    raise PystemonConfigException("Missing search pattern")
                else:
                    logger.error("Error: skipping empty search pattern entry")
            except Exception as e:
                if strict:
                    raise PystemonConfigException("Unable to parse regex '%s': %s" % (search, e))
                else:
                    logger.error("Error: Unable to parse regex '%s': %s" % (search, e))
        logger.debug("successfully compiled {0}/{1} regexes".format(len(patterns), len(regexes)))
        return patterns

    def _load_sites(self, yamlconfig):
        # Build array of enabled sites.
        sites_enabled = []
        count_enabled = 0
        sites = yamlconfig['site']
        logger.debug("loading {} sites ...".format(len(sites)))
        for site in sites:
            if yamlconfig['site'][site].get('enable'):
                logger.info("Site: {} is enabled, adding to pool...".format(site))
                new_site = None
                try:
                    count_enabled = count_enabled + 1
                    new_site = SiteConfig(site, yamlconfig['site'][site])
                    if new_site in self._sites:
                        i = self._sites.index(new_site)
                        logger.debug("found {} in running configuration".format(repr(new_site)))
                        current_site = self._sites[i]
                        logger.debug("matching running site: {}".format(current_site))
                        q = current_site.queue
                        logger.debug("running queue size: {}".format(q.qsize()))
                        new_site.queue = q
                    sites_enabled.append(new_site)
                except Exception as e:
                    logger.error("Unable to add site '{0}': {1}".format(site, e))
            elif yamlconfig['site'][site].get('enable') is False:
                logger.info("Site: {} is disabled.".format(site))
            else:
                logger.warning("Site: {} is not enabled or disabled in config file. We just assume it disabled.".format(site))
        self._check_site_rules(sites_enabled, yamlconfig)
        logger.debug("successfully loaded {0}/{1} enabled site(s)".format(len(sites_enabled), count_enabled))
        return sites_enabled

    def _check_site_rules(self, sites, yamlconfig):
        '''
        Refuse combinations that would get the whitelisted Pastebin IP blocked:
        Pastebin blocks an IP that uses the scraping API AND scrapes its web pages.
        '''
        if not any(s.preset == 'pastebin' for s in sites):
            return
        for s in sites:
            if s.preset == 'pastebin':
                if not (s.bind_ip or (yamlconfig.get('network') or {}).get('ip')):
                    logger.debug("site {}: no bind-ip / network ip set. Fine on a machine with one network; "
                                 "with several (VPN, VMs) set bind-ip to the local address that leaves through "
                                 "your whitelisted public IP.".format(s.name))
                continue
            for url in (s.download_url, s.archive_url):
                host = url.split('//', 1)[-1].split('/', 1)[0].lower()
                if host in ('pastebin.com', 'www.pastebin.com'):
                    raise PystemonConfigException(
                        "site {} scrapes pastebin.com web pages while the Pastebin scraping API is enabled. "
                        "Pastebin blocks the IP for that. Disable one of them.".format(s.name))

