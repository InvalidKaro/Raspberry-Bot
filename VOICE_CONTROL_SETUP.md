# HomePi iPhone Voice Control

HomePi kann Sprachbefehle über ein iPhone ausführen, ohne ein Mikrofon am Raspberry Pi und ohne lokale Spracherkennung. Das iPhone übernimmt Diktat/Siri; das bestehende Dashboard verarbeitet nur einen kleinen HTTP-Request.

Dadurch entsteht im Leerlauf praktisch keine zusätzliche CPU-Last auf dem Raspberry Pi 3 B+.

## Architektur

```text
iPhone / Siri / Kurzbefehle
        |
        | WLAN oder Tailscale
        v
POST /api/voice-command
        |
        | Voice API Token
        v
HomePi command parser
        |
        +-- Status lesen
        +-- systemd Units steuern
        +-- HomePi reboot/poweroff
```

Es gibt bewusst **keine beliebige Shell-Ausführung als root**. Für Systemsteuerung wird ein root-owned Helper unter `/usr/local/sbin/homepi-systemctl` installiert. Dieser erlaubt systemd-Steuerung, validiert Unit-Namen und startet keine Shell.

## Installation auf HomePi

```bash
cd ~/services/Raspberry-Bot
git pull --ff-only
bash scripts/install_voice_control.sh
```

Der Installer:

1. installiert den systemctl-Helper root-owned unter `/usr/local/sbin/homepi-systemctl`,
2. aktualisiert und prüft die sudoers-Regeln,
3. erzeugt einen zufälligen `VOICE_API_TOKEN` in `.env.dashboard`,
4. startet das Dashboard neu,
5. zeigt den Token für den iPhone-Kurzbefehl an.

Token später erneut anzeigen:

```bash
grep '^VOICE_API_TOKEN=' ~/services/Raspberry-Bot/.env.dashboard
```

Den Token nicht öffentlich teilen.

## API-Test

```bash
TOKEN="$(grep '^VOICE_API_TOKEN=' ~/services/Raspberry-Bot/.env.dashboard | cut -d= -f2-)"
curl -sS -X POST http://127.0.0.1:8080/api/voice-command \
  -H "X-HomePi-Token: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Status"}'
```

Beispielantwort:

```json
{
  "ok": true,
  "speech": "CPU 4 Prozent, RAM 42 Prozent, Speicher 31 Prozent, Temperatur 39.5 Grad, Uptime 120 Stunden 8 Minuten.",
  "command": "system-summary"
}
```

## iPhone-Kurzbefehl

In **Kurzbefehle** einen neuen Kurzbefehl anlegen. `HomePi` funktioniert, Siri versteht als Auslöser aber oft **`Serversteuerung`** oder **`Raspberry Steuerung`** zuverlässiger.

Nach dem Start des Kurzbefehls muss das Wort `HomePi` im eigentlichen Diktat **nicht noch einmal gesagt werden**. Einfach `Status`, `Bot neu starten`, `Pi neu starten` usw. sprechen.

### Aktionen

1. **Text diktieren**
   - Sprache: Deutsch
   - Stoppen: nach Pause

2. **Inhalte von URL abrufen**
   - URL: `http://PI-IP:8080/api/voice-command`
   - Methode: `POST`
   - Request Body: JSON
   - Feld `text`: Ergebnis von **Text diktieren**
   - Header `X-HomePi-Token`: `DEIN_TOKEN`

3. **Wert aus Wörterbuch abrufen**
   - Schlüssel: `speech`

4. **Text sprechen**
   - den Wert `speech`

## Siri-Erkennung

Der Parser akzeptiert mehrere typische Diktatvarianten von HomePi, unter anderem:

```text
HomePi
Home Pi
Home Pie
Home Pai
Home Pei
HomPi
```

Noch zuverlässiger ist es, nach dem Start des Kurzbefehls einfach **ohne Wake-Name** zu diktieren:

```text
Status
Bot neu starten
Meshtastic neu starten
Pi neu starten
```

## Befehlsübersicht

Der Sprachbefehl `Befehle`, `Hilfe`, `Was kannst du?` oder `Befehlsliste` liefert eine kurze Funktionsübersicht.

