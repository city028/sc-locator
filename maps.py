"""Maps: a part of a body's height map kept to be printed, on A4 portrait.

No tkinter here, so all of it can be tested without a window. The height
map (main_window.py) opens, frames and draws them; sc_locator.py keeps them
in maps.json.

A map is a view, not a picture: where it is and how it is drawn, so it is
drawn from the surveys and waypoints as they are now each time, and what is
added later shows on it without anything being done:

    {"id": "...", "name": "...", "body": "microtech",
     "centre": [x, y, z],   unit vector on the body, the frame's middle
     "width_m": 2400.0,     ground across the frame, west to east
     "switches": {...},     what it shows that is not a survey: SWITCHES
     "layers": {...},       its levels and opacities, as heightmap_layers
     "hidden": [...],       the kinds (traces.LAYERS) it leaves out
     "legend": {...},       where its legend box is and how big: LEGEND
     "compass": {...},      its north arrow: COMPASS
     "ink": "colour",       printed in colour or "bw": black and white
     "numbers": {id: n},    each waypoint's number, kept once given
     "notes": "...",        what the map is for, printed on its second page
     "created": time, "updated": time}

North is up: the frame is the flat map around its centre (heightmap.
local_frame), so it is as tall as FRAME_MM makes it for its width.
"""
import math
import time
import uuid

import heightmap
import traces

# The printed page, mm: A4 portrait. Down it: the margin, the title, the
# band round the frame for its LAT/LON figures, the frame, the band again,
# the foot (scale bar, scale, contour interval) and the margin. Across: the
# margin, the band, the frame, the band, the margin. Changed, a map keeps
# its centre and width and only shows more or less ground north to south.
PAGE_MM = (210.0, 297.0)
MARGIN_MM = 10.0
TITLE_MM = 12.0
BAND_MM = 8.0
FOOT_MM = 10.0
FRAME_MM = (PAGE_MM[0] - 2 * (MARGIN_MM + BAND_MM),
            PAGE_MM[1] - 2 * (MARGIN_MM + BAND_MM) - TITLE_MM - FOOT_MM)
EDGE_POINTS = 8         # points along each side of a frame drawn on the map

# What a map shows besides its surveys (those are shown or hidden a kind at
# a time: "hidden"), as height map switches, and how a new one starts.
SWITCHES = {"relief": True, "lines": True, "samples": False, "poi": True,
            "survey": False, "names": True}
# The height map switches for a group of survey kinds (traces.KINDS'
# "switch"): always on for a map, which hides kinds one by one instead.
GROUPS = sorted({k["switch"] for k in traces.KINDS.values()})
# And never on a map: spot heights, the survey pattern or other maps'
# frames.
NEVER = ("poi_height", "course", "frames")


# The legend: a box in a corner of the frame, `w_mm` x `h_mm`; what does
# not fit in it goes on page 2. The corners as Settings names them.
LEGEND = {"corner": "SE", "w_mm": 70.0, "h_mm": 60.0}
CORNERS = {"top left": "NW", "top right": "NE", "bottom left": "SW",
           "bottom right": "SE"}
LEGEND_MIN_MM = (20.0, 15.0)


def legend_of(m):
    """Map `m`'s legend as LEGEND, filled in where not set and kept inside
    the frame."""
    out = dict(LEGEND)
    out.update(m.get("legend") or {})
    if out["corner"] not in CORNERS.values():
        out["corner"] = LEGEND["corner"]
    out["w_mm"] = min(FRAME_MM[0], max(LEGEND_MIN_MM[0], float(out["w_mm"])))
    out["h_mm"] = min(FRAME_MM[1], max(LEGEND_MIN_MM[1], float(out["h_mm"])))
    return out


def legend_box(m):
    """Map `m`'s legend box in its frame, (left, top, right, bottom) mm from
    the frame's top left corner."""
    lg = legend_of(m)
    w, h = lg["w_mm"], lg["h_mm"]
    left = 0.0 if lg["corner"][1] == "W" else FRAME_MM[0] - w
    top = 0.0 if lg["corner"][0] == "N" else FRAME_MM[1] - h
    return left, top, left + w, top + h


# The north arrow: shown or not, its corner, `size_mm` square, on a plate of
# paper with a thin border or straight on the map. COMPASS_INSET_MM in from
# the frame's edges; in the legend's corner, just past the legend's box.
COMPASS = {"on": True, "corner": "NE", "size_mm": 20.0, "plate": False}
COMPASS_MM = (10.0, 50.0)
COMPASS_INSET_MM = 3.0


# What a map is printed in, as the config keeps it and as the Maps tab
# names it.
INKS = {"colour": "Colour", "bw": "Black and white"}


def ink_of(m):
    """"colour" or "bw" (black and white) for map `m`: colour if not set."""
    return m.get("ink") if m.get("ink") in INKS else "colour"


def compass_of(m):
    """Map `m`'s north arrow as COMPASS, filled in where not set, its size
    kept to COMPASS_MM."""
    out = dict(COMPASS)
    out.update(m.get("compass") or {})
    if out["corner"] not in CORNERS.values():
        out["corner"] = COMPASS["corner"]
    out["size_mm"] = min(COMPASS_MM[1], max(COMPASS_MM[0],
                                            float(out["size_mm"])))
    return out


