# pystemon guide

pystemon watches paste sites, downloads new pastes and tells you when one contains something you are
looking for (your domain, a leaked key, a keyword). This guide covers the fork
`hyber-code/pystemon`, with the Pastebin Pro scraping API as the main source.

Contents: [1. How it works](#1-how-it-works) | [2. Pastebin Pro setup](#2-pastebin-pro-setup) |
[3. Install: Linux or Proxmox](#3-install-on-linux-or-a-proxmox-container) | [4. Install: Windows](#4-install-on-windows) |
[5. Install: Docker](#5-install-with-docker) | [6. Configuration](#6-configuration-cheat-sheet) |
[7. Alerts](#7-alerts) | [8. Updating](#8-updating) | [9. Troubleshooting](#9-troubleshooting) |
[10. Good manners](#10-good-manners-and-limits)

---

## 1. How it works

1. Every 60 to 75 seconds pystemon asks the site for the list of newest pastes.
2. Pastes it has not seen are downloaded one at a time (for Pastebin, at most one per second).
3. Each paste is checked against your search patterns (regular expressions).
4. Matches are saved to the `alerts` folder and, if you set it up, emailed or sent to Telegram.

Everything is a plain text config file: `pystemon.yaml`.

## 2. Pastebin Pro setup

You need a Pastebin **Pro** account. The scraping API uses **no API key**. It only checks your IP address.

1. Find your public IPv4 address from the machine that will run pystemon:
   `curl -4 https://ifconfig.me` (Windows PowerShell: `curl.exe -4 https://ifconfig.me`)
2. Log in to Pastebin and open <https://pastebin.com/doc_scraping_api>. Put that IP in the whitelist box and
   save. It takes about a minute. Pastebin allows **one** whitelisted IP per account.
3. Check it from the same machine: open `https://scrape.pastebin.com/api_scraping.php?limit=1` in a browser.
   You should see a small block of JSON. An error page means the IP is wrong (or a VPN is on).
4. In `pystemon.yaml` use the ready-made block:

```yaml
site:
  pastebin.com_pro:
    enable: yes
    preset: pastebin
    mode: scrape        # the Pro scraping API as it works today
    limit: 100          # pastes per poll, 1 to 250
    # lang: php         # only one language (Pastebin's own filter)
    # syntax-exclude: [text]
    # max-size: 500000
```

The preset applies Pastebin's published rules for you: no proxy, at most 1 request per second, about one poll a
minute, JSON parsing, and it never touches `/raw/*`. pystemon refuses to start if the free `pastebin.com` entry
(which does scrape `/raw/*`) is enabled at the same time, because Pastebin blocks an IP that does both.

**If Pastebin changes the rules:** change `mode: scrape` to `mode: api` and set the new `archive-url`,
`download-url`, `limit` and `throttling` in the same block. `api` mode does not enforce the scrape limits, so
you are in charge of them.

Syntax names (for `lang`, `syntax-include`, `syntax-exclude`) are the short names from
<https://pastebin.com/doc_api>: `php`, `python`, `cpp`, `csharp`, `dos` (batch), `text` (plain text) and so on.
A name that is not in the list gives a warning with a suggestion.

## 3. Install on Linux or a Proxmox container

Use a small Debian or Ubuntu container (not the Proxmox host itself). It needs to go out through the whitelisted IP.

```bash
apt update && apt install -y git python3-venv
git clone https://github.com/hyber-code/pystemon.git /opt/pystemon
cd /opt/pystemon
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp test-pastebin.yaml pystemon.yaml      # then edit pystemon.yaml (see section 6)
```

Quick test (stop with Ctrl+C after about 3 minutes; log goes to a file as well):

```bash
.venv/bin/python pystemon.py -c pystemon.yaml 2>&1 | tee pystemon-test.log
```

Run it all the time as a service:

```bash
useradd --system --home /opt/pystemon pystemon
chown -R pystemon:pystemon /opt/pystemon
sed -i 's#/home/pystemon/pystemon#/opt/pystemon#g; s#ExecStart=.*#ExecStart=/opt/pystemon/.venv/bin/python /opt/pystemon/pystemon.py -c /opt/pystemon/pystemon.yaml#' pystemon.service
cp pystemon.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now pystemon
journalctl -u pystemon -f          # live log
```

Reload the config without stopping: `systemctl reload pystemon`. The unit has hardening options; if it will
not start on an old systemd, delete lines from the hardening block at the bottom.

## 4. Install on Windows

Needs Python 3.9 or newer. In PowerShell, in the folder you want:

```powershell
git clone https://github.com/hyber-code/pystemon.git
cd pystemon
python -m pip install -r requirements.txt
python pystemon.py -c test-pastebin.yaml 2>&1 | Tee-Object pystemon-test.log
```

Stop with Ctrl+C. Do not use the `-d` (background) option on Windows. For something permanent, use Docker or a
Linux container instead.

## 5. Install with Docker

The image is small, runs as a normal user (id 10001), and needs no ports.

```bash
git clone https://github.com/hyber-code/pystemon.git && cd pystemon
nano docker/pystemon.yaml                       # set your search patterns, email, and so on
mkdir -p data && sudo chown 10001:10001 data    # results are written here
docker compose up -d --build
docker compose logs -f
```

- The compose file builds the image on your own machine, so nothing needs to be downloaded or published.
  The first build takes a few minutes. A ready-made image for x86 and ARM (Raspberry Pi) is also published
  by GitHub Actions at `ghcr.io/hyber-code/pystemon:latest`; to use it, follow the comment in
  `docker-compose.yml` and make the package public on GitHub first.
- Stop: `docker compose down`. Update: `git pull && docker compose up -d --build`.
- The container goes out through the host's public IP, so whitelist that one.

## 6. Configuration cheat sheet

Search patterns are regular expressions, case-insensitive by default:

```yaml
search:
  - search: 'example\.com'
    description: 'my domain'
  - search: 'AKIA[0-9A-Z]{16}'      # looks like an AWS key
    description: 'aws key'
  - search: 'password'
    count: 3                        # only if it appears at least 3 times
    exclude: 'lorem ipsum'          # ignore the paste if this also matches
    to: 'someone@example.com'       # extra person to email for this pattern only
    regex-flags: 're.I | re.M'      # optional: names like re.I, re.M, re.S only
```

Where results go:

```yaml
storage:
  archive:
    storage-classname: FileStorage
    save: yes            # keep pastes that matched
    save-all: no         # yes = keep every paste (needs a lot of disk)
    dir: "alerts"
    dir-all: "archive"
    compress: yes
```

Useful per-site options (Pastebin preset): `limit`, `lang`, `metadata: yes` (doubles the requests), `min-size`,
`max-size`, `syntax-include`, `syntax-exclude`, `bind-ip` (the local address of this machine, only if it has
several networks), `use-proxy`.

Other storage engines (sqlite, redis, mongodb, telegram) are in the sample `pystemon.yaml`. Note that
`network: ip:` in that file is commented out on purpose: it is a local address, set it only if you need it.

## 7. Alerts

**Email**

```yaml
email:
  alert: yes
  from: alerts@example.com
  to: you@example.com
  server: smtp.example.com
  port: 587
  tls: yes               # the server certificate is checked
  username: 'smtp-user'
  password: 'smtp-password'
  subject: '[pystemon] {subject}'
```

Large pastes are attached instead of pasted into the mail. Keep the config file readable by you only
(`chmod 600 pystemon.yaml`) because it holds the SMTP password.

**Telegram:** in the `storage:` block enable `telegram` with your bot `token` and `chat-id`
(see the sample `pystemon.yaml`). Long pastes are cut to 4000 characters.

## 8. Updating

```bash
cd /opt/pystemon && git pull && .venv/bin/pip install -r requirements.txt && systemctl restart pystemon
```

Docker: `docker compose pull && docker compose up -d`. Windows: `git pull` and run again.

Your `pystemon.yaml` and the `alerts`/`archive` folders are not touched by `git pull` as long as you copied
the sample instead of editing it in place (or keep your config outside the folder and pass `-c /path/to/file`).

## 9. Troubleshooting

| What you see | Usually means | What to do |
|---|---|---|
| `403 ... aborting` on the list | IP not whitelisted, or a different IP is used | Whitelist the IPv4 from `curl -4 https://ifconfig.me`. Turn off VPNs. |
| `429 ... waiting Ns` | Pastebin asked us to slow down | Nothing: pystemon waits as asked. If frequent, lower `limit` or use `min-size`/`syntax-exclude`. |
| `not valid JSON` | An error page came back instead of the list | Same as 403, check the URL in a browser. |
| `Pastie size is 0B, ignoring` | The paste was removed between the list and the download | Normal on a busy site. |
| `is not in Pastebin's syntax list` | A typo in `lang` or a syntax filter | Use the suggested name. |
| `scrapes pastebin.com web pages while the Pastebin scraping API is enabled` | Both the free and Pro entries are on | Set the free `pastebin.com` entry to `enable: no`. |
| No hits at all | Your patterns are too narrow, or `count`/`exclude` hide them | Try `search: 'http'` to prove the chain works, then narrow. |
| Docker: `permission denied` on `/data` | The data folder is not writable for the container user | `sudo chown 10001:10001 data` |
| `Configuration file not found` | Wrong path after `-c` | Give the full path to the yaml file. |

Turn on more detail with `logging-level: DEBUG` in the config (or `--debug` on the command line).

## 10. Good manners and limits

- Follow Pastebin's rules: [scraping API](https://pastebin.com/doc_scraping_api). One whitelisted IP, at most one
  request per second, about one list poll a minute, no scraping of `/raw/*` from that IP.
- pystemon downloads only public pastes and keeps them on your disk. Treat what it saves as sensitive (it can
  contain leaked credentials and personal data), keep the folder private, and delete what you do not need.
- The tool is AGPLv3 (see `LICENSE`). If you run a modified version as a public service you must offer the source.
