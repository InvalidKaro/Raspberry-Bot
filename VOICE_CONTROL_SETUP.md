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
        | Bearer Token
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
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"text":"HomePi Status"}'
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

In **Kurzbefehle** einen neuen Kurzbefehl namens `HomePi` anlegen.

### Aktionen

1. **Text diktieren**
   - Sprache: Deutsch
   - Stoppen: nach Pause

2. **Inhalte von URL abrufen**
   - URL: `http://homepi.local:8080/api/voice-command`
   - Methode: `POST`
   - Request Body: JSON
   - Feld `text`: Ergebnis von **Text diktieren**
   - Header `Authorization`: `Bearer DEIN_TOKEN`
   - Header `Content-Type`: `application/json`

3. **Wert aus Wörterbuch abrufen**
   - Schlüssel: `speech`

4. **Text sprechen**
   - den Wert `speech`

Danach kann der Kurzbefehl auch über Siri gestartet werden, z. B.:

```text
Hey Siri, HomePi
```

Das iPhone fragt nach dem Diktat bzw. nimmt den folgenden Befehl entgegen.

## Beispiele

```text
HomePi Status
Wie warm ist HomePi?
Wie ist die RAM Auslastung?

Starte Meshtastic neu
Starte den Bot neu
Starte Display zwei neu
Stoppe den Bot
Status Dienst ssh
Dienst nginx neu starten
systemctl restart cron
Liste Dienste
Systemd neu laden
```

### Kritische Befehle

Reboot, Herunterfahren sowie riskantere systemd-Aktionen verlangen eine explizite Bestätigung.

Erster Versuch:

```text
HomePi neu starten
```

Antwort:

```text
Das ist ein kritischer Befehl. Sage den Befehl erneut mit dem Wort bestätigen.
```

Dann:

```text
HomePi neu starten bestätigen
```

Dasselbe gilt unter anderem für:

```text
HomePi herunterfahren bestätigen
Dienst ssh stoppen bestätigen
Dienst nginx deaktivieren bestätigen
Dienst bluetooth maskieren bestätigen
```

Bei bekannten HomePi-Diensten sind normale `restart`-Befehle ohne zweite Bestätigung möglich. Bei beliebigen fremden systemd-Units verlangt `restart` zusätzlich `bestätigen`.

## Unterstützte systemd-Aktionen

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
reboot
poweroff
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

Für jede andere systemd-Unit die Form `Dienst <unit> ...` verwenden, z. B. `Dienst ssh neu starten bestätigen`.

## Sicherheit

- `/api/voice-command` hat keine Dashboard-Session nötig, akzeptiert aber ausschließlich den separaten `VOICE_API_TOKEN`.
- Der Token wird mit konstantzeitlichem Vergleich geprüft.
- Die API ist auf 30 Requests pro Minute und Client begrenzt.
- Diktat wird maximal 500 Zeichen lang akzeptiert.
- Es gibt keinen `shell=True`- oder freien Bash-Endpunkt.
- Der privilegierte Helper wird bei Installation root-owned kopiert; Änderungen im Git-Checkout ändern daher nicht automatisch den root-Helper.
- Reboot, Poweroff, Stop/Disable/Mask und fremde Service-Restarts erfordern eine Bestätigung.

Für Nutzung außerhalb des Heim-WLANs sollte der Endpoint nur über Tailscale/VPN erreichbar gemacht werden, nicht per Router-Portfreigabe ins öffentliche Internet.
