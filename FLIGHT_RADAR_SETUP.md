# HomePi Flight Radar

Lightweight live aircraft radar for Raspberry Pi. The service runs independently from the main dashboard on port `8093` so a radar/API outage cannot take the bot dashboard down.

## Data sources

1. `adsb.lol` point API (primary)
2. `airplanes.live` point API (automatic fallback)

The backend validates coordinates/radius, caches upstream results for a few seconds, and never accepts arbitrary upstream URLs from the browser.

## Install / update

```bash
cd ~/services/Raspberry-Bot
git switch main
git pull --ff-only

sudo cp systemd/homepi-flight-radar.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now homepi-flight-radar
```

Check:

```bash
systemctl status homepi-flight-radar --no-pager
curl http://127.0.0.1:8093/health
```

Open from the LAN:

```text
http://homepi.local:8093/
```

## Features

- dark premium radar UI with animated sweep and range rings
- all aircraft returned by the selected data source inside the selected search radius
- 50 / 100 / 120 / 250 NM range presets
- live refresh every 7 seconds with a 4-second backend cache
- map pan -> automatically loads traffic around the new center
- callsign / registration / type / ICAO search
- aircraft heading/rotation on the map
- aircraft details: barometric/geometric altitude, ground speed, track, vertical rate, squawk, navigation altitude, IAS/TAS/Mach, category, source and signal age where available
- emergency squawks 7500 / 7600 / 7700 highlighted
- fallback provider if the primary source is unavailable
- responsive layout for desktop and phone

## Notes

This is cooperative surveillance data, not primary radar. Aircraft may be absent when they are not broadcasting usable data or are outside receiver/network coverage. Availability and individual fields vary by aircraft and source.
