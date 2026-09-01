# Raspberry-Bot Phase 3.3

## Main additions

### MD Personnel Statistics

New `/perso` command group with user-entered chart data, bar/line charts, optional comparison series, calculated statistics and saved reusable datasets.

Example:

```text
/perso graph
  title: Bewerbungen pro Woche
  x_values: KW31;KW32;KW33;KW34
  y_values: 12;18;15;23
  x_label: Kalenderwoche
  y_label: Bewerbungen
  series_name: Eingegangen
```

### Welcome placeholders

Welcome messages now use a centralized Dyno-style renderer. `/setup welcome-preview` tests a template against real member data and `/setup welcome-placeholders` lists every token.

### Pi-hole permission handling

When Pi-hole v6 config access is missing, the bot quietly falls back to the FTL systemd service instead of repeatedly running CLI commands that generate permission-denied log entries.

### Database / Pi optimization

The fresh schema bug in `command_usage` was fixed, new indexes were added, and conservative SQLite pragmas were tuned for a Raspberry Pi 3 B+.

### Dashboard bundle version

`/health` now reports `3.3` for this cumulative bundle.
