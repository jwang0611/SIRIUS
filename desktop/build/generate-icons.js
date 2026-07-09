// Regenerates icon.ico / icon.icns / icon.png from icon.svg.
//
// Pure-JS (sharp + png2icons) on purpose: no dependency on macOS-only tools
// (iconutil) or system packages (rsvg-convert, imagemagick), so this runs the
// same way on any build machine or CI runner. Re-run after editing icon.svg:
//
//   cd desktop && node build/generate-icons.js

const fs = require("fs");
const path = require("path");
const sharp = require("sharp");
const png2icons = require("png2icons");

const BUILD_DIR = __dirname;
const SVG_PATH = path.join(BUILD_DIR, "icon.svg");

async function main() {
  const svg = fs.readFileSync(SVG_PATH);
  const png1024 = await sharp(svg, { density: 384 }).resize(1024, 1024).png().toBuffer();

  fs.writeFileSync(path.join(BUILD_DIR, "icon.png"), await sharp(png1024).resize(512, 512).png().toBuffer());

  const ico = png2icons.createICO(png1024, png2icons.BICUBIC, 0, false, true);
  if (!ico) throw new Error("ICO generation failed");
  fs.writeFileSync(path.join(BUILD_DIR, "icon.ico"), ico);

  const icns = png2icons.createICNS(png1024, png2icons.BICUBIC, 0);
  if (!icns) throw new Error("ICNS generation failed");
  fs.writeFileSync(path.join(BUILD_DIR, "icon.icns"), icns);

  console.log("Wrote icon.png, icon.ico, icon.icns to", BUILD_DIR);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
