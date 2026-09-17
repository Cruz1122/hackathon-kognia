---
name: gooey-ui-system
description: Mandatory project-wide UI skill. Use whenever creating, editing, reviewing, or refactoring ANY UI component, screen, interaction, or visual state. Enforces the gooey/slime material language, fixed palette, Urbanist typography, adaptive corner radius (pills when compact, squarer when tall), and viscosity 7.
compatibility: cursor, opencode
metadata:
  category: ui-design
  risk: low
---

# gooey-ui-system

## Purpose

This is the mandatory design system for the product, not an optional visual layer.

Every bounded UI component created or modified must comply unless the user explicitly overrides a rule for that specific task.

Core identity:

- gooey/slime material behavior;
- palette limited to `#414141`, `#f7c974`, `#faeccf`, `#f8f8f8`;
- Urbanist for all intentional UI typography;
- adaptive corner radius: pills when compact, squarer as the box gets taller;
- SVG goo filter with viscosity fixed at `7`;
- restrained, soft motion;
- crisp content separated from filtered visual mass.

## Activation triggers

Always use this skill for:

- pages and screens;
- buttons, inputs, cards, chips, badges, tabs, nav, sidebars;
- modals, drawers, menus, dropdowns, toasts, tooltips;
- loaders, switches, segmented controls, interactive rows;
- hover, press, drag, attach, detach, loading and idle states;
- UI refactors or design reviews;
- HTML/CSS/JS, React, Astro, Vue, Svelte, Tailwind or equivalent frontend work.

If a bounded UI component exists, this skill applies.

# 1. Non-negotiable palette

```css
:root {
  --ui-graphite: #414141;
  --ui-amber:    #f7c974;
  --ui-cream:    #faeccf;
  --ui-paper:    #f8f8f8;
}
```

Semantic roles:

- `#414141` — graphite: dark surfaces, primary text, dark controls.
- `#f7c974` — amber: primary accent, active mass, loaders, emphasis.
- `#faeccf` — cream: warm secondary surfaces.
- `#f8f8f8` — paper: main light canvas and light surfaces.

Rules:

- Do not invent additional brand colors.
- Do not substitute near-match hex values.
- Alpha variants of these four colors are allowed.
- Prefer flat fills.
- Do not add gradients unless explicitly requested.
- Do not use arbitrary grays, generic blue primaries, `#000`, or `#fff` when a system token serves the role.
- Amber/cream normally use graphite text.
- Graphite normally uses paper or cream text.

# 2. Typography — Urbanist only

Every visible UI text element uses Urbanist.

```css
:root {
  --font-ui: "Urbanist", sans-serif;
}

html,
body,
button,
input,
textarea,
select,
option {
  font-family: var(--font-ui);
}
```

Urbanist is a variable typeface with a weight axis from `100` to `900`.

Use weight for hierarchy:

- `400` body copy;
- `500` secondary labels and compact controls;
- `600` emphasized UI text;
- `700` buttons, tabs, compact headings;
- `800` display headings;
- `900` rare maximum-emphasis display use.

Do not mix in Inter, Geist, Roboto, system-ui, serif, or another display font as an aesthetic choice.

Monospace is allowed only for actual code when semantic readability requires it.

# 3. Geometry — adaptive radius

`9999px` / `rounded-full` is correct for pills and small circles. On a tall box it becomes a stadium: the taller the surface, the more the silhouette distorts. Radius must stay a corner, and get relatively squarer as height grows.

Canonical tokens:

```css
:root {
  --radius-full: 9999px;
  --radius-min: 14px;
  --radius-max: 32px;
  --radius-ui: min(var(--radius-max), 50%);
}
```

Default for every bounded surface that can grow:

```css
border-radius: var(--radius-ui);
```

CSS already clamps a radius to half of each side, so:

- short controls (chips, inputs, buttons, toasts) still read as pills;
- a one-line bubble stays pill-like;
- a tall bubble, tool card, code block or panel keeps soft corners but looks more rectangular.

