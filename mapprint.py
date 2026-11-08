"""Maps on paper: a map's pages (maps.py) drawn with PIL, from the surveys
and waypoints as they are now.

No tkinter here, so all of it can be tested without a window. The Maps tab
(main_window.py) shows the pages small as a preview, and they are printed
at PRINT_DPI: every size is in mm on the page (type in pt), turned into
pixels at the dpi asked for, so the preview and the print are one page.

Page 1, A4 portrait as maps.PAGE_MM lays it out: the map's name over the
frame, the LAT/LON figures in the white band round it, the scale bar, the
scale and the contour interval under it. In the frame, the map as the
height map draws it with the map's own options (maps.view_switches, its
levels, opacities and kinds left out), on white where nothing was measured,
the north arrow if shown (maps.compass_box), and the legend in its box
(maps.legend_box): what is on the map, a line
each. With waypoint names off, each waypoint has a number by its icon
(maps.numbered) and the legend a line for it; with names on, a line for
each kind of icon. What does not fit in the box goes on page 2.

Page 2, and more if it runs over (later_pages): the legend's lines that did
not fit in its box, what the map is for (its notes), the heights of what is
in the frame (height_rows: a table, highest first, and beside it a profile
from the lowest on the left to the highest on the right, its top and bottom
level with the table's first and last rows), and the notes of each survey
shown in its frame. A map with none of these has page 1 only.

The lines' widths and patterns, the icons and the like are the height
map's, in its px: each such px is SCREEN_PX_MM on paper.

A map can be printed in black and white (maps.ink_of): PALETTES["bw"] for
what colour told apart, the relief a light grey by height, water a light
grey with fine lines across it, an area's fill hatching (HATCHES) instead
of a wash, and the page saved as grey ("L") so nothing else stays coloured.
"""
import math
import time

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont

import heightmap
import maps
import starmap
import subtypes
import traces

PRINT_DPI = 300
SCREEN_PX_MM = 0.26     # one of the height map's px, on paper
PAPER = "#FFFFFF"       # ground nobody measured, and the page
INK, GREY_INK = "#000000", "#5F6368"
GRID_INK = "#8A9096"            # LAT/LON lines
# As the height map's (main_window), on paper.
CONTOUR, CONTOUR_INDEX = "#C8925A", "#9C6A35"
WATER, SHORE = (169, 212, 239), (42, 122, 184)
RAIL, RAIL_GAP = "#000000", "#FFFFFF"
BUILDING, BUILDING_EDGE = "#F2C49B", "#3A2A1E"
PIN, BADGE = "#FF9F43", "#05080C"
# What changes in black and white: grid, height lines (and the index ones),
# water and its shore, buildings (fill, edge, name), a waypoint's icon
# (ring and glyph, badge) and plain dot (fill, edge), a lake's level.
PALETTES = {
    "colour": {"grid": GRID_INK, "contour": CONTOUR, "index": CONTOUR_INDEX,
               "water": WATER, "shore": SHORE, "building": BUILDING,
               "edge": BUILDING_EDGE, "name": BUILDING_EDGE, "pin": PIN,
               "badge": BADGE, "dot": PIN, "dot_edge": BADGE,
               "level": "#%02x%02x%02x" % SHORE},
    "bw": {"grid": "#B4B4B4", "contour": "#9A9A9A", "index": "#5A5A5A",
           "water": (238, 238, 238), "shore": (0, 0, 0),
           "building": "#5A5A5A", "edge": "#000000", "name": "#FFFFFF",
           "pin": "#FFFFFF", "badge": "#000000", "dot": "#000000",
           "dot_edge": "#FFFFFF", "level": "#000000"},
}
# In black and white an area is hatched, not washed: lines the way named,
# HATCH_MM apart, as dark as its opacity makes them. Kinds not named keep
# no fill (a swamp has its tufts).
HATCHES = {"built-up-area": "diagonal", "hostile-area": "cross"}
HATCH_MM = 1.6
WATER_LINES_MM = 0.9    # the fine lines across water in black and white
FONT_FILES = {False: ("consola.ttf", "arial.ttf"),
              True: ("consolab.ttf", "arialbd.ttf")}


def mm(v, dpi):
    """`v` mm in px at `dpi`."""
    return v * dpi / 25.4


def font(pt, dpi, bold=False):
    """Consolas (the program's own) at `pt` points, else Arial, else PIL's."""
    size = max(1, round(pt * dpi / 72))
    for name in FONT_FILES[bold]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def fmt_dist(m):
    """A length for the scale bar: 500 m, 2 km, 1.5 km."""
    return f"{m / 1000:g} km" if m >= 1000 else f"{m:g} m"


def bar_length(width_m):
    """The scale bar's length: a round 1, 2 or 5 to a power of ten, up to
    a quarter of the frame's width."""
    want = width_m / 4
    step = 10 ** math.floor(math.log10(want))
    return max(k * step for k in (1, 2, 5) if k * step <= want)


def gather(m, rec, radius, all_traces, waypoints, offset_m, system=None):
    """What drawing map `m` needs, copied, so it can be drawn without the
    lock held: call with it held. `rec` is its body's height map record,
    `offset_m` the building wall offset a building walked before it was
    kept takes (building_wall_offset_m), `system` the body's star system
    for the title, if known."""
    body = m["body"]
    frame = heightmap.local_frame(m["centre"])
    half_w = m["width_m"] / 2
    half_h = maps.height_m(m["width_m"]) / 2
    return {
        "radius": radius,
        # Cells just off the frame still shape its contours near the edge.
        "around": heightmap.local_cells(rec, radius, frame,
                                        half_w + heightmap.REACH_M,
                                        half_h + heightmap.REACH_M),
        "near": heightmap.local_cells(rec, radius, frame, half_w, half_h),
        "body_range": heightmap.height_range(
            v[0] for v in (rec["cells"].values() if rec else ())),
        "traces": [dict(t, points=[list(p) for p in t["points"]])
                   for t in all_traces if t["planet"] == body],
        "waypoints": [dict(w) for w in waypoints if w.get("planet") == body],
        "offset_m": offset_m,
        "system": system,
    }


