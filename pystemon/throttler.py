import logging.handlers
import threading

try:
    from queue import Queue
    from queue import Empty
except ImportError:
    from Queue import Queue
    from Queue import Empty

logger = logging.getLogger('pystemon')

class ThreadThrottler(threading.Thread):

    def __init__(self, site, throttling):
        threading.Thread.__init__(self)
        self.site = site
        self._throttling = throttling
        self.queue = Queue()
        self.condition = threading.Condition()
        self.kill_received = False

    def __repr__(self):
        with self.condition:
            return 'ThreadThrottler[{}][{}]'.format(self.site, self._throttling)

    def is_same_as(self, other):
        res = False
        try:
            res = ( isinstance(other, ThreadThrottler)
                    and
                    (self.site == other.site)
                    and
                    (self.throttling == other.throttling) )
        except Exception as e:
            logger.error("Unable to compare ThreadThrottler instances: {}".format(e))
            pass
        return res

    @property
    def throttling(self):
        with self.condition:
            return self._throttling

    @throttling.setter
    def throttling(self, throttling):
        with self.condition:
            self._throttling = throttling

    def stop(self):
        with self.condition:
            logger.info('ThreadThrottler[{}] exiting'.format(self.site))
            self.kill_received = True
            self.condition.notify_all()

    def wait(self):
        event = threading.Event()
        event.clear()
        self.queue.put(event)
        event.wait()

    def run(self):
        site = self.site
        logger.info('ThreadThrottler[{}] started'.format(site))
        queue = self.queue
        try:
            while True:
                with self.condition:
                    if self.kill_received:
                        break
                # Do NOT hold the lock while waiting for a request: stop() needs
                # it, and shutdown would otherwise hang until the next download.
                logger.debug("ThreadThrottler[{}]: waiting for a download request ...".format(site))
                try:
                    consumer_lock = queue.get(timeout=1)
                except Empty:
                    continue
                logger.debug("ThreadThrottler[{}]: releasing download request".format(site))
                consumer_lock.set()
                queue.task_done()
                with self.condition:
                    if self.kill_received:
                        break
                    sleeptime = self._throttling / float(1000)
                    logger.debug("ThreadThrottler[{}]: now waiting {} second(s) ...".format(site, sleeptime))
                    self.condition.wait(sleeptime)
        except Exception as e:
            logger.error('ThreadThrottler[{}] crashed: {}'.format(self.site, e))
        logger.debug('ThreadThrottler[{}]: releasing any remaining item from the queue'.format(self.site))
        # only returns the 'approximate' size
        while True:
            try:
                consumer_lock = queue.get(block=False)
                consumer_lock.set()
                queue.task_done()
            except Empty:
                break
            except Exception:
                pass
        logger.info('ThreadThrottler[{}] exited'.format(site))