Use `--radius-full` or `border-radius: 50%` only when the silhouette must remain a circle or a capsule even if the box is large: avatars, loaders, circular icon buttons, dots.

Do not use `8px`, `12px`, `16px`, `rounded-lg`, `rounded-xl`, or other generic radii. Do not scale `--radius-max` up with height; the cap is what makes tall surfaces squarer.

Page canvases, full-bleed sections and invisible layout wrappers are not bounded UI components and therefore do not require a radius.

# 4. Gooey foundation — viscosity fixed at 7

Use one shared SVG goo filter.

`stdDeviation` MUST remain exactly `7` unless the user explicitly changes the system.

```html
<svg width="0" height="0" aria-hidden="true" style="position:absolute">
  <defs>
    <filter
      id="ui-goo"
      x="-50%"
      y="-50%"
      width="200%"
      height="200%"
      color-interpolation-filters="sRGB"
    >
      <feGaussianBlur
        in="SourceGraphic"
        stdDeviation="7"
        result="blur"
      />

      <feColorMatrix
        in="blur"
        type="matrix"
        values="
          1 0 0 0 0
          0 1 0 0 0
          0 0 1 0 0
          0 0 0 22 -10
        "
        result="goo"
      />

      <feComposite
        in="SourceGraphic"
        in2="goo"
        operator="atop"
      />
    </filter>
  </defs>
</svg>
```

```css
.goo-layer {
  filter: url(#ui-goo);
  -webkit-filter: url(#ui-goo);
}
```

Do not expose viscosity as an end-user setting by default.

# 5. Component architecture

Never filter labels, icons, or hit targets if doing so damages clarity.

Preferred structure:

```text
Component
├── visual goo layer
│   ├── base mass
│   └── optional interacting mass
└── crisp UI layer
    ├── label
    ├── icon
    └── hit target
```

Example:

```html
<div class="goo-button">
  <div class="goo-button__visual goo-layer" aria-hidden="true">
    <div class="goo-button__mass"></div>
  </div>

  <button class="goo-button__control">Action</button>
</div>
```

The visual mass may deform. Content stays crisp.

# 6. Universal component rule

Every new component must visibly belong to the same system.

At minimum it inherits:

1. the four-color palette;
2. Urbanist;
3. adaptive radius (`--radius-ui`, or `--radius-full` only for true circles/capsules);
4. soft-mass / surface-tension material language;
5. the same restrained motion philosophy.

Do NOT create default Bootstrap, Material, shadcn, Apple Liquid Glass, glassmorphic, neumorphic, cyberpunk, or generic SaaS components and merely recolor them amber.

When integrating third-party UI:

1. remove its default typography;
2. replace its colors with system tokens;
3. force adaptive radius (`--radius-ui`, `--radius-full` only for circles/capsules);
4. remove incompatible borders/shadows;
5. rebuild relevant interaction states in the gooey material language.

# 7. Motion language

Motion should feel:

```text
soft
slightly viscous
continuous
low-amplitude
surface-tension driven
```

It must not feel like particle effects, rubber-cartoon motion, glass, or random bubbles.

## Idle

Idle motion is optional.

When used:

- keep it subtle;
- deform existing mass instead of spawning decorative particles;
- do not make layout appear unstable;
- avoid attention-seeking perpetual animation.

## Hover

Hover may:

- subtly pull a mass toward a nearby compatible surface;
- slightly compress or stretch the pill;
- change fill within the approved palette;
- create a short gooey bridge when actual surfaces approach.

Hover must NOT:

- turn a pill into a circle;
- spawn random balls;
- explode particles;
- radically alter component dimensions.

## Press

Prefer a small compression plus brief asymmetric deformation and smooth return.

Avoid exaggerated bounce.

## Attach / detach

When two compatible masses approach:

- let the SVG filter create the merge naturally;
- avoid fake connector lines;
- avoid bead chains;
- avoid decorative intermediate spheres.

When they separate:

