# Image style — Tilth docs

Reusable prompt scaffold for generating documentation images that share a consistent visual voice. Anchor new generations to this file so the docs don't drift into an inconsistent collage over time.

This file is intentionally not in the published nav — it's a contributor reference, not user-facing docs.

## The reference image

![Brain / Hands / Session split — the canonical Tilth docs visual voice](brain-hands-session.png)

`brain-hands-session.png` is the visual benchmark. New images should feel like they belong on the same page as it — same palette, same medium, same temperament. When something feels off in a new generation, compare side-by-side with this image and figure out what slipped.

## Style block (paste at the end of every prompt)

```
STYLE — consistent across all Tilth documentation images:

Medium: a thoughtful hand-drafted technical sketch in the style of a
craft-oriented engineering zine. Linework feels hand-drawn but precise —
slightly imperfect, never wobbly or sloppy. The image should look like
it belongs in a beautifully made software-craft publication, not in a
generic AI infographic.

Background: warm off-white paper (#F5F1E8), with a subtle natural paper
grain texture. No flat white, no gradient backgrounds.

Linework and text: charcoal grey (#2A2A2A) for all outlines, glyphs, and
text. Stroke weights are deliberate and confident — never spindly.

Typography (must render as crisp, legible vector type):
- Titles and section labels: bold geometric sans-serif, all caps,
  Inter or IBM Plex Sans feel.
- Code, file paths, and technical identifiers: monospace, JetBrains
  Mono feel.
- Captions and annotations: italic sans-serif, smaller weight.

Accent color: a single confident sage-green (#6B8E6F), used only on the
flow/connection elements that carry meaning (arrows, ribbons, paths,
threads). The accent should be the second-most-important visual element
after the primary subject — present and load-bearing, never decorative
filler. Everything else stays charcoal on paper.

Composition: 16:9 horizontal framing, generous spacing, soft drop
shadows on primary shapes for gentle depth. Clean, breathable layout.

Mood: precise but warm. Craft-oriented. Restrained. Deliberate.

Do NOT include: gradients, 3D rendering, neon colors, glow effects,
photorealism, generic AI-infographic aesthetic, decorative flourishes,
thin spindly lines, dashed lines, multiple accent colors, any text other
than what is explicitly specified in the subject description above.
```

## Template structure

```
[SUBJECT DESCRIPTION — describe what's in the frame: shapes, glyphs,
labels, arrows, figures, layout, what each element represents. Spell
out every word of text verbatim in quotes. Be explicit about placement
(left/center/right, top/bottom).]

[RELATIONSHIPS — if the image has connecting elements (arrows, threads,
paths), describe them here, including stroke weight relative to other
elements, head/terminator style, and any labels along them.]

<<paste the STYLE block above verbatim>>
```

## Two worked variants

### Diagrammatic (like `brain-hands-session.png`)

Open with: `"A clean technical architecture diagram, 16:9 horizontal composition. N rounded rectangular boxes arranged [horizontally / in a cycle / ...]..."` — then for each box specify title, glyph, monospace labels, italic caption. Then describe arrows with explicit stroke weight (e.g., "2.5x the weight of the box outlines") and labels. Then paste the style block.

### Illustrative (figure-based)

Open with: `"A horizontal 16:9 illustrated scene. Centered subject: a calm, gender-neutral figure shown from [angle], wearing [simple work attire], with no facial features rendered..."` — describe props around them, label tags with leader lines, what each labeled element represents. Then paste the style block, with this addendum appended:

```
Light translucent watercolor washes in muted sage-green, terracotta, and
dusty-blue may accompany the linework — uneven and hand-applied, never
flat-filled.
```

## Iteration notes

- **Roll 3–4 times** per prompt before tweaking — variance between generations is significant; more rolls is the cheapest path to a good result.
- **Aspect ratio:** request `16:9` explicitly. MkDocs Material renders banner-shaped images well at full content width.
- **Resolution:** ask for the largest size the endpoint supports; downscale later. Text legibility scales nonlinearly with resolution.
- **Adding a new color** for a one-off purpose (e.g., warning-red, info-blue): mention it in the subject description, *not* in the style block — that keeps the baseline palette stable across the set.
- **Periodic drift check:** after every 3–4 new images, lay them out side-by-side with `brain-hands-session.png`. If one feels off, regenerate rather than letting the family drift.
- **Text touch-up:** if a label is unreadable after several rolls, generate without labels, then overlay text in Figma/Inkscape rather than burning more rolls.
