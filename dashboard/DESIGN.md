# HomePi Dashboard Design System

## Product intent
HomePi is an operational control surface for a Raspberry Pi, Raspberry-Bot, Pi-hole, Tailscale, Git/deploy, SQLite, logs and backups. The interface should feel like a premium developer tool rather than a generic admin template.

## Design direction
- Technical, calm, precise, dark-first.
- Dense enough for operational work, but never visually noisy.
- Strong information hierarchy before decoration.
- Use surfaces sparingly; avoid cards nested inside cards.
- Primary accent is HomePi Signal Blue. Semantic green, amber and red are reserved for real status.
- Motion is subtle and optional. Never rely on animation to communicate state.
- Every state must remain readable on a small laptop, tablet and phone.

## Core tokens
- Canvas: `#07090d`
- Sidebar: `#0a0d12`
- Surface 1: `#0f131a`
- Surface 2: `#141923`
- Surface hover: `#191f2b`
- Border: `#252c39`
- Border strong: `#353e4e`
- Text: `#f5f7fb`
- Text soft: `#a5afbf`
- Text muted: `#778397`
- Signal Blue: `#69a7ff`
- Signal Blue strong: `#4f8ee8`
- Success: `#49d39a`
- Warning: `#efbd5f`
- Danger: `#ff7480`

## Typography
Use the system UI stack. No external font dependency is required on HomePi.
- Page title: 28–36 px, 700–760 weight, tight tracking.
- Section title: 20–24 px, 650–720 weight.
- Metric value: 26–34 px, tabular numerals.
- Body: 14–16 px.
- Labels: 11–12 px uppercase only for terse metadata; do not overuse.

## Layout
- Desktop: 248 px persistent sidebar + fluid content.
- Main content max width: 1440 px.
- Primary page gap: 20–24 px.
- Prefer asymmetric layouts and metric rails over repeated equal-card grids.
- The overview answers "what needs attention?" first, then offers drill-downs.
- Panel radius: 16–18 px.
- Mobile: navigation becomes horizontally scrollable instead of hiding critical sections.
- Use CSS Grid/Flexbox; do not measure layout in JavaScript.

## Components

### Navigation
- Clear active state with both contrast and a left/accent indicator.
- Buttons stay at least 44 px high.
- Horizontal mobile navigation preserves touch targets.
- Destructive logout remains visually distinct but not dominant.

### Panels
- One surface boundary per semantic group.
- Avoid unnecessary nested borders.
- Use subtle gradient/background only where it communicates hierarchy.
- Panels must support long text and code without breaking the viewport.

### Metrics
- Raw metrics live in a shared telemetry rail instead of isolated decorative cards.
- Surface warnings before healthy detail; healthy systems should recede visually.
- Numbers use tabular numerals.
- Progress bars are secondary; the numeric value remains primary.
- Status colors should not be the only carrier of meaning.

### Forms
- Every control has a visible label.
- Inputs use dark native-compatible backgrounds.
- Focus-visible state must be high contrast.
- Long values must wrap or scroll safely.

### Tables
- Sticky headers when practical.
- Tabular numerals for numeric columns.
- Horizontal scroll on narrow screens instead of layout breakage.
- Row hover may assist scanning but cannot be the only affordance.

### Editor / Logs
- Monospace content gets a separate darker work surface.
- Preserve keyboard navigation and selection behavior.
- Do not introduce heavy syntax libraries unless there is a clear operational benefit.

## Interaction rules
- Use `button` for actions and `a` for navigation.
- Never remove focus outlines without a replacement.
- Avoid `transition: all`; animate only specific properties.
- Respect `prefers-reduced-motion`.
- Destructive operations continue to require confirmation.
- Async status remains exposed through existing live regions/toasts.

## Performance rules
- No external font, icon or animation dependency for the base dashboard.
- Prefer CSS over JavaScript for visual behavior.
- Avoid backdrop-filter on large scrolling surfaces.
- Avoid continuous animations.
- Keep the dashboard usable on low-power clients and over remote access.

## Preservation contract
Visual redesigns must preserve:
- Existing element IDs consumed by `dashboard/static/app.js`.
- Existing `data-*` action hooks.
- Existing API paths.
- Existing authentication, CSRF and confirmation behavior.
- Existing backend service boundaries.

This file is the visual source of truth for future HomePi dashboard work.


## Product architecture
HomePi is a suite, not a pile of unrelated dashboards.

- `/` is the triage and navigation surface: health, resource pressure, repository state, and direct drill-down.
- `/control` owns maintenance, trends, process health and cog controls.
- `/ops` owns deeper analytics, Discord operations, incidents, hardware and reliability.
- `/database-admin` owns safe write-capable SQLite administration.
- `/workspace`, `/workspace/studio`, and `/workspace/manage` own Discord content and structured workspace data.
- `/media` and `/now-playing` own playback.
- `/meshtastic` owns LoRa and RF telemetry.
- Navigation between these areas is global and consistent. Avoid duplicating the same detailed data on several pages.

## Interaction architecture
- Main dashboard sections are deep-linkable through URL hashes and respond to browser Back/Forward.
- Subsites share a global HomePi navigation bar and searchable switcher.
- `Ctrl/Cmd + K` opens the global switcher except on pages that already own that shortcut.
- Existing destructive confirmations and CSRF behavior remain untouched.
- Do not introduce continuous polling faster than the underlying data changes.

## Anti-slop rules
- No decorative purple mesh gradients.
- No equal three/four-card feature rows as the default composition.
- No decorative status dots; dots only represent actual live state.
- No fake terminal/UI decoration that does not perform a real function.
- Do not nest cards simply to create depth.
- Prefer one clear accent, neutral surfaces, and semantic state colors.
- Every decorative treatment must justify its operational purpose.


## Dashboard feature principles
The dashboard should reduce time-to-diagnosis rather than merely expose more numbers.

- The overview uses a triage-first sequence: platform state → operator brief → live telemetry → recent operations → deeper workspaces.
- CPU, RAM, temperature and disk retain a small local trend history in the browser. This adds immediate directionality without adding a new Pi-side polling service or database table.
- Recent dashboard actions are surfaced on the overview using the existing audit endpoint so operational changes have visible context.
- The main dashboard exposes a keyboard command center for navigation and safe utility actions. Navigation entries remain native links where applicable.
- Diagnostic snapshots are copyable as plain text for debugging and support. Never include secrets, environment values, tokens or private file contents.
- Refresh frequency should match the underlying data. The overview status sampler stays at 30 seconds and recent audit activity at 120 seconds while the page is visible.

## Visual hierarchy
- Healthy data recedes. Warnings and failures get stronger semantic treatment.
- Trends support the primary number; they never replace the numeric value.
- Avoid four isolated cards when one shared telemetry rail communicates the relationship more clearly.
- Use asymmetry to indicate priority: the operator brief and primary operational workspaces receive more space than secondary links.
- Decoration should never compete with system state. No ambient radial glows, ornamental rings, or non-semantic status lights.