class Pen:
    """Drawing on one image at one dpi: px from mm, pt and the height map's
    px (`k` px a screen px), in an ink's palette (PALETTES)."""

    def __init__(self, img, dpi, ink="colour"):
        self.img, self.dpi = img, dpi
        self.draw = ImageDraw.Draw(img)
        self.k = mm(SCREEN_PX_MM, dpi)
        self.bw = ink == "bw"
        self.pal = PALETTES[ink]

    def mm(self, v):
        return mm(v, self.dpi)

    def font(self, pt, bold=False):
        return font(pt, self.dpi, bold)

    def wash(self, paint):
        """`paint(draw)` on a see-through layer, laid over the image."""
        layer = Image.new("RGBA", self.img.size, (0, 0, 0, 0))
        paint(ImageDraw.Draw(layer))
        self.img.alpha_composite(layer)
        self.draw = ImageDraw.Draw(self.img)

    def stroke(self, px, kind):
        """A line of px in its kind's style (traces.pattern), colour and
        width, the pattern at the height map's size."""
        k, colour = self.k, kind["colour"]
        runs, dots = traces.pattern([(x / k, y / k) for x, y in px],
                                    kind["style"])
        width = max(1, round(kind["width"] * k))
        for run in runs:
            self.draw.line([(x * k, y * k) for x, y in run], fill=colour,
                           width=width, joint="curve")
        r = max(1.5, 0.6 * kind["width"]) * k
        for x, y in dots:
            self.draw.ellipse((x * k - r, y * k - r, x * k + r, y * k + r),
                              fill=colour)

    def text(self, xy, text, pt, fill=INK, anchor="mm", bold=False,
             patch=None):
        """`text` at xy, on a patch of `patch` colour if given."""
        f = self.font(pt, bold)
        if patch:
            box = self.draw.textbbox(xy, text, font=f, anchor=anchor)
            pad = self.k
            self.draw.rectangle((box[0] - pad, box[1] - pad, box[2] + pad,
                                 box[3] + pad), fill=patch)
        self.draw.text(xy, text, font=f, fill=fill, anchor=anchor)

    def hatch(self, inside, style, grey):
        """Hatch the px where `inside` (a bool array the image's size) is
        true: "diagonal" lines, "cross" both ways or "across" (level),
        HATCH_MM apart (WATER_LINES_MM across), in `grey` (0..255)."""
        h, w = inside.shape
        y, x = np.indices((h, w))
        step = self.mm(WATER_LINES_MM if style == "across" else HATCH_MM)
        thick = max(1.0, self.mm(0.14))
        if style == "across":
            lines = (y % step) < thick
        else:
            lines = ((x + y) % step) < thick * 1.4
            if style == "cross":
                lines |= ((x - y) % step) < thick * 1.4
        # Pasted through a mask, so it hatches a page (RGB: a legend line
        # gone over to page 2) as well as the map (RGBA).
        mask = Image.fromarray(((inside & lines) * 255).astype(np.uint8), "L")
        colour = (grey,) * 3 + ((255,) if self.img.mode == "RGBA" else ())
        self.img.paste(colour, mask=mask)
        self.draw = ImageDraw.Draw(self.img)


def page1(m, data, dpi):
    """Map `m`'s first page at `dpi`, from `data` (gather): (RGB image, the
    waypoint numbers it was drawn with, {id: n}, the legend's lines that did
    not fit in its box, for page 2)."""
    page = Image.new("RGB", (round(mm(maps.PAGE_MM[0], dpi)),
                             round(mm(maps.PAGE_MM[1], dpi))), PAPER)
    left, top, right, bottom = (mm(v, dpi) for v in maps.frame_box())
    fw, fh = round(right - left), round(bottom - top)
    ink = maps.ink_of(m)
    body, info = draw_map(m, data, fw, fh, dpi)
    edges, interval = info["edges"], info["interval"]
    pen = Pen(body, dpi, ink)
    lx0, ly0, lx1, ly1 = (pen.mm(v) for v in maps.legend_box(m))
    overflow = draw_legend(pen, (lx0, ly0, lx1, ly1),
                           legend_lines(m, data, info))
    compass = maps.compass_of(m)
    if compass["on"]:
        draw_compass(pen, [pen.mm(v) for v in maps.compass_box(m)],
                     compass["plate"])
    page.paste(body.convert("RGB"), (round(left), round(top)))
    pen = Pen(page, dpi, ink)
    d = pen.draw
    d.rectangle((round(left), round(top), round(left) + fw,
                 round(top) + fh), outline=INK, width=max(1, round(pen.mm(.3))))
    # The title, and the body and day printed.
    margin = pen.mm(maps.MARGIN_MM)
    mid = margin + pen.mm(maps.TITLE_MM) / 2
    pen.text((margin, mid), m["name"], 16, anchor="lm", bold=True)
    pen.text((page.width - margin, mid), where(m, data), 8, GREY_INK,
             anchor="rm")
    # The LAT/LON figures, in the band, where each line meets the frame.
    band = pen.mm(maps.BAND_MM)
    for side, at, text in edges:
        if side == "top":
            pen.text((left + at, top - band / 2), text, 6.5, GREY_INK)
        elif side == "bottom":
            pen.text((left + at, bottom + band / 2), text, 6.5, GREY_INK)
        else:
            f = pen.font(6.5)
            box = d.textbbox((0, 0), text, font=f)
            tag = Image.new("RGB", (box[2] - box[0] + 2, box[3] - box[1] + 2),
                            PAPER)
            ImageDraw.Draw(tag).text((1 - box[0], 1 - box[1]), text, font=f,
                                     fill=GREY_INK)
            tag = tag.rotate(90, expand=True)
            x = left - band / 2 if side == "left" else right + band / 2
            page.paste(tag, (round(x - tag.width / 2),
                             round(top + at - tag.height / 2)))
    # The foot: the scale bar, in black and white quarters, then the scale
    # and the contour interval.
    foot = bottom + band + pen.mm(maps.FOOT_MM) / 2
    length = bar_length(m["width_m"])
    bar = length / m["width_m"] * fw
    thick = pen.mm(1.2)
    for i in range(4):
        x0 = left + bar * i / 4
        d.rectangle((x0, foot - thick / 2, x0 + bar / 4, foot + thick / 2),
                    fill=INK if i % 2 == 0 else PAPER, outline=INK,
                    width=max(1, round(pen.mm(.15))))
    pen.text((left, foot - thick), "0", 6.5, anchor="mb")
    pen.text((left + bar, foot - thick), fmt_dist(length), 6.5, anchor="mb")
    words = [f"1:{maps.scale(m['width_m']):,}"]
    if interval:
        words.append(f"contour interval {interval:g} m")
    pen.text((right, foot), "    ".join(words), 7, anchor="rm")
    lat, lon = heightmap.latlon(m["centre"])
    pen.text(((left + right) / 2, foot), f"centre map  LAT {lat:+.4f}  "
             f"LON {lon:+.4f}", 7)
    if pen.bw:
        page = page.convert("L")
    return page, info["numbers"], overflow


def where(m, data):
    """The title band's right: the star system (if known), the body and
    the day printed."""
    parts = [x.capitalize() for x in (data.get("system"), m["body"]) if x]
    return "   ".join(parts + [time.strftime("%Y-%m-%d")])


# Page 2 on: type and spacing, mm.
TEXT_PT, SUB_PT, HEAD_PT = 8, 8.5, 10
TEXT_MM, SUB_MM, HEAD_MM = 3.8, 5.0, 7.0     # a line of each, with its gap
LEGEND_COLUMNS = 4                           # across the page
HEIGHT_ROW_MM = 5.2         # a row of the heights table
HEIGHT_FOOT_MM = 6.0        # under it: the height lines' levels
HEIGHT_TABLE_MM = 105.0     # the table's width; the profile is right of it
HEIGHT_GAP_MM = 17.0        # between them, for the profile's height figures
HEIGHT_PT = 7.5


