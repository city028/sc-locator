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
"""
import math

import numpy as np

# What can be traced, and how the SURVEY menu offers it.
KINDS = {"railway": "trace a railway", "lake": "survey a lake",
         "sea": "survey a sea", "waterway": "survey a waterway"}
LEAST = {"railway": 2, "lake": 3, "sea": 3,   # points it takes to be kept
         "waterway": 2}
WATER = ("lake", "sea")     # kinds with a level, whose fan is water
STEP_M = 25.0           # a fix closer than this to the last point adds nothing
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
    within STEP_M of the last point is too, so hovering adds nothing.
    """
    if trace["planet"] is None:
        trace["planet"] = planet
    if planet != trace["planet"]:
        return f"not added: the trace is on {trace['planet']}"
    pts = trace["points"]
    if pts and math.dist(pts[-1], pos) < STEP_M:
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
