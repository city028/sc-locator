"""Traced lines: a railway recorded by flying (or riding) along it, or the
shore of a lake flown round.

No tkinter here, so all of it can be tested without a window. The overlay
(sc_locator.py) adds the fixes; the height map draws them.

A trace is the fixes taken along a feature, in the planet frame, so it stays
on the same ground as the body turns:

    {"name": "railway-001", "kind": "railway", "planet": body or None,
     "started": time, "points": [[x, y, z], ...]}

A railway's height does not matter: only where it runs. It is drawn with the
map symbol for a standard gauge railroad, multiple track (USGS topographic
map symbols, examples/topomapsymbols.pdf): two lines with ticks across them.

A lake starts skimming the water and then follows the waterline round. Its
first point is in the water, and also gives its "level": the water's height
over the reference sphere, so the surface curves with the body. Everything
between that first point and the route is water (see fan): flat blue on
the height map, whose own heights inside it are ignored (the HUD's ground
over a lake is neither the bed nor the surface).

A sea is a lake too big to skim slowly: the same level and the same fan,
only flown faster (see TRACE_MAX_SPEED in sc_locator.py).

A waterway (a river or a canal) is flown along its middle from one end to
the other. It is drawn as water, a line `width_m` wide to scale, set from
waterway_width_m when it starts and kept on the trace, so it can be put
right once a waterway's real width is known.

A building is walked round on foot, close along its walls, in first person
(the HUD gives the camera's position, which in third person trails the
character), a point every BUILDING_STEP_M. The walk is kept as it was
walked and squared off when drawn (see square): an outline of right
angles only, turned to the building's walls, pulled in `offset_m` (set
from building_wall_offset_m when it starts and kept on the trace) from
where the walk went. Its height does not matter: these structures stand on
legs over uneven ground. About a metre is all it needs to be right to
(building-001, Wala, 2026-10-25: squared, it matched the in-game map).
"""
import math

import numpy as np

# What can be traced, and how the SURVEY menu offers it.
KINDS = {"railway": "trace a railway", "lake": "survey a lake",
         "sea": "survey a sea", "waterway": "survey a waterway",
         "building": "survey a building"}
LEAST = {"railway": 2, "lake": 3, "sea": 3,   # points it takes to be kept
         "waterway": 2, "building": 3}
WATER = ("lake", "sea")     # kinds with a level, whose fan is water
CLOSED = WATER + ("building",)  # kinds that close back to the start
STEP_M = 25.0           # a fix closer than this to the last point adds nothing
BUILDING_STEP_M = 1.0   # and for a building, walked: a point every ~1 m
BUILDING_MIN_M = 2.0    # a building's walls shorter than this are smoothed out
LAP_AWAY_M = 8.0        # a walk this far from its start has set off round
LAP_CLOSE_M = 4.0       # and back this close to it has been all the way round
TICK_EVERY_PX = 40.0    # ticks this far apart along the drawn line
TICK_HALF_PX = 6.0      # and reaching this far either side of it


def new_trace(kind, name, now, planet=None):
    """An empty trace. `planet` None takes the body of the first fix."""
    return {"name": name, "kind": kind, "planet": planet, "started": now,
            "points": []}


def trace_name(traces, kind):
    """First free '<kind>-NNN'."""
    existing = {t["name"] for t in traces}
    n = 1
    while f"{kind}-{n:03d}" in existing:
        n += 1
    return f"{kind}-{n:03d}"


def add_point(trace, planet, pos):
    """Add a fix to the trace. Returns why it was not added, or None.

    A fix on another body is refused: one trace stays on one body. One
    within STEP_M (BUILDING_STEP_M for a building) of the last point is too,
    so hovering adds nothing.
    """
    if trace["planet"] is None:
        trace["planet"] = planet
    if planet != trace["planet"]:
        return f"not added: the trace is on {trace['planet']}"
    pts = trace["points"]
    step = BUILDING_STEP_M if trace["kind"] == "building" else STEP_M
    if pts and math.dist(pts[-1], pos) < step:
        return "not added: too close to the last point"
    pts.append([round(c, 2) for c in pos])
    return None


def length(trace):
    """Metres along the trace, point to point."""
    pts = trace["points"]
    return sum(math.dist(p, q) for p, q in zip(pts, pts[1:]))


def fan(route):
    """A lake's water as triangles: from its first point (the start, in the
    water) to each step of the route, [(start, p, q)]. Everything between
    the start and the route flown, so a route that crosses itself or goes
    round twice still covers it all, where filling the outline even-odd
    would leave holes. The start must see the shore it fans out to across
    water: a headland it cannot see past is covered too."""
    return [(route[0], p, q) for p, q in zip(route[1:], route[2:])]


