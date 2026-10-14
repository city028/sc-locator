"""Height map: ground heights you measured, on a grid wrapped round each body.

No tkinter here, so all of it can be tested without a window. The main window
(main_window.py) only draws what these functions return.

The grid is a cube-sphere: the six faces of a cube pushed out onto the body.
Unlike a lat/lon grid its cells do not squeeze together at the poles. Each
face is cut into n x n cells of equal angle, n chosen so a cell is about
CELL_M across (CELL_M at the middle of a face, down to about 0.7 of it at the
edges).

Only cells something was measured in are stored:

    {body: {"n": cells per face edge, "radius": m,
            "cells": {"face,i,j": [mean height m, samples]}}}

A height is ElevationUnderCam: the ground over the reference sphere, so the
ground under a cell is at radius + height from the centre. Positions are in the
planet frame, which turns with the body, so a cell stays on the same ground.
"""
import math

CELL_M = 100.0
GLOBE_CELLS = 192       # cells per face edge on the globe view: 8 km
                        # on a 1000 km body

# Each face: (normal, a axis, b axis). A point on a face is
# normal + a * a_axis + b * b_axis before it is pushed out onto the sphere.
FACES = (
    ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((-1, 0, 0), (0, -1, 0), (0, 0, 1)),
    ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),
    ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    ((0, 0, 1), (0, 1, 0), (-1, 0, 0)),
    ((0, 0, -1), (0, 1, 0), (1, 0, 0)),
)
QUARTER = math.pi / 2


def _dot(p, q):
    return p[0] * q[0] + p[1] * q[1] + p[2] * q[2]


def _unit(p):
    r = math.sqrt(_dot(p, p))
    return (p[0] / r, p[1] / r, p[2] / r)


def cells_per_face(radius, cell_m=CELL_M):
    """Cells along a face edge so one is about `cell_m` across."""
    return max(1, round(QUARTER * radius / cell_m))


def cell_of(pos, n):
    """(face, i, j) of the cell a planet-frame position is over."""
    k = max(range(3), key=lambda a: abs(pos[a]))
    face = 2 * k + (pos[k] < 0)
    normal, ax, bx = FACES[face]
    d = _dot(pos, normal)
    i = int((math.atan(_dot(pos, ax) / d) / QUARTER + 0.5) * n)
    j = int((math.atan(_dot(pos, bx) / d) / QUARTER + 0.5) * n)
    return face, min(max(i, 0), n - 1), min(max(j, 0), n - 1)


def grid_point(face, u, v, n):
    """Unit vector at grid coordinates (u, v) on a face; u, v in 0..n, so
    whole numbers are cell corners and half-way values are cell centres."""
    normal, ax, bx = FACES[face]
    a = math.tan((u / n - 0.5) * QUARTER)
    b = math.tan((v / n - 0.5) * QUARTER)
    return _unit(tuple(normal[c] + a * ax[c] + b * bx[c] for c in range(3)))


def cell_centre(face, i, j, n):
    return grid_point(face, i + 0.5, j + 0.5, n)


def key(cell):
    return "%d,%d,%d" % cell


def unkey(k):
    face, i, j = (int(x) for x in k.split(","))
    return face, i, j


def add_sample(store, body, radius, pos, elev):
    """Fold one ground height into its cell's mean. Returns the cell's key.

    A body's grid is fixed when its first sample arrives, so a later change to
    its radius cannot move the cells already measured.
    """
    rec = store.setdefault(body, {"n": cells_per_face(radius),
                                  "radius": radius, "cells": {}})
    k = key(cell_of(pos, rec["n"]))
    mean, count = rec["cells"].get(k, (0.0, 0))
    rec["cells"][k] = [round(mean + (elev - mean) / (count + 1), 2),
                       count + 1]
    return k


def cell_count(store, body):
    rec = store.get(body)
    return len(rec["cells"]) if rec else 0