def later_pages(m, data, dpi, overflow=()):
    """Map `m`'s pages after the first, at `dpi`: the legend lines in
    `overflow` (page1's), the map's notes, then the notes of each survey
    shown in its frame (noted_traces), as many pages as they take: [RGB
    image]. [] if there is none of these."""
    ink = maps.ink_of(m)
    probe = Pen(Image.new("RGB", (1, 1)), dpi)
    width = probe.mm(maps.PAGE_MM[0] - 2 * maps.MARGIN_MM)
    items = []      # (kind, what, mm down it takes)
    if overflow:
        items.append(("head", "Legend (continued)", HEAD_MM))
        for i in range(0, len(overflow), LEGEND_COLUMNS):
            items.append(("legend", overflow[i:i + LEGEND_COLUMNS],
                          LEGEND_ROW_MM))
    if (m.get("notes") or "").strip():
        items.append(("head", "About this map", HEAD_MM))
        items += [("text", line, TEXT_MM) for line in wrap(
            probe, m["notes"], probe.font(TEXT_PT), width)]
    rows, levels, interval = height_rows(m, data)
    if rows:
        top = maps.MARGIN_MM + maps.TITLE_MM + 2
        bottom = maps.PAGE_MM[1] - maps.MARGIN_MM - 6
        most = int((bottom - top - HEAD_MM - HEIGHT_FOOT_MM) // HEIGHT_ROW_MM
                   - 1)
        for i in range(0, len(rows), most):
            chunk = rows[i:i + most]
            items.append(("head", "Heights" if i == 0
                          else "Heights (continued)", HEAD_MM))
            items.append(("heights", (chunk, levels, interval),
                          HEIGHT_ROW_MM * (len(chunk) + 1) + HEIGHT_FOOT_MM))
    ts = noted_traces(m, data)
    if ts:
        items.append(("head", "Survey notes", HEAD_MM))
        for t in ts:
            kind = traces.KINDS[t["kind"]]["menu"]
            title = (f"{t['label']} ({kind}, {t['name']})" if t.get("label")
                     else f"{t['name']} ({kind})")
            items.append(("sub", title, SUB_MM))
            items += [("text", line, TEXT_MM) for line in wrap(
                probe, t["notes"], probe.font(TEXT_PT), width)]
    if not items:
        return []
    # Down the pages: a heading never last on a page, away from what it
    # heads.
    top = maps.MARGIN_MM + maps.TITLE_MM + 2
    bottom = maps.PAGE_MM[1] - maps.MARGIN_MM - 6
    laid, y = [[]], top
    for i, (kind, what, tall) in enumerate(items):
        need = tall + (items[i + 1][2] if kind in ("head", "sub")
                       and i + 1 < len(items) else 0)
        if y + need > bottom and laid[-1]:
            laid.append([])
            y = top
        laid[-1].append((kind, what, y))
        y += tall
    pages = []
    for n, lines in enumerate(laid, start=2):
        page = Image.new("RGB", (round(mm(maps.PAGE_MM[0], dpi)),
                                 round(mm(maps.PAGE_MM[1], dpi))), PAPER)
        pen = Pen(page, dpi, ink)
        title_page(pen, m, data, f"page {n} of {len(laid) + 1}")
        x0 = pen.mm(maps.MARGIN_MM)
        column = width / LEGEND_COLUMNS
        for kind, what, y in lines:
            if kind == "head":
                pen.text((x0, pen.mm(y + HEAD_MM - 1.8)), what, HEAD_PT,
                         anchor="ls", bold=True)
            elif kind == "sub":
                pen.text((x0, pen.mm(y + SUB_MM - 1.2)), what, SUB_PT,
                         anchor="ls", bold=True)
            elif kind == "text":
                pen.text((x0, pen.mm(y + TEXT_MM - 1.0)), what, TEXT_PT,
                         anchor="ls")
            elif kind == "heights":
                draw_heights(pen, x0, pen.mm(y), width, *what)
            else:
                for c, line in enumerate(what):
                    cx = x0 + c * column
                    draw_line_of_legend(pen, (cx, pen.mm(y),
                                              cx + column - pen.mm(2),
                                              pen.mm(y + LEGEND_ROW_MM)),
                                        line)
        pages.append(page.convert("L") if pen.bw else page)
    return pages


def height_rows(m, data):
    """The heights of what map `m` shows in its frame, highest first:
    ([(what, kind, height m)], the height lines' levels between the lowest
    and highest, the contour interval if height lines are drawn else
    None). The highest and lowest
    ground measured (if the height map is shown; not under a lake), each
    waypoint of the types shown that has a height (its map number after
    its name with names off), each lake's and sea's level."""
    frame = heightmap.local_frame(m["centre"])
    radius = data["radius"]
    on = maps.view_switches(m)
    hidden = set(m.get("hidden", ()))
    half_w = m["width_m"] / 2
    half_h = maps.height_m(m["width_m"]) / 2
    rows = []
    lakes = []
    for t in data["traces"]:
        if (t["kind"] in traces.WATER
                and len(t["points"]) >= traces.KINDS[t["kind"]]["least"]
                and in_frame(t, frame, radius, half_w, half_h)):
            runs = [[heightmap.to_map(p, frame, radius) for p in run]
                    for run in traces.parts(t)]
            if all(None not in run for run in runs):
                lakes.append((runs,))
            if (on["lake"] and t["kind"] not in hidden
                    and t.get("level") is not None):
                rows.append((t.get("label") or t["name"],
                             f"{traces.KINDS[t['kind']]['menu']} level",
                             t["level"]))
    ground = "heightmap" not in hidden
    cells = outside(data["near"], lakes) if ground else []
    if cells:
        rows.append(("Highest ground measured", "ground",
                     max(c[2] for c in cells)))
        rows.append(("Lowest ground measured", "ground",
                     min(c[2] for c in cells)))
    numbers = m.get("numbers") or {}
    for w in data["waypoints"]:
        if (not on.get(w.get("type", "poi"), on["poi"])
                or w.get("elev") is None
                or w.get("frame", "planet") != "planet"):
            continue
        xy = heightmap.to_map((w["x"], w["y"], w["z"]), frame, radius)
        if not xy or abs(xy[0]) > half_w or abs(xy[1]) > half_h:
            continue
        name = w["name"]
        if not on["names"] and w["id"] in numbers:
            name = f"{name} ({numbers[w['id']]} on the map)"
        rows.append((name, w.get("sub") or w.get("type", "poi"),
                     w["elev"]))
    rows.sort(key=lambda r: -r[2])
    if not rows:
        return [], [], None
    interval = heightmap.contour_interval(*data["body_range"])
    lo, hi = rows[-1][2], rows[0][2]
    levels = [v * interval for v in range(math.floor(lo / interval) + 1,
                                          math.ceil(hi / interval))]
    return rows, levels, interval if cells and on["lines"] else None


