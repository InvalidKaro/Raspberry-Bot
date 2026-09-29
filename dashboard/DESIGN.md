# HomePi Dashboard Design System

## Product intent
HomePi is an operational control surface for a Raspberry Pi, Raspberry-Bot, Pi-hole, Tailscale, Git/deploy, SQLite, logs and backups. The interface should feel like a premium developer tool rather than a generic admin template.

## Design direction
- Technical, calm, precise, dark-first.
- Dense enough for operational work, but never visually noisy.
- Strong information hierarchy before decoration.
- Use surfaces sparingly; avoid cards nested inside cards.
- Accent color is violet with restrained cyan/green status color usage.
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
- Violet: `#8b5cf6`
- Violet strong: `#7447e8`
- Cyan: `#50c7e8`
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
