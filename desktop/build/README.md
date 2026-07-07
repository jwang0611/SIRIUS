# App icons

`icon.svg` is the master SIRIUS mark. `electron-builder` needs raster icons next
to it before packaging:

- **Windows** — `icon.ico` (multi-size, include 256×256)
- **macOS** — `icon.icns`
- **Linux** (optional) — `icon.png` (512×512)

Generate them from `icon.svg` once, then commit the results:

```bash
# needs: librsvg (rsvg-convert), imagemagick, and (for icns) iconutil/png2icns

# 1) SVG -> high-res PNG
rsvg-convert -w 1024 -h 1024 icon.svg -o icon-1024.png

# 2) PNG -> .ico (Windows)
magick icon-1024.png -define icon:auto-resize=256,128,64,48,32,16 icon.ico

# 3) PNG -> .icns (macOS)
#    On macOS:
mkdir icon.iconset
for s in 16 32 64 128 256 512; do
  sips -z $s $s      icon-1024.png --out icon.iconset/icon_${s}x${s}.png
  sips -z $((s*2)) $((s*2)) icon-1024.png --out icon.iconset/icon_${s}x${s}@2x.png
done
iconutil -c icns icon.iconset -o icon.icns
#    On Linux: png2icns icon.icns icon-1024.png
```

If these files are absent, `electron-builder` still builds using Electron's
default icon (with a warning) — the app is fully functional, just unbranded.
