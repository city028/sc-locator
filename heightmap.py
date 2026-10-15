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

import numpy as np

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


GRID_STEPS = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0,
              5.0, 10.0, 15.0, 30.0)     # degrees between grid lines


def latlon(u):
    """(lat, lon) in degrees of a planet-frame direction, as to_latlon."""
    u = _unit(u)
    return (math.degrees(math.asin(max(-1.0, min(1.0, u[2])))),
            math.degrees(math.atan2(u[1], u[0])))


def map_grid(frame, radius, half_w, half_h, min_gap_m):
    """LAT and LON lines across a map half_w x half_h metres either side of
    the middle, at the finest step in GRID_STEPS at least `min_gap_m` apart.

    Returns (step, lines), each line ("lat" or "lon", degrees, runs), a run a
    list of (east m, north m). Meridians come out straight on this map and
    parallels very nearly so; both are sampled, which also covers a pole.
    """
    step = next((s for s in GRID_STEPS
                 if radius * math.radians(s) >= min_gap_m), GRID_STEPS[-1])
    c_lat, c_lon = latlon(frame[0])
    lats, dlons = [], []
    for i in range(9):
        for j in range(9):
            la, lo = latlon(from_map(half_w * (i / 4 - 1), half_h * (j / 4 - 1),
                                     frame, radius))
            lats.append(la)
            dlons.append((lo - c_lon + 180.0) % 360.0 - 180.0)
    lat0, lat1, d0, d1 = min(lats), max(lats), min(dlons), max(dlons)
    for pole in (1, -1):
        xy = to_map((0.0, 0.0, pole), frame, radius)
        if xy and abs(xy[0]) <= half_w and abs(xy[1]) <= half_h:
            if pole > 0:
                lat1 = 90.0
            else:
                lat0 = -90.0
            d0, d1 = -180.0, 180.0
    lat0, lat1 = max(-90.0, lat0 - step), min(90.0, lat1 + step)
    d0, d1 = d0 - step, d1 + step

    def runs(points):
        out, run = [], []
        for la, lo in points:
            a, o = math.radians(la), math.radians(lo)
            xy = to_map((math.cos(a) * math.cos(o), math.cos(a) * math.sin(o),
                         math.sin(a)), frame, radius)
            if xy and abs(xy[0]) <= 3 * half_w and abs(xy[1]) <= 3 * half_h:
                run.append(xy)
            elif run:
                out.append(run)
                run = []
        if run:
            out.append(run)
        return [r for r in out if len(r) > 1]

    n = 64
    lines = []
    for k in range(math.ceil(lat0 / step), math.floor(lat1 / step) + 1):
        la = k * step
        if abs(la) < 90.0:
            lines.append(("lat", la, runs(
                (la, c_lon + d0 + (d1 - d0) * i / n) for i in range(n + 1))))
    for k in range(math.ceil(d0 / step), math.floor(d1 / step) + 1):
        lo = c_lon - c_lon % step + k * step     # whole steps of true longitude
        lines.append(("lon", (lo + 180.0) % 360.0 - 180.0, runs(
            (lat0 + (lat1 - lat0) * i / n, lo) for i in range(n + 1))))
    return step, [ln for ln in lines if ln[2]]


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


REACH_M = 1500.0        # contours go no further than this from a measured cell
INDEX_EVERY = 5         # every 5th contour is thicker and labelled


def contour_interval(lo, hi, lines=20):
    """Metres between contours: a 1, 2 or 5 step giving about `lines` of them
    over lo..hi, and never under 1 m. Taken from the whole body, so it stays
    put while you pan."""
    want = max((hi - lo) / lines, 1.0)
    step = 10 ** math.floor(math.log10(want))
    return next(k * step for k in (1, 2, 5, 10) if k * step >= want)