def inside(xs, ys, route):
    """Which of the points (xs[i], ys[i]) lie in a lake flown as `route`,
    a list of (x, y) map points (see fan): a numpy bool array. On the height
    map's projection straight lines are the shortest paths over the sphere,
    so this holds on the body too."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    out = np.zeros(xs.shape, bool)

    def side(a, b):             # > 0 left of a -> b, < 0 right of it
        return (b[0] - a[0]) * (ys - a[1]) - (b[1] - a[1]) * (xs - a[0])

    for a, b, c in fan(route):
        s1, s2, s3 = side(a, b), side(b, c), side(c, a)
        out |= (((s1 >= 0) & (s2 >= 0) & (s3 >= 0))
                | ((s1 <= 0) & (s2 <= 0) & (s3 <= 0)))
    return out


def inside_ring(x, y, ring):
    """Is (x, y) inside the outline `ring`, [(x, y)], closed back to its
    first point? Even-odd, so it holds for any outline square gives."""
    out = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            out = not out
    return out


def lap_end(route):
    """Where a walk round a building is back at its start: the index of its
    closest point to the start once it has been LAP_AWAY_M away and come
    back within LAP_CLOSE_M, or None if it has not got round yet. Points
    may be map (x, y) or planet-frame (x, y, z)."""
    start, away, best = route[0], False, None
    for i, p in enumerate(route):
        d = math.dist(p, start)
        if not away:
            away = d > LAP_AWAY_M
        elif d <= LAP_CLOSE_M:
            if best is None or d < math.dist(route[best], start):
                best = i
        elif best is not None:
            break               # back, and now off past the start
    return best


def square(route, offset=0.0, min_m=BUILDING_MIN_M):
    """A building walked round as `route`, a list of (x, y) map points, as
    the corners of an outline of right angles only: [(x, y)].

    The walls' direction is the mean of every step's, taken modulo 90 deg
    and weighted by its length. In those axes each step runs mostly along
    one of them, and the steps in a row along the same axis are one wall,
    standing at their length-weighted mean across it. The shortest wall
    under `min_m` is dropped and the walls either side of it become one,
    until none is that short: a step out and back, or the wobble of a
    straight walk, is smoothed out. Each wall is then pulled `offset` in
    towards the inside, and the corners are where the walls meet. The walk
    is closed back to its start. One that went on round past its start is
    cut where it got back there (lap_end): joined back from further on, it
    cut across the building (Pad 2, Wala, 2026-10-25: a 28 m diagonal).

    A walk too rough to give four walls comes out as the rectangle round
    it, in the same axes.
    """
    c = s = 0.0
    ring = list(route)
    end = lap_end(ring)
    if end is not None:
        ring = ring[:end + 1]
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        a = math.atan2(by - ay, bx - ax)
        d = math.hypot(bx - ax, by - ay)
        c, s = c + d * math.cos(4 * a), s + d * math.sin(4 * a)
    th = math.atan2(s, c) / 4
    co, si = math.cos(th), math.sin(th)
    uv = [(x * co + y * si, -x * si + y * co) for x, y in ring]

    def back(u, v):
        return u * co - v * si, u * si + v * co

    # Walls as [axis (0: along u, 1: along v), net length along it,
    # sum of weight * where it stands across, sum of weight].
    walls = []
    for (au, av), (bu, bv) in zip(uv, uv[1:] + uv[:1]):
        axis = 0 if abs(bu - au) >= abs(bv - av) else 1
        along = (bu - au, bv - av)[axis]
        across = ((av + bv) / 2, (au + bu) / 2)[axis]
        w = abs(along) + 1e-9
        walls.append([axis, along, across * w, w])

    def join(ws):
        out = []
        for wall in ws:
            if out and out[-1][0] == wall[0]:
                out[-1] = [wall[0]] + [p + q for p, q in
                                       zip(out[-1][1:], wall[1:])]
            else:
                out.append(list(wall))
        if len(out) > 1 and out[0][0] == out[-1][0]:    # round the start
            last = out.pop()
            out[0] = [last[0]] + [p + q for p, q in zip(last[1:], out[0][1:])]
        return out

    walls = join(walls)
    while len(walls) > 4:
        i = min(range(len(walls)), key=lambda k: abs(walls[k][1]))
        if abs(walls[i][1]) >= min_m:
            break
        del walls[i]
        walls = join(walls)
    if len(walls) < 4:
        us, vs = [p[0] for p in uv], [p[1] for p in uv]
        lo_u, hi_u = min(us) + offset, max(us) - offset
        lo_v, hi_v = min(vs) + offset, max(vs) - offset
        return [back(lo_u, lo_v), back(hi_u, lo_v), back(hi_u, hi_v),
                back(lo_u, hi_v)]
    # Inside is to the left of the way walked round anticlockwise, to the
    # right clockwise. Left of +u is +v; left of +v is -u.
    turn = 1 if sum(a[0] * b[1] - b[0] * a[1]
                    for a, b in zip(uv, uv[1:] + uv[:1])) > 0 else -1
    lines = []
    for axis, along, wsum, w in walls:
        left = (1 if along > 0 else -1) * (1 if axis == 0 else -1)
        lines.append((axis, wsum / w + offset * turn * left))
    return [back(*((p1, p0) if a0 == 0 else (p0, p1)))
            for (a0, p0), (_, p1) in zip(lines, lines[1:] + lines[:1])]


def ticks(line, every=TICK_EVERY_PX, half=TICK_HALF_PX):
    """The cross ticks of the railroad symbol along a line of screen points:
    [(x0, y0, x1, y1)], one every `every` px starting half that in, each
    square to the stretch of line it sits on and `half` px either side."""
    out, at = [], every / 2
    walked = 0.0
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        d = math.hypot(bx - ax, by - ay)
        if d == 0:
            continue
        ux, uy = (bx - ax) / d, (by - ay) / d
        while at <= walked + d:
            t = at - walked
            x, y = ax + ux * t, ay + uy * t
            out.append((x - uy * half, y + ux * half,
                        x + uy * half, y - ux * half))
            at += every
        walked += d
    return out
