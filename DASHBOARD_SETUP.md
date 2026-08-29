# HomePi Dashboard — Phase 1

Phase 1 includes Pi metrics, Raspberry-Bot service state/control, Pi-hole status, journalctl logs, Git status, `git pull --ff-only`, `git push`, password login, CSRF protection, and a separate systemd service. It does not expose an arbitrary shell.

## Install

Copy/merge this package into `/home/stefano/services/Raspberry-Bot`, then:

```bash
cd ~/services/Raspberry-Bot
source .venv/bin/activate
pip install -r requirements-dashboard.txt
deactivate
cp .env.dashboard.example .env.dashboard
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
nano .env.dashboard
```

Set a private `DASHBOARD_TOKEN` (12+ chars) and paste the generated random value into `DASHBOARD_SECRET`. Add `.env.dashboard` to `.gitignore`.

## Limited bot-control permission

```bash
command -v systemctl
```

If it prints `/usr/bin/systemctl`:

```bash
sudo cp sudoers/raspberry-dashboard /etc/sudoers.d/raspberry-dashboard
sudo chmod 440 /etc/sudoers.d/raspberry-dashboard
sudo visudo -cf /etc/sudoers.d/raspberry-dashboard
```

The final command must report that the file parsed OK.

## Dashboard service

```bash
sudo cp systemd/raspberry-dashboard.service /etc/systemd/system/raspberry-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now raspberry-dashboard
sudo systemctl status raspberry-dashboard
```

Open `http://homepi.local:8080` on your home network.

## Checks

```bash
curl http://127.0.0.1:8080/health
journalctl -u raspberry-dashboard -n 80 --no-pager
cd ~/services/Raspberry-Bot
git push --dry-run
```

Do not port-forward port 8080 directly to the internet. Remote access should later go through an authenticated VPN/reverse proxy.
