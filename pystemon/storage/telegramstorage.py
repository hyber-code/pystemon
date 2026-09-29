
import logging.handlers
import requests
from pystemon.storage import PastieStorage

logger = logging.getLogger('pystemon')

class TelegramStorage(PastieStorage):

    def __init_storage__(self, **kwargs):
        self.token = kwargs.get('token')
        self.chat_id = kwargs.get('chat-id')

    def __save_pastie__(self, pastie):
        if pastie.matched:
            message = '''
I found a hit for a regular expression on one of the pastebin sites.

The site where the paste came from :        {site}
The original paste was located here:        {url}
And the regular expressions that matched:   {matches}

Below (after newline) is the content of the pastie:

{content}

        '''.format(site=pastie.site.name, url=pastie.public_url, matches=pastie.matches_to_regex(), content=pastie.pastie_content.decode('utf8', errors='replace'))

            url = 'https://api.telegram.org/bot{0}/sendMessage'.format(self.token)
            try:
                # never log the URL: it contains the bot token
                logger.debug('Sending message to telegram for pastie_id {}'.format(pastie.id))
                # Telegram refuses messages longer than 4096 characters
                requests.post(url, data={'chat_id': self.chat_id, 'text': message[:4000]}, timeout=15)
            except Exception as e:
                logger.warning("Failed to alert through telegram: {0}".format(e))