def draw_heights(pen, x0, y0, width, rows, levels, interval):
    """The heights table at (x0, y0) px, `width` across: #, what, kind,
    height, highest first; under it the height lines' levels, if drawn
    (`interval` not None).
    Right of it the profile: each row's height as a point, lowest on the
    left to highest on the right, joined by a line, numbered as the table;
    its top level with the first row and its bottom with the last, the
    height lines' levels faint across it."""
    row = pen.mm(HEIGHT_ROW_MM)
    tw = pen.mm(HEIGHT_TABLE_MM)
    cols = (x0, x0 + pen.mm(7), x0 + pen.mm(66), x0 + tw)
    for x, text, anchor in ((cols[0], "#", "lm"), (cols[1], "What", "lm"),
                            (cols[2], "Kind", "lm"),
                            (cols[3], "Height m", "rm")):
        pen.text((x, y0 + row / 2), text, HEIGHT_PT, GREY_INK, anchor=anchor,
                 bold=True)
    pen.draw.line((x0, y0 + row, x0 + tw, y0 + row), fill=INK,
                  width=max(1, round(pen.mm(.25))))
    f = pen.font(HEIGHT_PT)

    def cut(text, room):
        if pen.draw.textlength(text, font=f) <= room:
            return text
        while text and pen.draw.textlength(text + "...", font=f) > room:
            text = text[:-1]
        return text + "..."

    ys = []
    for n, (what, kind, z) in enumerate(rows, start=1):
        y = y0 + row * n + row / 2
        ys.append(y)
        pen.text((cols[0], y), str(n), HEIGHT_PT, anchor="lm", bold=True)
        pen.text((cols[1], y), cut(what, cols[2] - cols[1] - pen.mm(2)),
                 HEIGHT_PT, anchor="lm")
        pen.text((cols[2], y), cut(kind, pen.mm(22)), HEIGHT_PT, GREY_INK,
                 anchor="lm")
        pen.text((cols[3], y), f"{z:,.0f}", HEIGHT_PT, anchor="rm")
    if interval:
        pen.text((x0, y0 + row * (len(rows) + 1) + pen.mm(3)),
                 f"Height lines every {interval:g} m"
                 + (": " + ", ".join(f"{v:,.0f}" for v in levels)
                    if levels else ""), 7, GREY_INK, anchor="lm")
    if len(rows) < 2:
        return
    # The profile, its top and bottom level with the first and last rows.
    px0 = x0 + tw + pen.mm(HEIGHT_GAP_MM)
    px1 = x0 + width - pen.mm(2)
    top, bottom = ys[0], ys[-1]
    hi, lo = rows[0][2], rows[-1][2]

    def y_of(z):
        return (top + bottom) / 2 if hi == lo else \
            bottom - (z - lo) / (hi - lo) * (bottom - top)

    for v in levels:
        y = y_of(v)
        pen.draw.line((px0, y, px1, y), fill=(205, 208, 212), width=1)
        # Its figure, unless it would run into the highest's or lowest's.
        if min(abs(y - top), abs(y - bottom)) >= pen.mm(3):
            pen.text((px0 - pen.mm(1.5), y), f"{v:,.0f}", 6.5, GREY_INK,
                     anchor="rm")
    for z in (hi, lo):
        pen.text((px0 - pen.mm(1.5), y_of(z)), f"{z:,.0f}", 6.5, INK,
                 anchor="rm")
    count = len(rows)
    pts = [(px0 + i * (px1 - px0) / (count - 1), y_of(z), count - i)
           for i, (_, _, z) in enumerate(reversed(rows))]
    pen.draw.line([(x, y) for x, y, _ in pts], fill=INK,
                  width=max(1, round(pen.mm(.45))), joint="curve")
    r = pen.mm(1.1)
    # Every point numbered where there is room for it; else every few.
    room = (px1 - px0) / (count - 1)
    every = max(1, math.ceil(pen.mm(4) / room))
    for i, (x, y, n) in enumerate(pts):
        pen.draw.ellipse((x - r, y - r, x + r, y + r), fill=INK,
                         outline=PAPER, width=max(1, round(pen.mm(.35))))
        if i % every == 0 or i == count - 1:
            pen.text((x, y - pen.mm(2.4)), str(n), 6.5, anchor="ms",
                     bold=True)


def title_page(pen, m, data, right):
    """The title band of a page after the first: the map's name, then its
    system and body, the day and `right` (which page)."""
    margin = pen.mm(maps.MARGIN_MM)
    mid = margin + pen.mm(maps.TITLE_MM) / 2
    pen.text((margin, mid), m["name"], 16, anchor="lm", bold=True)
    pen.text((pen.img.width - margin, mid), f"{where(m, data)}   {right}", 8,
             GREY_INK, anchor="rm")
    y = margin + pen.mm(maps.TITLE_MM)
    pen.draw.line((margin, y, pen.img.width - margin, y), fill=INK,
                  width=max(1, round(pen.mm(0.2))))


def wrap(pen, text, font, width):
    """`text` as the lines it takes `width` px wide in `font`: its own line
    breaks kept, a blank line kept, and a word too long for a line cut."""
    out = []
    for para in text.strip().splitlines():
        words, line = para.split(), ""
        if not words:
            out.append("")
            continue
        for word in words:
            trial = f"{line} {word}" if line else word
            if pen.draw.textlength(trial, font=font) <= width:
                line = trial
                continue
            if line:
                out.append(line)
            while pen.draw.textlength(word, font=font) > width:
                cut = len(word) - 1
                while cut > 1 and pen.draw.textlength(word[:cut],
                                                      font=font) > width:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    return out


def noted_traces(m, data):
    """The surveys shown in map `m`'s frame that have notes, in the order
    traces.LAYERS has their kinds, then by name or label A to Z."""
    frame = heightmap.local_frame(m["centre"])
    on = maps.view_switches(m)
    hidden = set(m.get("hidden", ()))
    half_w = m["width_m"] / 2
    half_h = maps.height_m(m["width_m"]) / 2
    order = list(traces.LAYERS)
    ts = [t for t in data["traces"]
          if (t.get("notes") or "").strip() and t["kind"] not in hidden
          and on.get(traces.KINDS[t["kind"]]["switch"], True)
          and in_frame(t, frame, data["radius"], half_w, half_h)]
    return sorted(ts, key=lambda t: (order.index(t["kind"]),
                                     (t.get("label") or t["name"]).lower()))