def compass_box(m):
    """Map `m`'s north arrow in its frame, (left, top, right, bottom) mm
    from the frame's top left corner: COMPASS_INSET_MM in from its corner,
    or past the legend's box if the legend is in that corner too (kept
    inside the frame)."""
    c = compass_of(m)
    size, inset = c["size_mm"], COMPASS_INSET_MM
    left = inset if c["corner"][1] == "W" else FRAME_MM[0] - inset - size
    top = inset if c["corner"][0] == "N" else FRAME_MM[1] - inset - size
    if c["corner"] == legend_of(m)["corner"]:
        _, l_top, _, l_bottom = legend_box(m)
        top = (l_bottom + inset if c["corner"][0] == "N"
               else l_top - inset - size)
        top = min(FRAME_MM[1] - size, max(0.0, top))
    return left, top, left + size, top + size


def numbered(numbers, placed):
    """Waypoint numbers, {id: n}, for the waypoints `placed` on the map,
    [(id, x, y)] (y down): those already numbered keep theirs, and the
    others take the next free ones, top left to bottom right."""
    out = dict(numbers or {})
    new = sorted((y, x, wid) for wid, x, y in placed if wid not in out)
    n = max(out.values(), default=0)
    for _, _, wid in new:
        n += 1
        out[wid] = n
    return out


def frame_box():
    """The frame on the page, (left, top, right, bottom) mm."""
    left, top = MARGIN_MM + BAND_MM, MARGIN_MM + TITLE_MM + BAND_MM
    return left, top, left + FRAME_MM[0], top + FRAME_MM[1]


def scale(width_m):
    """The map's scale as printed, N in 1:N, rounded to 3 figures."""
    n = width_m / (FRAME_MM[0] / 1000)
    return int(round(n, -max(0, int(math.log10(n)) - 2))) if n >= 1 else 1


def height_m(width_m):
    """Ground down a frame `width_m` across."""
    return width_m * FRAME_MM[1] / FRAME_MM[0]


def fit(w, h, pad):
    """The largest frame in px, (across, down), that fits a w x h px window
    with `pad` px clear all round."""
    across = max(1.0, min(w - 2 * pad, (h - 2 * pad) * FRAME_MM[0]
                          / FRAME_MM[1]))
    return across, across * FRAME_MM[1] / FRAME_MM[0]


def map_name(maps):
    """First free 'map-NNN', offered for a new map."""
    names = {m["name"] for m in maps}
    n = 1
    while f"map-{n:03d}" in names:
        n += 1
    return f"map-{n:03d}"


def new_map(name, body, centre, width_m, switches, layers, now=None,
            hidden=()):
    """A map of `body` around `centre` (a planet-frame point), `width_m`
    across, drawn with `switches` and `layers` (copied), leaving out the
    kinds in `hidden`."""
    now = time.time() if now is None else now
    return {"id": uuid.uuid4().hex[:12], "name": name, "body": body,
            "centre": list(heightmap.local_frame(centre)[0]),
            "width_m": float(width_m), "switches": dict(switches),
            "layers": {k: dict(v) for k, v in (layers or {}).items()},
            "hidden": sorted(hidden),
            "notes": "", "created": now, "updated": now}


def view_switches(m):
    """Every height map switch as map `m` is drawn with: {name: on}."""
    got = m.get("switches") or {}
    out = {name: bool(got.get(name, on)) for name, on in SWITCHES.items()}
    out.update({name: True for name in GROUPS})
    out.update({name: False for name in NEVER})
    return out


def from_view(switches):
    """(switches, hidden) for a new map from the height map's switches as
    they are: what it shows besides surveys, and the survey kinds whose
    group is switched off."""
    return ({name: bool(switches.get(name, on))
             for name, on in SWITCHES.items()},
            sorted(kind for kind, k in traces.KINDS.items()
                   if not switches.get(k["switch"], True)))


def settled(m):
    """{field: value} to bring a map made before maps had a tab of their own
    up to date, or None if it is: it kept every height map switch, so a
    group switched off becomes its kinds hidden."""
    got = m.get("switches") or {}
    if set(got) <= set(SWITCHES):
        return None
    switches, off = from_view(got)
    return {"switches": switches,
            "hidden": sorted(set(m.get("hidden", ())) | set(off))}


def listed(maps, body=None, text=""):
    """The maps to list, [(body, [map, ...])], bodies A to Z and each one's
    maps by name: only `body`'s if given, and only those whose name, body
    or notes hold `text` (ignoring case) if any."""
    text = text.strip().lower()
    out = {}
    for m in maps:
        if body and m["body"] != body:
            continue
        if text and not any(text in (m.get(k) or "").lower()
                            for k in ("name", "body", "notes")):
            continue
        out.setdefault(m["body"], []).append(m)
    return [(b, sorted(ms, key=lambda m: m["name"].lower()))
            for b, ms in sorted(out.items())]


def of_body(maps, body):
    """`body`'s maps, by name A to Z, ignoring case."""
    return sorted((m for m in maps if m["body"] == body),
                  key=lambda m: m["name"].lower())


def outline(m, radius):
    """The frame's edge as planet-frame points round it, EDGE_POINTS a
    side, from the north-west corner clockwise."""
    frame = heightmap.local_frame(m["centre"])
    hw, hh = m["width_m"] / 2, height_m(m["width_m"]) / 2
    corners = ((-hw, hh), (hw, hh), (hw, -hh), (-hw, -hh))
    out = []
    for (x0, y0), (x1, y1) in zip(corners, corners[1:] + corners[:1]):
        for k in range(EDGE_POINTS):
            t = k / EDGE_POINTS
            out.append(heightmap.from_map(x0 + (x1 - x0) * t,
                                          y0 + (y1 - y0) * t, frame, radius))
    return out
