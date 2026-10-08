#!/usr/bin/env python3
"""Generate static/logo.svg: the All Saints' Church (诸圣堂) logo, redrawn for the app.

The characters are traced from a heavy system font into SVG paths (so the logo
looks the same on every device) and coloured by the viewer's light/dark setting
(light characters on dark backgrounds, dark on light). The starburst stays yellow.

    .venv/bin/python tools/make_logo.py [--font PATH] [--index N]
"""
import argparse
import math
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTCollection, TTFont

ap = argparse.ArgumentParser()
ap.add_argument("--font", default="/System/Library/Fonts/Hiragino Sans GB.ttc")
ap.add_argument("--index", type=int, default=2)  # W6 (bold)
args = ap.parse_args()

font = TTCollection(args.font).fonts[args.index] if args.font.endswith(".ttc") else TTFont(args.font)
cmap, glyphs = font.getBestCmap(), font.getGlyphSet()
upm = font["head"].unitsPerEm

SIZE = 100            # character size in SVG units
GAP = 6               # extra space between characters
scale = SIZE / upm
paths, x = [], 0.0
for ch in "诸圣堂":
    g = glyphs[cmap[ord(ch)]]
    pen = SVGPathPen(glyphs)
    # font units (y up) -> SVG (y down), baseline at y = 88
    g.draw(TransformPen(pen, (scale, 0, 0, -scale, x, 88)))
    paths.append(pen.getCommands())
    x += g.width * scale + GAP
width = x - GAP


def star(cx, cy, rays, r_long, r_short, r_inner, rotate):
    """A starburst: rays alternating long/short, very thin at the base."""
    pts = []
    for i in range(rays * 2):
        a = math.radians(rotate + i * 180 / rays)
        r = (r_long if (i // 2) % 2 == 0 else r_short) if i % 2 == 0 else r_inner
        pts.append(f"{cx + r * math.sin(a):.1f},{cy - r * math.cos(a):.1f}")
    return " ".join(pts)


cx = width / 2  # over the middle character, like the original
burst = star(cx, 36, rays=10, r_long=58, r_short=38, r_inner=6, rotate=12)

svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="-4 -20 {width + 8:.0f} 150" role="img" aria-label="诸圣堂 All Saints' Church">
  <style>.ink {{ fill: #2b2b2d; }} @media (prefers-color-scheme: dark) {{ .ink {{ fill: #ececef; }} }}</style>
  <g class="ink">{"".join(f'<path d="{d}"/>' for d in paths)}</g>
  <polygon points="{burst}" fill="#FFD84D" stroke="#F2B705" stroke-width="1.2" stroke-linejoin="round"/>
  <text class="ink" x="{width / 2:.1f}" y="124" text-anchor="middle"
        font-family="Montserrat, 'Avenir Next', 'Helvetica Neue', Arial, sans-serif"
        font-size="17" font-weight="600" letter-spacing="7.2">ALL SAINTS’ CHURCH</text>
</svg>
'''
out = Path(__file__).resolve().parent.parent / "static" / "logo.svg"
out.write_text(svg, encoding="utf-8")
print(f"wrote {out} ({len(svg) // 1024} KB)")
