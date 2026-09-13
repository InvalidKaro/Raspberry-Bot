# Smart Actions v3 — Context-aware diagnostics

Smart Actions v3 extends command-flow ranking with live Raspberry Pi health context.

## Diagnostic signals

The diagnostic engine currently evaluates:

- Raspberry Pi current/historic undervoltage flags
- current/historic frequency capping and throttling
- current/historic soft temperature limits
- elevated CPU temperature
- sustained CPU load
- high RAM pressure
- low disk headroom
- Pi-hole FTL availability

Each finding contains a severity, likely cause, confidence score, evidence, recommended next steps and command suggestions.

## Suggestion ranking

For system-oriented command flows (`system`, `admin`, `overview`, `pi`, `config`), Smart Actions combines:

1. live diagnostic relevance
2. current command/group context
3. recent user command usage
4. execution mode preference
5. flow history / loop avoidance

Live diagnostic suggestions are cached for 20 seconds. Existing system sampling remains low-overhead and `vcgencmd get_throttled` continues to be refreshed by the system sampler rather than once per interaction.

## Discord UX

The Control Center now explains the primary detected cause with evidence and a confidence estimate instead of showing only raw throttling flags. Full diagnostics provide `Warum passiert das?` and `Was jetzt?` sections.
