# Raspberry-Bot Phase 3.3.1

## MD Personalabteilung weekly report

New `/perso weekly` command:

```text
/perso weekly
  wochen: KW35;KW36;KW37;KW38
  bewerbungen: 12;17;14;21
  einweisungen: 5;8;6;11
```

Defaults:

- X axis: Kalenderwoche
- Y axis: Anzahl
- Series 1: Bewerbungen
- Series 2: Einweisungen
- Bar chart by default, optional line chart
- Optional saved dataset for `/perso render`
- Optional ephemeral/private response

The embed also calculates totals, weekly averages, the overall `Einweisungen / Bewerbungen` rate and the strongest week for each series. Counts are validated as non-negative whole numbers.

Dashboard `/health` reports `3.3.1`.
