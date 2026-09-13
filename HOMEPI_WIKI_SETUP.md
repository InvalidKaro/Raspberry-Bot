# HomePi Wiki + Offline Library

Lightweight local knowledge base for the Raspberry Pi 3 B+, with Kiwix/ZIM support on USB storage.

## Architecture

- HomePi Wiki: `127.0.0.1:8092`
- Kiwix Server: `127.0.0.1:8091`
- Nginx entrypoints:
  - `/wiki/` -> HomePi Wiki
  - `/wikipedia/` -> Kiwix
- ZIM files: `/mnt/homepi-data/kiwix`

## Install the feature branch

```bash
cd ~/Raspberry-Bot
git fetch origin
git switch feature/homepi-wiki
git pull --ff-only
```

## Python dependencies

Use the existing project virtualenv:

```bash
cd ~/Raspberry-Bot
source .venv/bin/activate
pip install -r requirements-wiki.txt
deactivate
```

## Prepare local data directory

```bash
mkdir -p ~/Raspberry-Bot/homepi_wiki/data
```

## Install systemd unit

```bash
sudo cp systemd/homepi-wiki.service /etc/systemd/system/homepi-wiki.service
sudo systemctl daemon-reload
sudo systemctl enable --now homepi-wiki
sudo systemctl status homepi-wiki --no-pager
```

Direct test before Nginx:

```bash
curl -I http://127.0.0.1:8092/wiki/
curl http://127.0.0.1:8092/wiki/api/status
```

## USB storage

Do not assume the USB device name. First inspect it:

```bash
lsblk -f
```

After the USB partition has been identified and mounted persistently at `/mnt/homepi-data`, create:

```bash
sudo mkdir -p /mnt/homepi-data/kiwix
sudo chown -R stefano:stefano /mnt/homepi-data/kiwix
```

Large ZIM files must use a filesystem that supports files larger than 4 GiB. Avoid FAT32.

## Kiwix

Install the distribution Kiwix server package if available for the installed Raspberry Pi OS release, then run `kiwix-serve` bound to localhost on port 8091. Exact package/command should be verified on the Pi because package naming and Kiwix CLI options may differ by OS release.

## Nginx

Install Nginx if not present, then copy/adapt `nginx/homepi-wiki.conf`. If an existing HomePi Nginx server block already exists, merge only the `/wiki/` and `/wikipedia/` location blocks instead of creating a second conflicting server block.

Always validate before reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Add Wiki pages

Create Markdown files under:

```text
homepi_wiki/content/
```

Supported front matter:

```text
---
title: Page title
category: Category
description: Short description
---
```

Restart the wiki after adding pages so its SQLite FTS search index is rebuilt:

```bash
sudo systemctl restart homepi-wiki
```

## Troubleshooting

```bash
journalctl -u homepi-wiki -n 100 --no-pager
ss -ltnp | grep -E '8091|8092'
curl http://127.0.0.1:8092/wiki/api/status
```
