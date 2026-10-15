"""Survey patterns: the course to fly so the height map fills in evenly.

No tkinter here, so all of it can be tested without a window. The overlay
(sc_locator.py) shows what guide() returns; the height map draws corners().

An expanding square starts at a point of interest and winds outward
clockwise: north, east, south, west, with legs of 1, 1, 2, 2, 3, 3 ... times
the line spacing. Every leg is due north, east, south or west, so it can be
flown on the ship's compass alone. It never ends by itself: the pilot ends
the survey when the area covered is big enough, and can resume it later at
the leg they stopped on, or skip ahead past the legs already flown.

Positions are worked out on a flat map around the start (heightmap.to_map),
which is true to well under a degree for the tens of km a survey covers.
"""
import math

import heightmap

# The patterns offered when a survey starts: name -> line spacing in metres,
# None for flying wherever you like.
PATTERNS = {
    "free fly": None,
    "square, lines 500 m apart": 500.0,
    "square, lines 1000 m apart": 1000.0,
}
START_M = 300.0         # a leg is joined this close to its start or its line
ON_LINE_M = 50.0        # closer than this to the line counts as on it
# The steer heading meets the line this far ahead, and never cuts across it
# steeper than this. Softer than the 1000 m and 45 deg first flown, which
# swung pilots 500-750 m to and fro across legs 15 and 19 (2026-10-15).
LOOK_AHEAD_M = 1500.0
MAX_CUT_DEG = 30.0
TURNED_DEG = 45.0       # moving within this of a leg's heading: turned onto it
# Speed to fly a leg at. The HUD gives a fix every ~2 s, so a short leg flown
# fast is over in a few fixes and its corner comes before the pilot can react
# (New Babbage, 2026-10-15: at 100 m/s the first 500 m legs were missed; at
# 29 m/s they were flown, a reading every ~60 m).
SHORT_LEG_M = 2000.0
SLOW_MS, FAST_MS = 30.0, 100.0
# Warn of a corner this many seconds ahead at that speed, and say TURN NOW
# about a fix and a half before it.
WARN_S, NOW_S = 8.0, 3.0
FLOWN = 0.5             # a leg with a cell near this much of it was flown
SAMPLE_M = 100.0        # how finely a leg is checked for cells near it
HEADINGS = ((0, "N"), (90, "E"), (180, "S"), (270, "W"))
STEP = ((0.0, 1.0), (1.0, 0.0), (0.0, -1.0), (-1.0, 0.0))  # (east, north)


def new_plan(pattern, planet=None, origin=None):
    """A survey plan, or None for free fly. `origin` is the planet-frame
    point the square starts at; None starts it wherever you first get a fix.

    `leg` is the leg being flown (0 is the first) and `joined` whether you
    have reached it yet: a new plan waits for you at its start, a resumed one
    at the start of the leg it stopped on. `turning` is set at each corner
    until you have come round onto the new leg.
    """
    spacing = PATTERNS[pattern]
    if spacing is None:
        return None
    return {"pattern": pattern, "spacing": spacing, "planet": planet,
            "origin": None if origin is None else tuple(origin),
            "leg": 0, "joined": False, "turning": False}


def turned_to(heading, target):
    """True if a heading (degrees, None if unknown) is within TURNED_DEG of
    `target`."""
    return (heading is not None
            and abs((heading - target + 180.0) % 360.0 - 180.0) <= TURNED_DEG)


