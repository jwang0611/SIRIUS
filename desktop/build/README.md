# App icons

`icon.svg` is the master SIRIUS mark. `icon.ico` (Windows), `icon.icns`
(macOS), and `icon.png` (Linux/fallback) are generated from it and committed
here — `electron-builder` picks them up automatically by filename convention.

Regenerate after editing `icon.svg`, from the `desktop/` directory:

```bash
npm run icons
```

This runs `generate-icons.js`, which uses `sharp` (SVG → PNG rasterization)
and `png2icons` (PNG → ICO/ICNS), both pure JS. It intentionally avoids
macOS-only tools (`iconutil`) or system packages (`rsvg-convert`,
`imagemagick`) so it runs the same on any build machine or CI runner.