- preserve a short sense of adhesion;
- allow the neck to narrow naturally;
- return both surfaces smoothly to their resting pill geometry.

# 8. Canonical loader

When a spinner is needed, use the project loader:

- 8 amber dots arranged in a circle;
- one slightly larger amber runner mass;
- slow orbital movement;
- goo viscosity `7`;
- paper, cream, or graphite background depending on context.

Recommended timing:

```css
.loader-ring {
  animation: ring-drift 15s linear infinite;
}

.loader-runner {
  animation:
    runner-orbit
    7.8s
    cubic-bezier(.55, .02, .45, .98)
    infinite;
}
```

The loader should feel viscous and calm, not urgent.

# 9. Borders and depth

Default to color separation instead of borders.

If a border is required:

- derive it from the approved palette;
- keep it subtle;
- preserve adaptive radius (`--radius-ui`).

Avoid heavy shadows.

If elevation is necessary, use a low-opacity graphite-derived shadow.

# 10. Accessibility

The visual system must not override usability.

Required:

- visible focus states;
- keyboard operability;
- semantic HTML;
- sufficient text contrast;
- crisp text outside filtered mass layers;
- reduced-motion handling.

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
}
```

Do not communicate state through slime motion alone.

# 11. Performance

SVG filters can be expensive.

- Apply the filter to the smallest practical visual wrapper.
- Do not filter an entire page.
- Do not filter large text/content trees.
- Avoid dozens of simultaneous filtered animations.
- Reuse one global filter definition where practical.
- Prefer transforms and opacity for animation.
- Keep filter bounds large enough to prevent clipping, but not excessive.

# 12. Baseline tokens

Every implementation should begin from equivalent tokens:

```css
:root {
  --ui-graphite: #414141;
  --ui-amber: #f7c974;
  --ui-cream: #faeccf;
  --ui-paper: #f8f8f8;

  --font-ui: "Urbanist", sans-serif;
  --radius-full: 9999px;
  --radius-min: 14px;
  --radius-max: 32px;
  --radius-ui: min(var(--radius-max), 50%);
  --goo-viscosity: 7;
}
```

For Tailwind, map `--radius-ui` as the default bounded radius. Use `rounded-full` only for true circles and compact capsules that must stay pills at any size.

# 13. Quality gate

Before ANY UI task is complete, verify:

- [ ] Bounded components use `--radius-ui`; `--radius-full` / `50%` only on true circles or compact capsules.
- [ ] No authored brand color exists outside the four approved colors except alpha variants.
- [ ] All intentional UI typography is Urbanist.
- [ ] Typography hierarchy uses Urbanist weights instead of mixed families.
- [ ] Goo viscosity remains exactly `stdDeviation="7"`.
- [ ] Labels/icons remain crisp outside filtered visual layers.
- [ ] No third-party component remains in its default visual style.
- [ ] Hover/press does not spawn arbitrary particles.
- [ ] Pill components remain pills during interaction.
- [ ] Goo merging occurs only between actual visual masses.
- [ ] The slow circular gooey loader is used when a spinner is needed.
- [ ] Reduced-motion behavior exists for animated UI.
- [ ] The full screen reads as one coherent system.

If any item fails, the UI task is not complete.

# 14. Prohibited drift

Do not introduce these unless explicitly requested:

```text
non-Urbanist display fonts
generic system typography
square or mildly rounded (8px / 12px / 16px) component corners
9999px radius on tall cards, messages, panels, or dialogs
new brand colors
glassmorphism
Apple-style Liquid Glass
neumorphism
default shadcn styling
default Material styling
generic blue primary buttons
random blob particles
bubble explosions
hover states that turn pills into circles
goo viscosity other than 7
excessive idle deformation
```

# 15. Design intent

The interface should read as a warm, restrained, tactile product built from one soft material.

The palette supplies the identity.

Urbanist supplies the hierarchy.

Adaptive radius supplies the silhouette: pills when small, squarer when tall.

The viscosity-7 goo filter supplies the material behavior.

These four rules are inseparable.
