pystemon
========
Monitoring tool for PasteBin-alike sites written in Python

Copyleft AGPLv3 - Christophe Vandeplas - christophe@vandeplas.com  
Feel free to use the code, but please share the changes you've made by doing Pull Requests! 

Features:
---------
* search for regular expressions in pasties
* flexible design, minimal effort to add another paste* site
* use custom download functions for complex pastie sites
* uses multiple threads per unique site to download the pastes
* waits a random time (within a range) before downloading the latest pastes, time customizable per site
* (optional) only trigger on X hits in the same pastie
* (optional) exclude matching pasties if exclusion regex matches
* (optional) allow additional email recipients per search pattern
* (optional) uses random User-Agents
* (optional) uses random proxies
* removes a proxy if it is unreliable (fails 5 times)
* (optional) compress saved files with Gzip. (no zip to limit external dependencies)
* can run as daemon
* multitude of outputs: file, email, sqlite, redis, mongodb, telegram

Python Dependencies
-------------------
Python dependencies can be installed with: `pip3 -r requirements.txt`.
Optional ones are:
* PyMongo (For Mongodb support)
* redis (For redis support)


Usage
------
```
Usage: pystemon.py [options]
Options:
      -h, --help            show this help message and exit  
      -c FILE, --config=FILE  
                            load configuration from file  
      -d, --daemon          runs in background as a daemon  
      -k, --kill            kill pystemon daemon
      -s, --stats           display statistics about the running threads (NOT IMPLEMENTED)    
      -v                    outputs more information  

Default configuration file: /etc/pystemon.yaml or pystemon.yaml in current directory
``` 

Pastebin Pro (scraping API)
---------------------------
Pastebin lets Pro accounts read new pastes through a scraping API instead of scraping web pages
([rules](https://pastebin.com/doc_scraping_api)). pystemon has a ready-made setting for it, with a
single switch for the day the rules change:

```yaml
site:
  pastebin.com_pro:
    enable: yes
    preset: pastebin
    mode: scrape          # scrape = the Pro scraping API as it works today
                          # api    = a future full API: set the new URLs and limits in this block
    # bind-ip: '192.168.1.50'  # optional: local address of this machine, only if it has several networks or VPNs
```

What `mode: scrape` does for you, following Pastebin's rules: no proxy (only your whitelisted IP works),
at most one request per second, one poll of the list per minute, a limit of at most 250 pastes, JSON parsing
of the list, and the public link in alerts points to the normal paste page, never to `/raw/*`.
pystemon refuses to start if the free `pastebin.com` site (which scrapes `/raw/*`) is enabled at the same time,
because Pastebin blocks an IP that does both.

Options in the same block: `limit`, `lang` (Pastebin's own language filter), `metadata: yes`,
`min-size`, `max-size`, `syntax-include` and `syntax-exclude` (paste lists are filtered before anything is
downloaded, so unwanted pastes cost no request).

Every value can be overridden in the block, so switching to another mode never needs a code change.

Behaviour worth knowing
-----------------------
* HTTP 429 and `Retry-After` are honoured; server errors are retried with a growing delay, never in a tight loop.
* A single download is limited to 5 MB (`max-size` per site changes it).
* `network: ip:` (or `bind-ip:` per site) really binds downloads to that source address.
* Regex flags in the config (`regex-flags`) accept only names such as `re.I | re.M`.

Tests: `pip install -r requirements-dev.txt && python -m pytest tests`.

Docker
------
Render docker image with:
```
docker build -t cvandeplas/pystemon:latest .
```

Run it (the image runs as a normal user, not root; mount your config and a data folder,
and point `dir` and `dir-all` in the config at `/data`):
```
docker run -d --name pystemon --restart unless-stopped \
  -v $PWD/pystemon.yaml:/opt/pystemon/pystemon.yaml:ro \
  -v $PWD/data:/data \
  cvandeplas/pystemon:latest
```
Make the `data` folder writable for the container user (`chown 100:101 data` on the Alpine image, or run
`docker run --rm cvandeplas/pystemon id`-style checks to see the ids).