def draw_map(m, data, w, h, dpi):
    """The map in its frame, w x h px at `dpi`: (RGBA image, info), info
    {"edges": where the LAT/LON lines meet its edges, (side, px along it,
    figure); "interval": the contour interval if height lines are drawn,
    else None; "range": (lo, hi) m of the relief if drawn, else None;
    "kinds": the survey kinds drawn in it; "waypoints": [(id, waypoint)]
    drawn in it; "numbers": the waypoint numbers, as maps.numbered}."""
    radius = data["radius"]
    frame = heightmap.local_frame(m["centre"])
    mpp = m["width_m"] / w
    half_w, half_h = w / 2 * mpp, h / 2 * mpp
    on = maps.view_switches(m)
    lay = traces.layers(m.get("layers"))
    hidden = set(m.get("hidden", ()))
    ground = "heightmap" not in hidden
    img = Image.new("RGBA", (w, h), PAPER)
    pen = Pen(img, dpi, maps.ink_of(m))
    k = pen.k

    def to_px(xys):
        return [(w / 2 + x / mpp, h / 2 - y / mpp) for x, y in xys]

    def on_map(points):
        xys = [heightmap.to_map(p, frame, radius) for p in points]
        return None if None in xys else xys

    ts = data["traces"]
    lake_xys = []
    for t in ts:
        if (t["kind"] in traces.WATER
                and len(t["points"]) >= traces.KINDS[t["kind"]]["least"]):
            runs = [on_map(run) for run in traces.parts(t)]
            if None not in runs:
                lake_xys.append((runs, t.get("level"), t["kind"]))
    # Over a lake the HUD's ground is neither the bed nor the surface: the
    # cells there shape no contour, shade or colour (as on the height map).
    around = outside(data["around"], lake_xys)
    near = outside(data["near"], lake_xys)
    lo, hi = heightmap.height_range(z for _, _, z in near)
    interval = heightmap.contour_interval(*data["body_range"])
    opacity = lay["heightmap"][1]
    shaded = None
    if ground and on["relief"] and around:
        shaded = (lo, hi)
        block = max(1, round(4 * k))
        relief = heightmap.relief_grid(around, half_w, half_h,
                                       max(heightmap.RELIEF_STEP, 4 * mpp * k))
        small = heightmap.fade(heightmap.relief_image(
            relief, (0.0, 0.0, 1.0, 0.0), w, h, mpp, block, lo, hi, PAPER),
            opacity, PAPER)
        if pen.bw:
            small = light_grey(small)
        img.paste(Image.fromarray(small).resize((w, h), Image.BILINEAR),
                  (0, 0))
    if ground and on["samples"]:
        size = max(1, math.ceil(heightmap.CELL_M / mpp))
        for x, y, z in near:
            colour = ImageColor.getrgb(heightmap.height_colour(z, lo, hi))
            if pen.bw:
                colour = tuple(light_grey(np.array([colour], np.uint8))[0])
            sx, sy = w / 2 + x / mpp, h / 2 - y / mpp
            pen.draw.rectangle((sx - size / 2, sy - size / 2, sx + size / 2,
                                sy + size / 2),
                               fill=tuple(heightmap.fade(colour, opacity,
                                                         PAPER)))
    edges = draw_grid(pen, frame, radius, w, h, mpp)
    lines_drawn = ground and on["lines"] and draw_contours(
        pen, around, frame, radius, w, h, mpp, interval)

    # The surveys level by level (traces.LAYERS), as the height map has it.
    drawn = [t for t in ts if "style" in traces.KINDS[t["kind"]]
             and len(t["points"]) >= traces.KINDS[t["kind"]]["least"]]
    kinds = set()
    rivers = [(run, t["width_m"]) for t in ts
              if t["kind"] == "waterway" and len(t["points"]) >= 2
              for run in traces.parts(t)]
    for level in traces.LEVELS:
        here = {kind for kind, (at, _) in lay.items()
                if at == level and kind not in hidden
                and on.get(traces.KINDS.get(kind, {}).get("switch"), True)}
        kinds |= {t["kind"] for t in ts if t["kind"] in here
                  and in_frame(t, frame, radius, half_w, half_h)}
        water = here & {"sea", "lake", "waterway"}
        if on["lake"]:
            for op in sorted({lay[kind][1] for kind in water}):
                same = {kind for kind in water if lay[kind][1] == op}
                draw_water(pen, [x for x in lake_xys if x[2] in same],
                           rivers if "waterway" in same else [], op,
                           frame, radius, w, h, mpp)
        if on["zone"]:
            draw_zones(pen, [(on_map(traces.ring(t["points"])), t, lay)
                             for t in drawn if t["kind"] in here
                             and traces.KINDS[t["kind"]]["group"] == "area"],
                       to_px, w, h)
        if on["rail"] and "railway" in here:
            for t in ts:
                if t["kind"] == "railway":
                    draw_rail(pen, t, frame, radius, w, h, mpp)
        if on["drawn"]:
            for t in drawn:
                if (t["kind"] in here
                        and traces.KINDS[t["kind"]]["group"] == "line"):
                    draw_line(pen, t, frame, radius, w, h, mpp)
        if on["building"] and "building" in here:
            draw_buildings(pen, data, lay["building"][1], on_map, to_px, w, h)
    placed, numbers = draw_waypoints(pen, data["waypoints"], m, on, frame,
                                     radius, w, h, mpp)
    return img, {"edges": edges, "interval": interval if lines_drawn else None,
                 "range": shaded, "kinds": kinds, "waypoints": placed,
                 "numbers": numbers}


def in_frame(t, frame, radius, half_w, half_h):
    """Does trace `t` show in the frame: a point of it in there, or (an
    area) the frame's middle inside it?"""
    xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                         for p in t["points"]) if xy]
    if any(abs(x) <= half_w and abs(y) <= half_h for x, y in xys):
        return True
    return (traces.KINDS[t["kind"]]["group"] == "area" and len(xys) > 2
            and traces.inside_ring(0.0, 0.0, xys))


def light_grey(rgb):
    """8 bit RGB (an array, ... x 3) as the light grey of its brightness
    that black and white prints the relief in: from paper down to half."""
    lum = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    grey = np.rint(255 - (255 - lum) * 0.5).astype(np.uint8)
    return np.stack([grey] * 3, axis=-1)


def outside(cells, lake_xys):
    """The cells, (east m, north m, height m), outside every lake."""
    if not cells or not lake_xys:
        return cells
    xs = np.array([c[0] for c in cells])
    ys = np.array([c[1] for c in cells])
    wet = np.zeros(len(cells), bool)
    for runs, *_ in lake_xys:
        for xys in runs:
            wet |= traces.inside(xs, ys, xys)
    return [c for c, dry in zip(cells, (~wet).tolist()) if dry]


def draw_grid(pen, frame, radius, w, h, mpp):
    """The LAT/LON lines, thin and grey; returns where each meets the
    frame's edges, (side, px along that side, figure), for the band."""
    half_w, half_h = w / 2 * mpp, h / 2 * mpp
    step, lines = heightmap.map_grid(frame, radius, half_w, half_h,
                                     120 * mpp * pen.k)
    places = max(0, math.ceil(-math.log10(step) - 1e-9))
    width = max(1, round(pen.mm(0.12)))
    edges = []
    for kind, deg, runs in lines:
        text = f"{deg:+.{places}f}"
        for run in runs:
            px = [(w / 2 + x / mpp, h / 2 - y / mpp) for x, y in run]
            pen.draw.line(px, fill=pen.pal["grid"], width=width)
            for (x0, y0), (x1, y1) in zip(px, px[1:]):
                if kind == "lat":
                    for side, edge in (("left", 0.0), ("right", w)):
                        if (x0 - edge) * (x1 - edge) <= 0 and x0 != x1:
                            y = y0 + (edge - x0) / (x1 - x0) * (y1 - y0)
                            if 0 <= y <= h:
                                edges.append((side, y, text))
                else:
                    for side, edge in (("top", 0.0), ("bottom", h)):
                        if (y0 - edge) * (y1 - edge) <= 0 and y0 != y1:
                            x = x0 + (edge - y0) / (y1 - y0) * (x1 - x0)
                            if 0 <= x <= w:
                                edges.append((side, x, text))
    return edges


