# HomePi Intelligence

Dieses Paket verbindet drei Funktionen:

1. **Blackbox** – persistente Ereignis- und Temperaturhistorie
2. **Server Score** – kompakter Gesundheitswert 0–100
3. **Warnzentrale** – regionale amtliche Warnungen über die öffentliche NINA/BBK-Schnittstelle

## Installation

```bash
cd ~/services/Raspberry-Bot
git pull --ff-only
bash scripts/install_homepi_intelligence.sh
sudo systemctl restart raspberry-display2 raspberry-dashboard
```

Status prüfen:

```bash
sudo systemctl status raspberry-intelligence --no-pager
cat data/homepi_intelligence.json
```

## Warnregion konfigurieren

Die Warnzentrale braucht einen 12-stelligen Amtlichen Regionalschlüssel auf Kreisebene.

```bash
nano .env.homepi
```

Eintragen:

```env
HOMEPI_WARNING_ARS=XXXXXXXXXXXX
HOMEPI_WARNING_LABEL=Meine Region
```

Anschließend:

```bash
sudo systemctl restart raspberry-intelligence
```

Ohne `HOMEPI_WARNING_ARS` funktionieren Blackbox und Server Score vollständig; die Warnseite zeigt lediglich `CONFIG` an und der Sprachassistent erklärt, dass die Region noch fehlt.

## Blackbox

Gespeichert werden unter anderem:

- Boot-/Reboot-Ereignisse
- Internet-Ausfall und Wiederherstellung inklusive Dauer
- Zustandswechsel der HomePi-Dienste
- Meshtastic Connect/Disconnect
- neue, vom Pi gesehene Netzwerk-Nachbarn
- unerwartete Bot-Fehler aus `dashboard_error_events`
- Temperaturwarnungen
- neue bzw. beendete amtliche Warnungen
- Temperatur-, CPU-, RAM-, Speicher-, Internet- und Score-Samples

Datenbank:

```text
data/homepi_blackbox.sqlite3
```

System-Samples werden standardmäßig alle fünf Minuten gespeichert und ein Jahr aufbewahrt. Ereignisse bleiben bestehen.

## Server Score

Teilwerte:

- System
- Netzwerk
- Dienste
- Stabilität

Gesamtstatus:

- `GREEN`: 90–100
- `YELLOW`: 75–89
- `ORANGE`: 55–74
- `RED`: 0–54

## Display 2

Zusätzliche Seiten:

- `SERVER SCORE`
- `BLACKBOX`
- `WARNZENTRALE`

Diese Seiten rotieren sowohl im HomePi- als auch im Meshtastic-Profil. Wichtige amtliche Warnungen können die normale Rotation kurz als Prioritäts-Overlay überblenden.

## Sprachassistent

Beispiele:

```text
Blackbox
Was ist heute passiert?
Was ist letzte Nacht passiert?
Server Score
Wie geht es dem Server?
Warnzentrale
Gibt es Warnungen?
Gibt es eine Unwetterwarnung?
```

Die Antworten kommen weiterhin über das vorhandene JSON-Feld `speech`, daher muss der iPhone-Kurzbefehl nicht geändert werden.

## Konfiguration

Siehe `.env.homepi.example`.
