---
title: Offline Library
category: Wissen
description: Wikipedia, WikiHow und Medizin-Inhalte lokal mit Kiwix lesen.
---

# Offline Library

Die Offline Library nutzt **Kiwix**. ZIM-Dateien liegen auf dem USB-Speicher unter:

```text
/mnt/homepi-data/kiwix
```

Das Wiki erkennt vorhandene `.zim`-Dateien automatisch und zeigt sie als Karten an.

## Geplante Inhalte

| Bereich | Beispiel |
|---|---|
| Enzyklopädie | Wikipedia Deutsch ohne Bilder |
| How-To | WikiHow, sofern als passendes ZIM verfügbar |
| Medizin | medizinische Wikipedia-/Kiwix-Sammlungen |

## Warum USB?

Große ZIM-Archive gehören nicht ins Git-Repository. Sie können direkt vom Raspberry Pi auf den USB-Stick heruntergeladen und anschließend von `kiwix-serve` bereitgestellt werden.

## Zugriff

Nach der Einrichtung ist Kiwix hinter dem Reverse Proxy erreichbar:

```text
http://homepi.local/wikipedia/
```