def height_grid(cells, half_w, half_h, step, reach=REACH_M, least=3):
    """Heights on a grid of points `step` m apart over the map, guessed from
    the measured cells near each one, nearer cells counting for more.

    `cells` as local_cells gives them. A point with fewer than `least` cells
    within `reach` gets NaN: a lone cell would ring itself with contours.
    Returns (x0, y0, rows), rows a numpy array, rows[j, i] the height at
    (x0 + i * step, y0 + j * step). The grid only spans the cells' own patch
    of the map, plus `reach`, which on a map mostly empty is far less than
    all of it.
    """
    nx, ny = math.ceil(half_w / step), math.ceil(half_h / step)
    if not cells:
        return 0.0, 0.0, np.empty((0, 0))
    pts = np.asarray(cells, dtype=float)
    ia = max(-nx, math.floor((pts[:, 0].min() - reach) / step))
    ib = min(nx, math.ceil((pts[:, 0].max() + reach) / step))
    ja = max(-ny, math.floor((pts[:, 1].min() - reach) / step))
    jb = min(ny, math.ceil((pts[:, 1].max() + reach) / step))
    if ia > ib or ja > jb:
        return 0.0, 0.0, np.empty((0, 0))
    x0, y0 = ia * step, ja * step
    cols, nrows = ib - ia + 1, jb - ja + 1
    sw = np.zeros(cols * nrows)
    sz, found = np.zeros_like(sw), np.zeros_like(sw)
    # Each cell adds itself to the points of a window round it, which skips
    # the many points nowhere near a cell. Cells go in batches, so a big
    # survey does not need one huge array.
    win = np.arange(int(2 * reach // step) + 2)
    for s in range(0, len(pts), 2000):
        cx, cy, cz = (pts[s:s + 2000, k, None, None] for k in range(3))
        i = np.ceil((cx - reach - x0) / step) + win[None, None, :]
        j = np.ceil((cy - reach - y0) / step) + win[None, :, None]
        d2 = (x0 + i * step - cx) ** 2 + (y0 + j * step - cy) ** 2
        ok = ((d2 <= reach * reach) & (i >= 0) & (i < cols)
              & (j >= 0) & (j < nrows))
        k = (j * cols + i)[ok].astype(np.intp)
        w = (1.0 / (d2 + CELL_M * CELL_M))[ok]
        sw += np.bincount(k, w, sw.size)
        sz += np.bincount(k, w * np.broadcast_to(cz, ok.shape)[ok], sw.size)
        found += np.bincount(k, None, sw.size)
    with np.errstate(invalid="ignore", divide="ignore"):
        rows = np.where(found >= least, sz / sw, np.nan)
    return x0, y0, rows.reshape(nrows, cols)


def contour_lines(grid, step, interval):
    """Contours through a height_grid every `interval` m.

    Returns [(height, line)], a line a list of (east m, north m); a closed
    one ends where it starts. Each square of four known grid points is cut
    where the heights cross a contour (marching squares), and the cuts are
    joined up through the grid edges they share.
    """
    x0, y0, rows = grid
    out = []
    if rows.size == 0 or np.isnan(rows).all():
        return out
    nr, nc = rows.shape
    known = ~np.isnan(rows)
    square = known[:-1, :-1] & known[:-1, 1:] & known[1:, :-1] & known[1:, 1:]
    # Grid edges by number: the ones along a row, then the ones up a column.
    n_along = nr * (nc - 1)
    along = np.arange(n_along).reshape(nr, nc - 1)
    up = n_along + np.arange((nr - 1) * nc).reshape(nr - 1, nc)
    # A square's edges, round from its lower left corner: bottom, right, top,
    # left.
    edges = np.stack([along[:-1], up[:, 1:], along[1:], up[:, :-1]],
                     -1).reshape(-1, 4)
    za, zb = rows[:, :-1], rows[:, 1:]            # the ends of the edges along
    ua, ub = rows[:-1, :], rows[1:, :]            # and up
    ii, jj = np.arange(nc), np.arange(nr)
    for k in range(math.ceil(np.nanmin(rows) / interval),
                   math.floor(np.nanmax(rows) / interval) + 1):
        level = k * interval
        below = rows < level
        cross_along = below[:, :-1] != below[:, 1:]
        cross_up = below[:-1, :] != below[1:, :]
        crossed = np.stack([cross_along[:-1], cross_up[:, 1:],
                            cross_along[1:], cross_up[:, :-1]], -1)
        crossed &= square[..., None]
        crossed = crossed.reshape(-1, 4)
        hit = crossed.sum(1)
        if not hit.any():
            continue
        # Two edges crossed: one cut between them. Four: a saddle, cut
        # bottom to right and top to left.
        two = np.argsort(~crossed[hit == 2], axis=1, kind="stable")[:, :2]
        two_edges = np.take_along_axis(edges[hit == 2], two, 1)
        four = edges[hit == 4]
        cuts = np.concatenate([two_edges, four[:, :2], four[:, 2:]])
        # Where each crossed edge is cut, in map metres.
        with np.errstate(invalid="ignore", divide="ignore"):
            t_along = (level - za) / (zb - za)
            t_up = (level - ua) / (ub - ua)
        px = np.concatenate([(x0 + (ii[None, :-1] + t_along) * step).ravel(),
                             np.broadcast_to(x0 + ii[None, :] * step,
                                             (nr - 1, nc)).ravel()])
        py = np.concatenate([np.broadcast_to(y0 + jj[:, None] * step,
                                             (nr, nc - 1)).ravel(),
                             (y0 + (jj[:-1, None] + t_up) * step).ravel()])
        # Ends of cuts: end e is cut e % m's first end, or its second from m
        # on. An edge is shared by at most two cuts: pair their ends up.
        m = len(cuts)
        end_edge = np.concatenate([cuts[:, 0], cuts[:, 1]])
        order = np.argsort(end_edge, kind="stable")
        same = end_edge[order[1:]] == end_edge[order[:-1]]
        partner = np.full(2 * m, -1)
        partner[order[:-1][same]] = order[1:][same]
        partner[order[1:][same]] = order[:-1][same]
        part, edge_of, used = partner.tolist(), end_edge.tolist(), bytearray(m)

        def walk(e):
            """The edges passed going on out of end e."""
            passed = []
            while True:
                passed.append(edge_of[e])
                p = part[e]
                if p < 0 or used[p % m]:
                    return passed
                used[p % m] = 1
                e = p + m if p < m else p - m       # out the cut's other end

        for c in range(m):
            if not used[c]:
                used[c] = 1
                # Round a ring, forward comes back to the edge it started on.
                line = walk(c)[::-1] + walk(c + m)
                out.append((level, list(zip(px[line].tolist(),
                                            py[line].tolist()))))
    return out


def contours_on_body(cells, frame, radius, half_w, half_h, step, interval):
    """contour_lines over a map, each point turned into a unit vector on the
    body, so the lines can be drawn again on a map moved since (to_map)
    without working them out again."""
    grid = height_grid(cells, half_w, half_h, step)
    c, e, nth = (np.array(v) for v in frame)
    out = []
    for level, line in contour_lines(grid, step, interval):
        xy = np.array(line) / radius
        p = c + xy[:, :1] * e + xy[:, 1:] * nth         # from_map, all at once
        p /= np.linalg.norm(p, axis=1)[:, None]
        out.append((level, list(map(tuple, p.tolist()))))
    return out


RELIEF_STEP = 50.0      # metres between the filled view's grid points, at least
RELIEF_REACH_M = 400.0  # the fill goes no further than this from a measured cell
RELIEF_LIFT = 2.0       # slopes are drawn this much steeper, so gentle ground shades
SUN = _unit((-0.6, 0.6, 0.53))      # map frame, y north, z up: upper left


def relief_grid(cells, half_w, half_h, step):
    """What the filled and shaded view is drawn from: (x0, y0, step, heights,
    light). Heights are height_grid's (NaN where nothing is near enough);
    `light` is the same shape, a brightness for height_colour: 0.45 on ground
    facing away from the sun, about 1.1 facing it, 0.85 on the flat.
    """
    reach = max(RELIEF_REACH_M, 2 * step)
    x0, y0, rows = height_grid(cells, half_w, half_h, step, reach=reach,
                               least=2)
    # Slope across each point; at an edge, from the one side there is.
    pad = np.pad(rows, 1, constant_values=np.nan)
    span = []
    for a, b in ((pad[1:-1, :-2], pad[1:-1, 2:]),
                 (pad[:-2, 1:-1], pad[2:, 1:-1])):
        known = (~np.isnan(a)).astype(int) + ~np.isnan(b)
        rise = np.where(np.isnan(b), rows, b) - np.where(np.isnan(a), rows, a)
        span.append(np.divide(rise, known * step, out=np.zeros_like(rows),
                              where=known > 0))
    nx_, ny_ = -span[0] * RELIEF_LIFT, -span[1] * RELIEF_LIFT
    shade = np.maximum(0.0, (nx_ * SUN[0] + ny_ * SUN[1] + SUN[2])
                       / np.sqrt(nx_ * nx_ + ny_ * ny_ + 1.0))
    light = np.where(np.isnan(rows), np.nan,
                     np.minimum(1.15, 0.45 + 0.75 * shade))
    return x0, y0, step, rows, light


def frame_shift(frame0, frame, radius):
    """(east m, north m, cos, sin): where a map around `frame` sits on the map
    around frame0, to turn a point of the one into the other with
    X = east + cos * x - sin * y, Y = north + sin * x + cos * y. None if
    frame is off frame0's near half. A flat approximation, good for the small
    move between two maps tens of km apart."""
    o = to_map(frame[0], frame0, radius)
    n = to_map(from_map(0.0, 1000.0, frame, radius), frame0, radius)
    if o is None or n is None:
        return None
    vx, vy = n[0] - o[0], n[1] - o[1]
    d = math.hypot(vx, vy)
    return o[0], o[1], vy / d, -vx / d


def relief_image(relief, shift, w, h, mpp, block, lo, hi):
    """The filled and shaded view of a w x h px map, one pixel to a `block` px
    square of the map, so the result is drawn small and then zoomed by
    `block` (Tk does that natively, much faster than pixel by pixel).

    `relief` as relief_grid gives it, `shift` as frame_shift gives it for the
    map being drawn. Returns an (h // block, w // block, 3) array of 8 bit
    RGB, coloured on a lo..hi scale; GREY where nothing is under a block.
    """
    x0, y0, step, zs, light = relief
    out = np.empty((h // block, w // block, 3), np.uint8)
    out[:] = [int(GREY[i:i + 2], 16) for i in (1, 3, 5)]
    if zs.size == 0:
        return out
    ox, oy, c, s = shift
    nj, ni = zs.shape
    # Each block's middle, on the map and then on the relief grid.
    x = ((np.arange(w // block) + 0.5) * block - w / 2) * mpp
    y = (h / 2 - (np.arange(h // block) + 0.5) * block) * mpp
    gi = (ox + c * x[None, :] - s * y[:, None] - x0) / step
    gj = (oy + s * x[None, :] + c * y[:, None] - y0) / step
    inside = (gi >= 0) & (gi <= ni - 1) & (gj >= 0) & (gj <= nj - 1)
    gi, gj = np.where(inside, gi, 0.0), np.where(inside, gj, 0.0)
    i0 = np.minimum(gi.astype(int), max(ni - 2, 0))
    j0 = np.minimum(gj.astype(int), max(nj - 2, 0))
    i1, j1 = np.minimum(i0 + 1, ni - 1), np.minimum(j0 + 1, nj - 1)
    fx, fy = gi - i0, gj - j0

    def mix(g):
        return ((g[j0, i0] * (1 - fx) + g[j0, i1] * fx) * (1 - fy)
                + (g[j1, i0] * (1 - fx) + g[j1, i1] * fx) * fy)

    z, lt = mix(zs), mix(light)
    # At the edge of the fill a corner is unknown: the nearest point.
    edge = np.isnan(z)
    jn, i_n = np.where(fy >= 0.5, j1, j0), np.where(fx >= 0.5, i1, i0)
    z = np.where(edge, zs[jn, i_n], z)
    lt = np.where(edge, light[jn, i_n], lt)
    fill = inside & ~np.isnan(z)
    out[fill] = height_colours(z[fill], lo, hi, lt[fill])
    return out


def covers(frame0, half0, frame, radius, half_w, half_h):
    """True if a map half_w x half_h either side of `frame`'s middle lies
    inside the half0 = (half_w, half_h) map around frame0. Close enough for
    the small turn between two frames tens of km apart."""
    xy = to_map(frame[0], frame0, radius)
    return (xy is not None and abs(xy[0]) + half_w <= half0[0]
            and abs(xy[1]) + half_h <= half0[1])


def in_view(line, half_w, half_h):
    """The stretches of a line (map metres) inside the map, each with the
    point either side of it so it runs right to the edge."""
    inside = [abs(x) <= half_w and abs(y) <= half_h for x, y in line]
    runs, run = [], []
    for k, p in enumerate(line):
        if any(inside[max(0, k - 1):k + 2]):
            run.append(p)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return [r for r in runs if len(r) > 1]


def is_index(level, interval):
    """The thicker, labelled contours: every INDEX_EVERY-th, counted from 0."""
    return round(level / interval) % INDEX_EVERY == 0


def half_way(line, least):
    """The point half way along a line, or None if it is shorter than
    `least`: where a contour's height label goes."""
    legs = [math.dist(p, q) for p, q in zip(line, line[1:])]
    if sum(legs) < least:
        return None
    left = sum(legs) / 2
    for (p, q), d in zip(zip(line, line[1:]), legs):
        if left <= d:
            t = left / d
            return p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])
        left -= d
    return line[-1]


def centre_of(rec, area_m=50_000.0):
    """Unit vector at the middle of the busiest survey area, or None.

    Cells are grouped into tiles about `area_m` across and the tile with the
    most cells wins. The middle of all of them would be no good: two areas
    surveyed far apart put it on empty ground between them.
    """
    if not rec or not rec["cells"]:
        return None
    n = rec["n"]
    m = min(n, cells_per_face(rec["radius"], area_m))
    groups = {}
    for k in rec["cells"]:
        face, i, j = unkey(k)
        groups.setdefault((face, i * m // n, j * m // n), []).append(
            cell_centre(face, i, j, n))
    pts = max(groups.values(), key=len)
    return _unit(tuple(sum(p[i] for p in pts) for i in range(3)))


# Hypsometric tints, as on relief maps. Low to high: green, light green,
# yellow green, tan, pale brown, white, lighter at every step so higher
# ground always looks higher, and the lowest still lighter than GREY. Not
# blue: the globe is.
RAMP = ("#47884F", "#669F4C", "#A3B85E", "#D4B97C", "#E8D5BC", "#FAF7F2")
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


def height_colours(h, lo, hi, light):
    """height_colour for arrays of heights and lights: (..., 3) 8 bit RGB."""
    ramp = np.array([[int(c[k:k + 2], 16) for k in (1, 3, 5)] for c in RAMP],
                    float)
    t = (np.full(np.shape(h), 0.5) if hi <= lo
         else np.clip((np.asarray(h) - lo) / (hi - lo), 0.0, 1.0))
    t = t * (len(RAMP) - 1)
    i = np.minimum(t.astype(int), len(RAMP) - 2)
    rgb = ramp[i] + (ramp[i + 1] - ramp[i]) * (t - i)[..., None]
    return np.clip(np.rint(rgb * np.asarray(light)[..., None]), 0,
                   255).astype(np.uint8)


LIGHT = _unit((-0.4, -1.0, 0.5))   # view frame: from the viewer, upper left


def tile_light(normal):
    """Brightness 0.45..1 of a tile facing `normal` (view frame, any length),
    so the mesh reads as a ball and not a flat disc."""
    return 0.45 + 0.55 * max(0.0, _dot(_unit(normal), LIGHT))


def height_range(heights):
    """(lo, hi) of the heights given, (0, 0) if there are none."""
    hs = [h for h in heights if h is not None]
    return (min(hs), max(hs)) if hs else (0.0, 0.0)
