# Site style — Tilth docs

The visual identity for the rendered docs site (Material for MkDocs + custom CSS). Companion to [`IMAGE_STYLE.md`](IMAGE_STYLE.md), which governs *images inside* the docs; this file governs the *site that frames them*. Anchor changes to the chrome, typography or component look here so the site doesn't drift into an inconsistent collage over time.

This file is intentionally not in the published nav — it's a contributor reference, not user-facing docs. Excluded via `not_in_nav` in `mkdocs.yml`.

## Provenance

The theme is **Hex**, a published style reference from [refero.design](https://refero.design):

> **Hex — Analytical Clarity on Canvas.** A pristine digital workspace where data takes center stage, framed by muted sophistication and precise typography. Sophisticated, data-centric aesthetic, characterized by a clean white canvas that highlights nuanced typography and subtle violet accents. Interactive elements are thoughtfully understated, relying on outlines and muted states rather than bold fills, ensuring the focus remains on the analytical content and user workflow.

Live reference: <https://styles.refero.design/style/3e32db74-a61d-4e72-93b8-1fb949af2c00>.

**Why Hex for Tilth.** The repo is an artefact, not a marketing site. Hex's light, understated character — Canvas White surfaces, restrained Minsk Violet accents, ghost-button interactions, multi-layer card shadows instead of borders — fits a documentation set that wants the *content* (architecture diagrams, code blocks, prose explaining mechanics) to do the talking. The cousins Hex names — Linear, Figma, Vercel, Notion, Amplitude — set the tone we're aiming at: a calm, readable, technical surface.

## What's actually wired in

Three pieces, all small:

1. **`mkdocs.yml`** — `theme: material`, fonts set to IBM Plex Sans / IBM Plex Mono, a small `features` list (tabs, sections, indexes, code-copy), and `palette` with `primary: custom` + `accent: custom` so the CSS file owns colour.
2. **`docs/stylesheets/extra.css`** — defines the Hex design tokens as CSS custom properties, then maps them onto Material's `--md-*` variables. All component-level styling (headings, admonitions, code blocks, tables, header, footer, buttons) lives here.
3. **Google Fonts substitutes** — Hex calls for three proprietary fonts (PP Editorial New, PP Formula, Cinetype). The CSS file uses the substitutes the original spec recommends — **Playfair Display** (display headlines), **Archivo** (section headings), **IBM Plex Sans** (body / UI), **IBM Plex Mono** (code). Visually close, free to ship, no licensing tangle.

If you're editing the chrome, you're editing `extra.css` 95% of the time. `mkdocs.yml` only changes when you're adding a feature flag or a new markdown extension.

## Token reference

The full token set (every colour, every font scale entry, every shadow) lives in `docs/stylesheets/extra.css` under the "Hex design tokens" section. The list below is the **load-bearing subset** — what you reach for constantly when adding or tweaking a component.

### Colour — quick palette

| Role | Token | Hex |
|---|---|---|
| Primary background (page, cards) | `--color-canvas-white` | `#fffcfc` |
| Primary text | `--color-obsidian-ink` | `#01011b` |
| Heading text | `--color-charcoal-grey` | `#14141c` |
| Borders, secondary text | `--color-eggplant-gray` | `#31263b` |
| Muted helper text | `--color-cement-gray` | `#717a94` |
| Secondary surface (table head, code bg) | `--color-platinum-mist` | `#ecedf2` |
| Light dividers, grid lines | `--color-slate-cloud` | `#dbd7da` |
| **Primary accent** (links, focus) | `--color-minsk-violet` | `#473982` |
| Hover-state accent | `--color-indigo-punch` | `#6f63b7` |

The rest (`--color-dusk-violet`, `--color-lavender-field`, `--color-rose-quartz`) is for charts and decorative graphics. Don't reach for them in chrome.

### Typography — role map

| Role | Font (substitute) | Where it's used |
|---|---|---|
| Display headline | Playfair Display, 300 weight | `h1` on every page |
| Section heading | Archivo, 600–800 weight | `h2`–`h4`, table headers, admonition titles, header title |
| Body / UI | IBM Plex Sans, 400–500 weight | Prose, nav links, buttons, captions |
| Code | IBM Plex Mono, 400 weight | All `<code>` / `<pre>` |

Tight letter-spacing (`-0.014em` to `-0.025em`) is deliberate across the whole stack. Don't loosen it — the "modern, compact feel" Hex calls out is largely a tracking effect.

### Elevation — the signature card

The thing that most marks something as "Hex-looking" is the layered card shadow:

```css
box-shadow:
  rgba(49, 38, 59, 0.22) 0 0 0 1px,
  rgba(49, 38, 59, 0.09) 0 103px 103px 0,
  rgba(49, 38, 59, 0.1)  0 26px 57px 0;
```

Exposed as `var(--shadow-card)`. Already applied to admonitions and `<details>` blocks. Reuse the variable for any new card-shaped component — don't roll a new shadow from scratch.

### Radii

- `--radius-sm: 3px` — buttons, inline code
- `--radius-md: 6px` — cards, inputs, code blocks
- `--radius-lg: 12px` — modals / heavy overlays (rare in docs)
- `--radius-pill: 9999px` — small tags and badges only

## Do's and don'ts

Adapted from Hex's own guidance, narrowed to the cases that actually come up in editing the Tilth docs.

### Do

- Use **Canvas White** for all primary backgrounds. Reach for **Platinum Mist** only when you genuinely need a second surface (table heads, code-block panel).
- Use **Obsidian Ink** for body text, **Charcoal Grey** for headings. The contrast is intentional — heading slightly warmer than body.
- Apply **Eggplant Gray** as the default border colour for outlined elements.
- Use **Minsk Violet** sparingly — links, focus rings, hover-state accents. **Do not** promote it to a primary fill colour. It loses its meaning if it stops being a signal.
- Use **`var(--shadow-card)`** for new card-shaped components rather than inventing a new shadow.
- Keep buttons **ghost-style** by default (transparent fill, Eggplant Gray border, 3px radius). Filled buttons are reserved for the *one* clearest primary action on a page, if at all.
- Keep tracking tight (`letter-spacing` between `-0.01em` and `-0.025em` for type 16px and up).

### Don't

- Don't introduce bold filled buttons or saturated colours outside the palette. The subdued aesthetic is the point.
- Don't deviate from the typeface roles. The serif/sans/mono interplay is what carries the brand — swap one and it stops feeling Hex.
- Don't use `letter-spacing` greater than `normal` for any text. Loose tracking reads as web-1.0 and breaks the type rhythm.
- Don't use `9999px` radius on anything other than small tags/badges. Standard components use 3px, 6px, or 12px.
- Don't use heavy or opaque shadows. The system relies on subtle depth — multi-layer low-alpha, not single drop shadows.
- Don't add textured or patterned backgrounds. Surfaces stay clean and uniform.

## Extending the system

- **Adding a colour for a one-off purpose** (e.g., a warning swatch on a single page): add it as a local CSS variable inside the page-specific rule rather than promoting it into `:root`. Keeps the baseline palette stable.
- **Adding a new component**: prefer composing existing tokens. Reach for `--shadow-card` + `--radius-md` + `--color-canvas-white` first; only introduce new tokens when the same value would need to appear in three or more places.
- **Adding a font weight**: extend the existing `@import` line at the top of `extra.css`; don't add a second font family.
- **Changing a token value**: change it once at the `:root` level. Don't override Hex token values inside Material-mapping rules — push the new value upward into the token layer so every consumer picks it up.

## Drift check

After a substantive theming change, do a quick comparison run:

1. Build the site (`uv run --extra docs mkdocs build --strict --site-dir /tmp/tilth-site`) and serve it.
2. Open the [Hex reference page](https://styles.refero.design/style/3e32db74-a61d-4e72-93b8-1fb949af2c00) next to the home page and a deep-dives page.
3. Sanity-check the three things most likely to drift first: heading weights, the card shadow, and the link-hover colour.

If something feels off, fix it in `extra.css` and rebuild — don't let the visual voice slip between releases.
