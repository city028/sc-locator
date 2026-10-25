"""Draws the program icon: a shaded planet with a location pin on it, in the
star map's colours.

    python make_icon.py icon.ico [preview.png]

Each size is scaled down from one large drawing; 32 px and under from a
plainer one without the tile's border.
"""
import sys
from PIL import Image, ImageDraw

S = 1024                                   # drawn at this size, then scaled
BG, GRID, PIN = "#05080c", "#2D5A80", "#FF9F43"
PLANET = ("#1D4E73", "#3E8CC4", "#A8D8FF")


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def blend(cols, t):
    """As main_window.blend: rim (0) to highlight (1) through the middle."""
    a, b = (cols[0], cols[1]) if t < 0.5 else (cols[1], cols[2])
    t = t * 2 if t < 0.5 else (t - 0.5) * 2
    return tuple(round(x + (y - x) * t) for x, y in zip(rgb(a), rgb(b)))


def draw(small):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((16, 16, S - 16, S - 16), radius=200, fill=BG,
                        outline=GRID, width=0 if small else 20)
    # The planet: shaded rim to highlight, as the star map draws a body.
    cx, cy, r = 512, 650, 330
    steps = 120
    for i in range(steps):
        k = 1 - i / steps
        rr, off = r * k, r * (1 - k) * 0.4
        d.ellipse((cx - off - rr, cy - off - rr, cx - off + rr, cy - off + rr),
                  fill=blend(PLANET, i / (steps - 1) * 0.85))
    # The pin, its tip on the planet's upper face.
    hx, hy, hr, tip = 512, 300, 175, 560
    d.polygon([(hx - hr * 0.87, hy + hr * 0.5), (hx + hr * 0.87, hy + hr * 0.5),
               (hx, tip)], fill=PIN, outline=BG, width=0)
    d.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=PIN)
    d.ellipse((hx - 62, hy - 62, hx + 62, hy + 62), fill=BG)
    return im


big, small = draw(False), draw(True)
sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
frames = [(small if n <= 32 else big).resize((n, n), Image.LANCZOS)
          for n in sizes]
frames[-1].save(sys.argv[1], format="ICO", sizes=[(n, n) for n in sizes],
                append_images=frames[:-1])
if len(sys.argv) > 2:                      # a preview strip
    strip = Image.new("RGBA", (sum(sizes) + 10 * len(sizes), 256), "#ffffff")
    x = 0
    for f in frames:
        strip.paste(f, (x, 0), f)
        x += f.width + 10
    strip.save(sys.argv[2])