def coarse(rec, m):
    """Average the measured cells into an m x m grid per face.

    {(face, I, J): mean height}. Each measured cell counts once however many
    samples it has, so a spot you stood still on does not outweigh the ground
    you flew over.
    """
    if not rec:
        return {}
    n = rec["n"]
    sums = {}
    for k, (mean, _) in rec["cells"].items():
        face, i, j = unkey(k)
        c = (face, i * m // n, j * m // n)
        s = sums.setdefault(c, [0.0, 0])
        s[0] += mean
        s[1] += 1
    return {c: s[0] / s[1] for c, s in sums.items()}


def globe_tiles(heights, radius, m=GLOBE_CELLS, exaggeration=1.0):
    """The measured tiles of the globe, m x m per face, ready to project and
    draw. The rest of the body is drawn as a plain ball under them.

    `heights` is coarse(rec, m). Returns [(corners, centre, height)], corners
    four planet-frame points. Neighbouring tiles rise together at the corners
    they share, so the mesh does not tear. Corners are shared across face
    edges too: they are matched by position, not by index.
    """
    corner_sum = {}

    def corner_key(p):
        return tuple(round(c * 1e6) for c in p)

    grid = []
    for (face, i, j), h in heights.items():
        corners = [grid_point(face, i + du, j + dv, m)
                   for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1))]
        grid.append((corners, cell_centre(face, i, j, m), h))
        for p in corners:
            s = corner_sum.setdefault(corner_key(p), [0.0, 0])
            s[0] += h
            s[1] += 1
    tiles = []
    for corners, centre, h in grid:
        raised = []
        for p in corners:
            s = corner_sum[corner_key(p)]
            r = radius + s[0] / s[1] * exaggeration
            raised.append((p[0] * r, p[1] * r, p[2] * r))
        r = radius + h * exaggeration
        tiles.append((raised, (centre[0] * r, centre[1] * r, centre[2] * r),
                      h))
    return tiles


def local_frame(centre):
    """(c, east, north) unit vectors for a flat map around a planet-frame
    point. At a pole, east is taken along +y."""
    c = _unit(centre)
    e = (-c[1], c[0], 0.0)
    if _dot(e, e) < 1e-18:
        e = (0.0, 1.0, 0.0)
    e = _unit(e)
    nth = (c[1] * e[2] - c[2] * e[1], c[2] * e[0] - c[0] * e[2],
           c[0] * e[1] - c[1] * e[0])
    return c, e, nth


def to_map(p, frame, radius):
    """Planet-frame point -> (east m, north m) on the flat map, or None when
    it is on the far half of the body. Gnomonic: true near the middle, which
    is all a map tens of km across ever shows."""
    c, e, nth = frame
    d = _dot(p, c)
    if d <= 0:
        return None
    return radius * _dot(p, e) / d, radius * _dot(p, nth) / d


def from_map(x, y, frame, radius):
    """(east m, north m) on the flat map -> unit vector on the body."""
    c, e, nth = frame
    return _unit(tuple(c[i] + (x * e[i] + y * nth[i]) / radius
                       for i in range(3)))


def local_cells(rec, radius, frame, half_w, half_h):
    """Measured cells inside a map half_w x half_h metres either side of the
    middle, as [(east m, north m, height m)]."""
    if not rec:
        return []
    n = rec["n"]
    out = []
    for k, (mean, _) in rec["cells"].items():
        xy = to_map(cell_centre(*unkey(k), n), frame, radius)
        if xy and abs(xy[0]) <= half_w and abs(xy[1]) <= half_h:
            out.append((xy[0], xy[1], mean))
    return out


def centre_of(rec):
    """Unit vector at the middle of the measured cells, or None."""
    if not rec or not rec["cells"]:
        return None
    n = rec["n"]
    s = [0.0, 0.0, 0.0]
    for k in rec["cells"]:
        p = cell_centre(*unkey(k), n)
        for i in range(3):
            s[i] += p[i]
    if _dot(s, s) < 1e-18:
        return None
    return _unit(s)


# Low to high: deep blue, green, yellow, brown, white.
RAMP = ("#1F4E8C", "#3F9F5A", "#D9C65A", "#8C5A2E", "#F2F2F2")
GREY = "#5A626B"


def _mix(a, b, t):
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return [u + (v - u) * t for u, v in zip(ca, cb)]


def _hex(rgb, light=1.0):
    return "#" + "".join("%02x" % max(0, min(255, round(c * light)))
                         for c in rgb)


def height_colour(h, lo, hi, light=1.0):
    """Colour for height h on a lo..hi scale, darkened by `light` (0..1).
    Grey for None: ground nobody measured."""
    if h is None:
        return _hex([int(GREY[i:i + 2], 16) for i in (1, 3, 5)], light)
    t = 0.5 if hi <= lo else min(1.0, max(0.0, (h - lo) / (hi - lo)))
    t *= len(RAMP) - 1
    i = min(int(t), len(RAMP) - 2)
    return _hex(_mix(RAMP[i], RAMP[i + 1], t - i), light)


LIGHT = _unit((-0.4, -1.0, 0.5))   # view frame: from the viewer, upper left


def tile_light(normal):
    """Brightness 0.45..1 of a tile facing `normal` (view frame, any length),
    so the mesh reads as a ball and not a flat disc."""
    return 0.45 + 0.55 * max(0.0, _dot(_unit(normal), LIGHT))


def height_range(heights):
    """(lo, hi) of the heights given, (0, 0) if there are none."""
    hs = [h for h in heights if h is not None]
    return (min(hs), max(hs)) if hs else (0.0, 0.0)
