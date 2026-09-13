---
title: Home Server Übersicht
category: HomePi
description: Zentrale Übersicht über Dienste, Speicher und wichtige Pfade.
---

# Home Server Übersicht

Dieses Wiki dokumentiert den HomePi und seine Dienste. Große Wissensarchive liegen auf dem USB-Speicher, während Code und Wiki-Seiten im Git-Repository bleiben.

## Wichtige Bereiche

- **Dashboard:** Systemstatus und Verwaltungsfunktionen
- **Discord Bot:** Raspberry-Bot und zugehörige Services
- **Offline Library:** Kiwix mit ZIM-Dateien auf USB
- **Netzwerk:** Pi-hole, Tailscale und lokale Dienste

## Zielstruktur

```text
Raspberry Pi
├── Raspberry-Bot
├── HomePi Wiki
├── Dashboard
└── /mnt/homepi-data
    └── kiwix
        ├── wikipedia_*.zim
        ├── wikihow_*.zim
        └── medical_*.zim
```

> Das Git-Repository enthält keine ZIM-Dateien. Diese werden direkt auf dem Pi auf den USB-Speicher geladen.