def draw_contours(pen, around, frame, radius, w, h, mpp, interval):
    """The height lines, every INDEX_EVERY-th thicker and labelled with its
    height on a patch of paper that breaks it. True if any was drawn."""
    half_w, half_h = w / 2 * mpp, h / 2 * mpp
    k = pen.k
    lines = heightmap.contours_on_body(
        around, frame, radius, half_w, half_h,
        max(heightmap.CELL_M, 5 * mpp * k), interval)
    labels, drew = [], False
    for level, line in lines:
        index = heightmap.is_index(level, interval)
        xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                             for p in line) if xy]
        for run in heightmap.in_view(xys, half_w, half_h):
            drew = True
            pen.draw.line([(w / 2 + x / mpp, h / 2 - y / mpp) for x, y in run],
                          fill=pen.pal["index" if index else "contour"],
                          width=max(1, round((1.4 if index else 0.7) * k)),
                          joint="curve")
            xy = heightmap.half_way(run, 80 * mpp * k) if index else None
            if xy:
                labels.append((w / 2 + xy[0] / mpp, h / 2 - xy[1] / mpp,
                               f"{level:g}"))
    for x, y, text in labels:
        pen.text((x, y), text, 6, pen.pal["index"], patch=PAPER)
    return drew


def draw_water(pen, lake_xys, rivers, opacity, frame, radius, w, h, mpp):
    """Lakes, seas and waterways of one opacity as one picture, so a river
    running into a lake joins it: the water at `opacity` %, the shore a
    solid line round it, each lake's level in it (as the height map)."""
    k = pen.k
    mask = Image.new("L", (w, h), 0)
    paint = ImageDraw.Draw(mask)
    half_w, half_h = w / 2 * mpp, h / 2 * mpp
    for points, width_m in rivers:
        xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                             for p in points) if xy]
        for run in heightmap.in_view(xys, half_w, half_h):
            paint.line([(w / 2 + x / mpp, h / 2 - y / mpp) for x, y in run],
                       fill=255, width=max(round(2 * k), round(width_m / mpp)),
                       joint="curve")
    labels = []
    for runs, level, _ in lake_xys:
        pxs = [[(w / 2 + x / mpp, h / 2 - y / mpp) for x, y in xys]
               for xys in runs]
        for px in pxs:
            for tri in traces.fan(px):
                paint.polygon(tri, fill=255)
        if level is not None:
            xs = [p[0] for px in pxs for p in px]
            ys = [p[1] for px in pxs for p in px]
            labels.append((sum(xs) / len(xs), sum(ys) / len(ys), level))
    wet = np.array(mask) > 0
    if not wet.any():
        return
    inner = wet
    for _ in range(max(1, round(2 * k))):
        p = np.pad(inner, 1, mode="edge")
        inner = (p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1]
                 & p[1:-1, :-2] & p[1:-1, 2:])
    rgba = np.zeros((h, w, 4), np.uint8)
    rgba[wet] = (*pen.pal["water"], traces.alpha(opacity))
    rgba[wet & ~inner] = (*pen.pal["shore"], 255)
    pen.img.alpha_composite(Image.fromarray(rgba, "RGBA"))
    pen.draw = ImageDraw.Draw(pen.img)
    if pen.bw:
        pen.hatch(inner, "across", 150)
    for x, y, level in labels:
        if 0 <= x <= w and 0 <= y <= h:
            pen.text((x, y), f"{level:,.0f}", 7, pen.pal["level"],
                     patch=PAPER if pen.bw else None)


def draw_zones(pen, zones, to_px, w, h):
    """Areas drawn by pattern, (map points or None, trace, layers): the
    washes first, then each border in its style with the inside on the
    left, a swamp's tufts, the name in the middle."""
    shown = []
    for xys, t, lay in zones:
        if not xys:
            continue
        px = to_px(xys)
        xs, ys = [p[0] for p in px], [p[1] for p in px]
        if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
            continue
        shown.append((px, traces.KINDS[t["kind"]], t.get("label"),
                      lay[t["kind"]][1], t["kind"]))

    def washes(d):
        for px, kind, _, opacity, _ in shown:
            if kind["fill"]:
                d.polygon(px, fill=ImageColor.getrgb(kind["fill"])
                          + (traces.alpha(opacity),))

    if pen.bw:
        for px, _, _, opacity, name in shown:
            style = HATCHES.get(name)
            if style:
                mask = Image.new("L", (w, h), 0)
                ImageDraw.Draw(mask).polygon(px, fill=255)
                pen.hatch(np.array(mask) > 0, style,
                          round(255 - opacity / 100 * 195))
    elif any(kind["fill"] for _, kind, _, _, _ in shown):
        pen.wash(washes)
    k = pen.k
    for px, kind, label, _, _ in shown:
        if kind["symbol"] == "tufts":
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).polygon(px, fill=255)
            inside = np.array(mask) > 0
            step = traces.TUFT_EVERY_PX * k
            for row, y in enumerate(np.arange(step / 2, h, step)):
                for x in np.arange(step / 2 + (row % 2) * step / 2, w, step):
                    if inside[int(y), int(x)]:
                        for x0, y0, x1, y1 in traces.tuft(x / k, y / k):
                            pen.draw.line((x0 * k, y0 * k, x1 * k, y1 * k),
                                          fill=kind["colour"],
                                          width=max(1, round(k)))
        border = traces.inward(px)
        pen.stroke(border + border[:1], kind)
        if label:
            pen.text((sum(p[0] for p in px) / len(px),
                      sum(p[1] for p in px) / len(px)), label, 7,
                     kind["colour"])


def draw_rail(pen, t, frame, radius, w, h, mpp):
    """A railway as the multiple track railroad symbol: a wide black line
    with a thin paper one down its middle, and ticks across."""
    k = pen.k
    half_w, half_h = w / 2 * mpp, h / 2 * mpp
    xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                         for p in t["points"]) if xy]
    for run in heightmap.in_view(xys, half_w, half_h):
        px = [(w / 2 + x / mpp, h / 2 - y / mpp) for x, y in run]
        pen.draw.line(px, fill=RAIL, width=round(6 * k), joint="curve")
        pen.draw.line(px, fill=RAIL_GAP, width=max(1, round(2 * k)),
                      joint="curve")
        for x0, y0, x1, y1 in traces.ticks(px, traces.TICK_EVERY_PX * k,
                                           traces.TICK_HALF_PX * k):
            pen.draw.line((x0, y0, x1, y1), fill=RAIL,
                          width=max(1, round(2 * k)))