def leg_length(k, spacing):
    return (k // 2 + 1) * spacing


def speed_for(k, spacing):
    """The speed (m/s) to fly leg k at: slow on the short legs."""
    return SLOW_MS if leg_length(k, spacing) < SHORT_LEG_M else FAST_MS


def corners(n, spacing):
    """The first n + 1 corners of the square, (east m, north m) from its
    start: leg k runs from corner k to corner k + 1."""
    pts = [(0.0, 0.0)]
    for k in range(n):
        dx, dy = STEP[k % 4]
        x, y = pts[-1]
        d = leg_length(k, spacing)
        pts.append((x + dx * d, y + dy * d))
    return pts


def step(plan, planet, pos, radius, heading=None):
    """Follow the pilot along the plan: wait until they join the leg, then
    move on to the next one at each corner. Changes `plan` in place and
    returns what the overlay shows: guide(), or while joining
    {"to_join" m, "bearing", "leg", "heading", "name"}.

    `heading` is the way the pilot is moving (degrees from north), or None
    when not known: it tells a turn made early from no turn at all.
    """
    if plan["planet"] is None:
        plan["planet"] = planet
    if planet != plan["planet"]:
        return {"away": plan["planet"]}
    if plan["origin"] is None:          # starts where you are: joined already
        plan["origin"], plan["joined"] = tuple(pos), True
    xy = heightmap.to_map(pos, heightmap.local_frame(plan["origin"]), radius)
    if not plan["joined"]:
        k = plan["leg"]
        sx, sy = corners(k, plan["spacing"])[-1]
        heading, name = HEADINGS[k % 4]
        far = {"leg": k + 1, "heading": heading, "name": name,
               "speed": speed_for(k, plan["spacing"])}
        if xy is None:                  # the far side of the body
            return dict(far, to_join=math.dist(pos, plan["origin"]),
                        bearing=None)
        a, off = along(plan, xy), across(plan, xy)
        d = math.hypot(xy[0] - sx, xy[1] - sy)
        # At its start, or already on its line: a pilot resuming in flight
        # need not go back to the corner.
        if d > START_M and not (abs(off) <= START_M and
                                0 <= a <= leg_length(k, plan["spacing"])):
            return dict(far, to_join=d, bearing=math.degrees(
                math.atan2(sx - xy[0], sy - xy[1])) % 360.0)
        plan["joined"], plan["turning"] = True, False
    if xy is None:
        return {"away": plan["planet"]}
    # On to the next leg once past the end of this one, along its own
    # direction (not "at the corner": a pilot who drifted wide would never
    # get there), or once already turned onto it in the last START_M, as
    # TURN NOW comes up to that far before the corner.
    while True:
        k, spacing = plan["leg"], plan["spacing"]
        to_go = leg_length(k, spacing) - along(plan, xy)
        if to_go > 0 and not (to_go <= START_M and
                              turned_to(heading, HEADINGS[(k + 1) % 4][0])):
            break
        plan["leg"], plan["turning"] = k + 1, True
    # Round onto the new leg: moving its way, or plainly on it already.
    if plan.get("turning") and (
            turned_to(heading, HEADINGS[plan["leg"] % 4][0])
            or (along(plan, xy) >= START_M
                and abs(across(plan, xy)) <= START_M)):
        plan["turning"] = False
    return guide(plan, xy)


def along(plan, xy):
    """How far along the current leg (m) you are, measured on its line."""
    k = plan["leg"]
    sx, sy = corners(k, plan["spacing"])[-1]
    dx, dy = STEP[k % 4]
    return (xy[0] - sx) * dx + (xy[1] - sy) * dy


def across(plan, xy):
    """How far (m) right of the current leg's line you are, - for left."""
    k = plan["leg"]
    sx, sy = corners(k, plan["spacing"])[-1]
    dx, dy = STEP[k % 4]
    return (xy[0] - sx) * dy - (xy[1] - sy) * dx


def guide(plan, xy):
    """What to fly, for a pilot at map point xy on the current leg.

    {"leg", "heading", "name", "to_go" m, "next_heading", "next_name",
     "off" m right of the line (- left), "steer" heading, "size" (w, h) m,
     "speed" m/s to fly it at, "turn" None, "soon" or "now" for the corner
     ahead, or "late" while not yet round onto this leg}.
    The steer heading brings you back onto the line LOOK_AHEAD_M ahead, so it
    also holds you against wind pushing you off it.
    """
    k, spacing = plan["leg"], plan["spacing"]
    pts = corners(k + 1, spacing)
    off = across(plan, xy)
    cut = min(MAX_CUT_DEG, math.degrees(math.atan2(abs(off), LOOK_AHEAD_M)))
    heading, name = HEADINGS[k % 4]
    steer = (heading - math.copysign(cut, off)) % 360.0
    done = pts[:k + 1] + [xy]
    xs, ys = [p[0] for p in done], [p[1] for p in done]
    to_go = max(0.0, leg_length(k, spacing) - along(plan, xy))
    speed = speed_for(k, spacing)
    turn = ("late" if plan.get("turning") else
            "now" if to_go <= speed * NOW_S else
            "soon" if to_go <= speed * WARN_S else None)
    return {"leg": k + 1, "heading": heading, "name": name, "to_go": to_go,
            "next_heading": HEADINGS[(k + 1) % 4][0],
            "next_name": HEADINGS[(k + 1) % 4][1],
            "off": off, "steer": steer,
            "size": (max(xs) - min(xs), max(ys) - min(ys)),
            "speed": speed, "turn": turn}


def skip_to(plan, cells):
    """The leg after the outermost one already flown, never one behind the
    plan's own: where to pick up a square whose middle was flown again.

    `cells` are measured cells as (east m, north m) from the square's start.
    A leg counts as flown when at least FLOWN of it has a cell within half a
    line spacing, which is as far as a pilot drifts and still flies that leg
    rather than the next. Legs are checked out to the furthest cell.
    """
    spacing = plan["spacing"]
    near = spacing / 2
    # Every SAMPLE_M square with a cell within `near` of it, so checking a
    # point on a leg is one lookup: the menu asks this on every click.
    marked = set()
    r = math.ceil(near / SAMPLE_M)
    for x, y in cells:
        bx, by = round(x / SAMPLE_M), round(y / SAMPLE_M)
        for i in range(bx - r, bx + r + 1):
            for j in range(by - r, by + r + 1):
                if math.hypot(i * SAMPLE_M - x, j * SAMPLE_M - y) <= near:
                    marked.add((i, j))
    reach = max((max(abs(x), abs(y)) for x, y in cells), default=0.0)

    def has_cell(px, py):
        return (round(px / SAMPLE_M), round(py / SAMPLE_M)) in marked

    last, k = -1, 0
    # Every 4 legs the square grows a spacing each way; stop past the cells.
    while (k // 4) * spacing <= reach + spacing:
        (ax, ay), (bx, by) = corners(k + 1, spacing)[k:]
        n = max(1, int(leg_length(k, spacing) // SAMPLE_M))
        hits = sum(has_cell(ax + (bx - ax) * i / n, ay + (by - ay) * i / n)
                   for i in range(n + 1))
        if hits >= FLOWN * (n + 1):
            last = k
        k += 1
    return max(plan["leg"], last + 1)