### Systemstatus

```text
Status
Pi Status
Server Status
Wie warm ist der Pi?
Temperatur
CPU Auslastung
RAM Auslastung
Speicher Auslastung
Uptime
```

### Bekannte HomePi-Dienste

```text
Starte den Bot neu
Starte das Dashboard neu
Starte Display eins neu
Starte Display zwei neu
Starte Meshtastic neu
Starte Pi-hole neu
Starte Tailscale neu
```

Bekannte natürliche Namen:

| Gesprochen | Unit |
|---|---|
| Bot / Discord Bot | `raspberry-bot.service` |
| Dashboard | `raspberry-dashboard.service` |
| Display / Display 1 | `raspberry-display.service` |
| Display 2 | `raspberry-display2.service` |
| Meshtastic | `raspberry-meshtastic.service` |
| Pi-hole | `pihole-FTL.service` |
| Tailscale | `tailscaled.service` |

### Beliebige systemd-Units

```text
Liste Dienste
Systemd neu laden
Status Dienst ssh
Dienst nginx starten
Dienst nginx neu starten
Dienst nginx neu laden
Dienst nginx stoppen
Dienst nginx aktivieren
Dienst nginx deaktivieren
Dienst nginx maskieren
Dienst nginx entmaskieren
systemctl restart cron
systemctl status ssh
systemctl is-active tailscaled
systemctl is-enabled ssh
```

Unterstützte Aktionen:

```text
start
stop
restart
reload
try-restart
status
enable
disable
mask
unmask
is-active
is-enabled
daemon-reload
```

### Stromversorgung

```text
Pi neu starten
Server neu starten
HomePi neu starten
Pi herunterfahren
Server herunterfahren
HomePi herunterfahren
```

## Kritische Befehle und Bestätigung

Reboot, Herunterfahren, `stop`, `disable`, `mask` sowie riskantere Aktionen auf unbekannten systemd-Units verlangen eine explizite Bestätigung.

Beispiel:

```text
Pi neu starten
```

HomePi antwortet:

```text
Kritischer Befehl. Ich merke ihn mir 60 Sekunden. Starte HomePi noch einmal und sage nur Bestätigen oder Abbrechen.
```

Danach den Kurzbefehl erneut starten und nur sagen:

```text
Bestätigen
```

oder:

```text
Abbrechen
```

Die folgenden Bestätigungsformulierungen werden akzeptiert:

```text
Bestätigen
Befehl bestätigen
Ja bestätigen
Ausführen
Jetzt ausführen
Ja wirklich
```

Wenn innerhalb von 60 Sekunden ein anderer Befehl kommt, wird die vorgemerkte kritische Aktion verworfen. Dadurch kann ein späteres versehentliches `Bestätigen` keinen alten Befehl ausführen.

Alternativ funktioniert weiterhin die Ein-Satz-Form:

```text
Pi neu starten bestätigen
Dienst nginx neu starten bestätigen
Dienst ssh stoppen bestätigen
```

Bei bekannten HomePi-Diensten sind normale `restart`-Befehle ohne zweite Bestätigung möglich. `stop`, `disable` und `mask` bleiben auch dort bestätigungspflichtig.

## Sicherheit

- `/api/voice-command` hat keine Dashboard-Session nötig, akzeptiert aber ausschließlich den separaten `VOICE_API_TOKEN`.
- Der Token wird mit konstantzeitlichem Vergleich geprüft.
- Die API ist auf 30 Requests pro Minute und Client begrenzt.
- Diktat wird maximal 500 Zeichen lang akzeptiert.
- Es gibt keinen `shell=True`- oder freien Bash-Endpunkt.
- Der privilegierte Helper wird bei Installation root-owned kopiert; Änderungen im Git-Checkout ändern daher nicht automatisch den root-Helper.
- Kritische Aktionen werden nur 60 Sekunden vorgemerkt und können mit `Abbrechen` verworfen werden.

Für Nutzung außerhalb des Heim-WLANs sollte der Endpoint nur über Tailscale/VPN erreichbar gemacht werden, nicht per Router-Portfreigabe ins öffentliche Internet.