def draw_line(pen, t, frame, radius, w, h, mpp):
    """A road, border or other line drawn by pattern, its name by its
    middle."""
    kind = traces.KINDS[t["kind"]]
    half_w, half_h = w / 2 * mpp, h / 2 * mpp
    xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                         for p in t["points"]) if xy]
    for run in heightmap.in_view(xys, half_w, half_h):
        pen.stroke([(w / 2 + x / mpp, h / 2 - y / mpp) for x, y in run], kind)
    mid = heightmap.half_way(xys, 0) if t.get("label") and xys else None
    if mid is not None:
        pen.text((w / 2 + mid[0] / mpp + 8 * pen.k,
                  h / 2 - mid[1] / mpp - 8 * pen.k), t["label"], 7,
                 kind["colour"], anchor="ls")


def draw_buildings(pen, data, opacity, on_map, to_px, w, h):
    """Buildings, squared off or as walked (traces.outline): the fill at
    `opacity` %, a dark outline, the name on it where it fits inside."""
    shown = []
    for t in data["traces"]:
        if (t["kind"] != "building"
                or len(t["points"]) < traces.KINDS["building"]["least"]):
            continue
        xys = on_map(t["points"])
        if not xys:
            continue
        px = to_px(traces.outline(xys, t.get("offset_m", data["offset_m"]),
                                  t.get("shape", "squared")))
        xs, ys = [p[0] for p in px], [p[1] for p in px]
        if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
            continue
        shown.append((px, t.get("label")))
    if not shown:
        return
    fill = ImageColor.getrgb(pen.pal["building"]) + (traces.alpha(opacity),)
    pen.wash(lambda d: [d.polygon(px, fill=fill) for px, _ in shown])
    f = pen.font(6)
    for px, label in shown:
        pen.draw.polygon(px, outline=pen.pal["edge"],
                         width=max(1, round(pen.k)))
        if not label:
            continue
        xy = (sum(p[0] for p in px) / len(px), sum(p[1] for p in px) / len(px))
        x0, y0, x1, y1 = pen.draw.textbbox(xy, label, font=f, anchor="mm")
        if all(traces.inside_ring(x, y, px)
               for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))):
            pen.draw.text(xy, label, font=f, fill=pen.pal["name"],
                          anchor="mm")


def draw_waypoints(pen, wps, m, on, frame, radius, w, h, mpp):
    """The waypoints of the types shown: each its SUB's icon (or a plain
    dot), its name beside it with names on, else its number (the map's,
    new ones given as maps.numbered). Returns ([(id, waypoint)] drawn, top
    left to bottom right, and the numbers)."""
    k = pen.k
    shown = [x for x in wps if on.get(x.get("type", "poi"), on["poi"])]
    by_id = {x["id"]: x for x in shown}
    placed = []
    for wid, p in starmap.body_points(shown, m["body"]):
        xy = heightmap.to_map(p, frame, radius)
        if not xy:
            continue
        sx, sy = w / 2 + xy[0] / mpp, h / 2 - xy[1] / mpp
        if 0 <= sx <= w and 0 <= sy <= h:
            placed.append((wid, sx, sy))
    numbers = dict(m.get("numbers") or {})
    if not on["names"]:
        numbers = maps.numbered(numbers, placed)
    f = pen.font(7)
    for wid, sx, sy in placed:
        sub = by_id[wid].get("sub", "")
        if subtypes.icon_of(sub):
            size = 8 * k
            draw_icon(pen.draw, sub, sx, sy, size, k, pen.pal)
            gap = size + 3 * k
        else:
            r = 4 * k
            pen.draw.ellipse((sx - r, sy - r, sx + r, sy + r),
                             fill=pen.pal["dot"], outline=pen.pal["dot_edge"],
                             width=max(1, round(k)))
            gap = 7 * k
        if on["names"]:
            pen.draw.text((sx + gap, sy), by_id[wid]["name"], font=f,
                          fill=INK, anchor="lm")
        else:
            pen.text((sx + gap, sy), str(numbers[wid]), 7, anchor="lm",
                     bold=True, patch=PAPER)
    placed.sort(key=lambda p: (p[2], p[1]))
    return [(wid, by_id[wid]) for wid, _, _ in placed], numbers


def draw_icon(d, sub, x, y, size, k, pal=None):
    """A waypoint's SUB icon as the height map draws it: a dark badge
    `size` px in radius, the glyph in the pin's colour inside (`pal`'s,
    PALETTES; colour if not given)."""
    pal = pal or PALETTES["colour"]
    ring = pal["pin"] if pal is PALETTES["colour"] else pal["badge"]
    d.ellipse((x - size, y - size, x + size, y + size), fill=pal["badge"],
              outline=ring, width=max(1, round(1.5 * k)))
    width = max(1, round(size * 0.14))
    for kind, paint, xy in subtypes.icon_items(sub, x, y, size * 0.68):
        colour = pal["pin"] if paint == "fg" else pal["badge"]
        pts = list(zip(xy[::2], xy[1::2]))
        if kind == "poly":
            d.polygon(pts, fill=colour)
        elif kind == "rect":
            d.rectangle(xy, fill=colour)
        elif kind == "oval":
            d.ellipse(xy, fill=colour)
        elif kind == "ring":
            d.ellipse(xy, outline=colour, width=width)
        else:
            d.line(pts, fill=colour, width=width, joint="curve")


# --- the legend -------------------------------------------------------

LEGEND_ROW_MM = 4.4     # a line of the legend
LEGEND_PAD_MM = 2.0     # room inside its box
SYMBOL_MM = 7.0         # the symbol at the start of a line
COLUMN_MM = 45.0        # a column of lines at least this wide


def legend_lines(m, data, info):
    """What the legend says, a line for each thing on the map, as (symbol,
    text): the waypoints, the survey kinds, then the heights. A symbol is
    a tuple naming what to draw (see draw_symbol)."""
    lines = []
    on = maps.view_switches(m)
    if on["names"]:
        subs = {}
        for _, x in info["waypoints"]:
            sub = x.get("sub", "") if subtypes.icon_of(x.get("sub")) else ""
            subs.setdefault(sub, None)
        for sub in sorted(subs, key=lambda s: (s == "", s.lower())):
            lines.append((("icon", sub), sub or "Waypoint"))
    else:
        numbers = info["numbers"]
        for wid, x in sorted(info["waypoints"],
                             key=lambda p: numbers.get(p[0], 0)):
            sub = x.get("sub", "") if subtypes.icon_of(x.get("sub")) else ""
            lines.append((("icon", sub), f"{numbers[wid]}  {x['name']}"))
    lay = traces.layers(m.get("layers"))
    for kind in traces.LAYERS:
        if kind in info["kinds"]:
            lines.append((("kind", kind, lay[kind][1]),
                          traces.KINDS[kind]["menu"].capitalize()))
    if info["range"]:
        lo, hi = info["range"]
        lines.append((("relief",), f"Height {lo:,.0f} to {hi:,.0f} m"))
    if info["interval"]:
        lines.append((("contour",),
                      f"Height lines every {info['interval']:g} m"))
    return lines


