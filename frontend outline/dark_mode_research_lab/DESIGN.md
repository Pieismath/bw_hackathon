---
name: Dark Mode Research Lab
colors:
  surface: '#10141a'
  surface-dim: '#10141a'
  surface-bright: '#353940'
  surface-container-lowest: '#0a0e14'
  surface-container-low: '#181c22'
  surface-container: '#1c2026'
  surface-container-high: '#262a31'
  surface-container-highest: '#31353c'
  on-surface: '#dfe2eb'
  on-surface-variant: '#c1c6d7'
  inverse-surface: '#dfe2eb'
  inverse-on-surface: '#2d3137'
  outline: '#8b90a0'
  outline-variant: '#414755'
  surface-tint: '#adc6ff'
  primary: '#adc6ff'
  on-primary: '#002e69'
  primary-container: '#4b8eff'
  on-primary-container: '#00285c'
  inverse-primary: '#005bc1'
  secondary: '#6fdd78'
  on-secondary: '#00390e'
  secondary-container: '#01872e'
  on-secondary-container: '#f7fff1'
  tertiary: '#fabc45'
  on-tertiary: '#422c00'
  tertiary-container: '#bd8708'
  on-tertiary-container: '#392600'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#adc6ff'
  on-primary-fixed: '#001a41'
  on-primary-fixed-variant: '#004493'
  secondary-fixed: '#8bfb91'
  secondary-fixed-dim: '#6fdd78'
  on-secondary-fixed: '#002106'
  on-secondary-fixed-variant: '#005319'
  tertiary-fixed: '#ffdeaa'
  tertiary-fixed-dim: '#fabc45'
  on-tertiary-fixed: '#271900'
  on-tertiary-fixed-variant: '#5f4100'
  background: '#10141a'
  on-background: '#dfe2eb'
  surface-variant: '#31353c'
typography:
  headline-sm:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  data-lg:
    fontFamily: JetBrains Mono
    fontSize: 16px
    fontWeight: '500'
    lineHeight: 24px
  data-md:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  data-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 14px
  label-caps:
    fontFamily: Space Grotesk
    fontSize: 10px
    fontWeight: '700'
    lineHeight: 12px
    letterSpacing: 0.08em
spacing:
  unit: 4px
  gutter: 12px
  margin: 16px
  container-padding: 8px
---

## Brand & Style

This design system is engineered for high-velocity quantitative analysis and mission-critical financial oversight. The brand personality is clinical, authoritative, and obsessively precise, mirroring the aesthetic of high-end scientific instrumentation and legacy Bloomberg Terminals. It serves an audience of institutional traders and data scientists who require maximum information density without cognitive fatigue.

The visual style is **Corporate / Modern** with a **Brutalist** structural influence. It prioritizes functional clarity over decorative flair. Every pixel must justify its existence. The "Zero-Trust" provenance philosophy is manifested through explicit metadata tagging, timestamping of every data point, and modular containers that suggest a "hot-swappable" workspace environment.

## Colors

The palette is strictly functional, utilizing a "Lights-Out" philosophy where color is reserved exclusively for status signaling and primary actions.

- **Backgrounds:** Use `#0D1117` for the global canvas and `#161B22` for modular panes/cards to create subtle separation.
- **Action & Highlight:** Bridgewater Blue (`#007AFF`) is the surgical tool; use it for active states, primary buttons, and focus indicators.
- **Semantic Feedback:** Success Green, Warning Orange, and Danger Red are utilized with high saturation to ensure immediate recognition against the deep gray base. 
- **Borders:** Internal dividers use `#30363D` to maintain structure without creating high-contrast visual noise.

## Typography

This design system employs a dual-font strategy to differentiate between interface scaffolding and raw intelligence.

- **UI & Navigation:** Use **Inter** for all labels, descriptions, and navigational elements. It provides the necessary neutrality for a professional tool.
- **Quantitative Data:** All financial figures, timestamps, hashes, and ticker symbols must use **JetBrains Mono**. Monospaced alignment is critical for scanning vertical columns of numbers.
- **Metadata Labels:** Use **Space Grotesk** in uppercase for micro-labels and provenance tags (e.g., "SOURCE: REUTERS" or "LATENCY: 12ms") to provide a distinct "technical" character.
- **Hierarchy:** Maintain high density by keeping body text at 13px-14px. Headers should remain compact; scale is less important than weight and color for hierarchy.

## Layout & Spacing

The layout utilizes a **Fluid Grid** model optimized for ultra-wide monitors. The workspace is divided into modular "Cells" that snap to a 4px baseline grid.

- **Density:** Spacing is tight to maximize screen real estate. Use 8px for internal component padding and 12px for gutters between major modules.
- **Modules:** Content is organized into "Widgets" or "Panes." Each pane is independent and should be able to scroll horizontally if data tables exceed the width.
- **Alignment:** All data columns must be right-aligned if numeric, and left-aligned if alphabetical. This ensures the Monospace font's vertical rhythm is preserved.

## Elevation & Depth

In a high-density dark mode environment, shadows are ineffective. This design system uses **Tonal Layers** and **Low-contrast outlines** to define hierarchy.

- **Level 0 (Base):** `#0D1117` - The main application shell.
- **Level 1 (Surface):** `#161B22` - Individual module backgrounds.
- **Level 2 (Interaction):** `#21262D` - Hover states, dropdown menus, and active input fields.
- **Borders:** Every module must have a 1px solid border (`#30363D`). This "Cellular" approach reinforces the modular, high-end software vibe. No shadows are permitted except for floating modal dialogs, which should use a sharp, 0-spread black shadow to pop from the background.

## Shapes

The shape language is **Sharp (0px)**. 

To evoke the feel of precision engineering and terminal software, rounded corners are eliminated entirely. Every button, input, and container features hard 90-degree angles. This maximizes every available pixel and aligns with the brutalist, scientific nature of the platform. Focus states are indicated by 1px inset borders rather than external glows.

## Components

- **Buttons:** Rectangular, no radius. Primary buttons use a solid Bridgewater Blue fill with white text. Secondary buttons use a ghost style with a 1px border.
- **Data Tables:** The core of the design system. Use alternating row zebra-striping (`#161B22` and `#0D1117`). Headers are sticky, using Space Grotesk caps.
- **Provenance Tags:** Micro-components that sit at the corner of widgets. They indicate "Live," "Cached," or "Verified" status using small 6px color pips and 10px monospace text.
- **Input Fields:** Darker than the surface (`#0D1117`), with a subtle border. On focus, the border changes to Bridgewater Blue.
- **Chips:** Strictly rectangular. Used for active filters or data categories. They should look like segments of a status bar.
- **Value Indicators:** Use "Success Green" for positive deltas and "Danger Red" for negative deltas, accompanied by ▲/▼ geometric arrows.
- **Scrollbars:** Custom-styled to be ultra-thin (4px) and non-obtrusive, using `#30363D` for the track.