def draw_legend(pen, box, lines):
    """The legend's box, (left, top, right, bottom) px, on paper with a thin
    border: "Legend", then `lines` down columns at least COLUMN_MM wide.
    Returns the lines that did not fit. No lines: no box."""
    if not lines:
        return []
    x0, y0, x1, y1 = box
    pad, row = pen.mm(LEGEND_PAD_MM), pen.mm(LEGEND_ROW_MM)
    pen.draw.rectangle(box, fill=PAPER, outline=INK,
                       width=max(1, round(pen.mm(0.2))))
    pen.text((x0 + pad, y0 + pad + row / 2), "Legend", 7, anchor="lm",
             bold=True)
    top = y0 + pad + row
    columns = max(1, int((x1 - x0 - 2 * pad) // pen.mm(COLUMN_MM)))
    width = (x1 - x0 - 2 * pad) / columns
    rows = max(0, int((y1 - top - pad) // row))
    fit = lines[:rows * columns]
    for i, line in enumerate(fit):
        col, r = divmod(i, rows)
        cx = x0 + pad + col * width
        draw_line_of_legend(pen, (cx, top + r * row, cx + width - pad,
                                  top + (r + 1) * row), line)
    return lines[len(fit):]


def draw_line_of_legend(pen, box, line):
    """One line of the legend in `box`: its symbol, then its text, cut to
    fit."""
    x0, y0, x1, y1 = box
    symbol, text = line
    sw = pen.mm(SYMBOL_MM)
    draw_symbol(pen, (x0, y0 + pen.mm(0.6), x0 + sw, y1 - pen.mm(0.6)),
                symbol)
    f = pen.font(6.5)
    room = x1 - (x0 + sw + pen.mm(1.5))
    if pen.draw.textlength(text, font=f) > room:
        while text and pen.draw.textlength(text + "...", font=f) > room:
            text = text[:-1]
        text += "..."
    pen.draw.text((x0 + sw + pen.mm(1.5), (y0 + y1) / 2), text, font=f,
                  fill=INK, anchor="lm")


def draw_symbol(pen, box, symbol):
    """A legend line's symbol in `box`, drawn as on the map: a waypoint's
    icon, ("icon", sub); a survey kind, ("kind", kind, opacity %); the
    relief's colours, ("relief",); a height line, ("contour",)."""
    x0, y0, x1, y1 = box
    k, d = pen.k, pen.draw
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    run = [(x0 + k, my), (x1 - k, my)]
    if symbol[0] == "icon":
        size = min(8 * k, (y1 - y0) / 2)
        if symbol[1]:
            draw_icon(d, symbol[1], mx, my, size, k, pen.pal)
        else:
            r = size / 2
            d.ellipse((mx - r, my - r, mx + r, my + r), fill=pen.pal["dot"],
                      outline=pen.pal["dot_edge"], width=max(1, round(k)))
    elif symbol[0] == "relief":
        steps = max(2, round(x1 - x0))
        for i in range(steps):
            colour = ImageColor.getrgb(heightmap.height_colour(
                i, 0, steps - 1))
            if pen.bw:
                colour = tuple(light_grey(np.array([colour], np.uint8))[0])
            d.line((x0 + i, y0, x0 + i, y1), fill=colour)
    elif symbol[0] == "contour":
        d.line(run, fill=pen.pal["index"], width=max(1, round(1.4 * k)))
    else:
        _, kind, opacity = symbol
        spec = traces.KINDS[kind]
        if kind == "railway":
            d.line(run, fill=RAIL, width=round(6 * k))
            d.line(run, fill=RAIL_GAP, width=max(1, round(2 * k)))
            for tx in (x0 + (x1 - x0) / 3, x0 + 2 * (x1 - x0) / 3):
                d.line((tx, my - 6 * k, tx, my + 6 * k), fill=RAIL,
                       width=max(1, round(2 * k)))
        elif kind == "waterway":
            d.line(run, fill=pen.pal["water"], width=max(2, round(4 * k)))
            d.line(run, fill=pen.pal["shore"], width=1)
        elif kind in traces.WATER:
            d.rectangle(box, fill=_over_paper(pen.pal["water"], opacity),
                        outline=pen.pal["shore"], width=max(1, round(k)))
            if pen.bw:
                _hatch_box(pen, box, "across", 150)
        elif kind == "building":
            d.rectangle(box, fill=_over_paper(
                ImageColor.getrgb(pen.pal["building"]), opacity),
                outline=pen.pal["edge"], width=max(1, round(k)))
        elif spec["group"] == "area":
            style = HATCHES.get(kind)
            if pen.bw and style:
                _hatch_box(pen, box, style, round(255 - opacity / 100 * 195))
            elif spec["fill"] and not pen.bw:
                d.rectangle(box, fill=_over_paper(
                    ImageColor.getrgb(spec["fill"]), opacity))
            ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            border = traces.inward(ring)
            pen.stroke(border + border[:1], spec)
        else:
            pen.stroke(run, spec)


def _hatch_box(pen, box, style, grey):
    """Hatch the inside of a legend symbol's `box`, as pen.hatch."""
    x0, y0, x1, y1 = (round(v) for v in box)
    inside = np.zeros((pen.img.height, pen.img.width), bool)
    inside[max(0, y0 + 1):max(0, y1), max(0, x0 + 1):max(0, x1)] = True
    pen.hatch(inside, style, grey)


def _over_paper(rgb, opacity):
    """`rgb` at `opacity` % over white paper."""
    return tuple(round(255 + (c - 255) * opacity / 100) for c in rgb)


def draw_compass(pen, box, plate=False):
    """The north arrow in `box` (left, top, right, bottom) px: an arrowhead,
    its left half black and its right half paper, with N over it; on a
    plate of paper with a thin border if `plate`. North is straight up:
    every map is drawn north up."""
    x0, y0, x1, y1 = box
    s = x1 - x0
    d = pen.draw
    if plate:
        d.rectangle(box, fill=PAPER, outline=INK,
                    width=max(1, round(pen.mm(0.2))))
    cx, cy = (x0 + x1) / 2, y0 + 0.6 * s
    top, base, half, notch = cy - 0.22 * s, cy + 0.36 * s, 0.15 * s, \
        cy + 0.2 * s
    line = max(1, round(s * 0.02))
    d.polygon([(cx, top), (cx - half, base), (cx, notch)], fill=INK)
    d.polygon([(cx, top), (cx + half, base), (cx, notch)], fill=PAPER)
    d.line([(cx, top), (cx - half, base), (cx, notch), (cx + half, base),
            (cx, top)], fill=INK, width=line, joint="curve")
    pen.text((cx, top - 0.05 * s), "N", 0.3 * s * 72 / pen.dpi, anchor="ms",
             bold=True)
