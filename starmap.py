"""Star map geometry: what goes on each map and where it lands on screen.

No tkinter here, so all of it can be tested without a window. The map window
(main_window.py) only draws what these functions return.

Frames, as in the rest of the program:

- system frame: star at the origin, metres. A body's centre is only known once
  it has been analysed (bodies.json "centre"), so only analysed bodies are
  placed on the system map.
- planet frame: metres from the body's centre, turning with it, +z its spin
  axis (north). Waypoints on a body are stored in this frame, so they are drawn
  on the zoomed-in view of that body, where the body is the centre.
"""
import math

SYSTEMS = ("stanton", "pyro", "nyx")


def system_objects(bodies, waypoints, system):
    """What the system map of `system` shows.

    Returns {"bodies": [...], "points": [...], "unplaced": [...]}:
    - bodies: analysed bodies with a known centre, as dicts with key, centre,
      radius and label (label "moon" for moons, else whatever was set)
    - points: deep-space waypoints, as (id, (x, y, z)) in the system frame
    - unplaced: names of bodies that carry waypoints but have no centre yet,
      sorted, so they can still be listed and opened
    """
    placed = []
    for key, rec in bodies.items():
        if rec.get("system") != system or rec.get("scope") == "space":
            continue
        if not rec.get("centre"):
            continue
        placed.append({"key": key, "centre": tuple(rec["centre"]),
                       "radius": rec.get("radius_m"),
                       "label": rec.get("label")})
    placed.sort(key=lambda b: b["key"])
    keys = {b["key"] for b in placed}
    points, unplaced = [], set()
    for w in waypoints:
        if w.get("system") != system:
            continue
        if w.get("frame") == "system":
            points.append((w["id"], (w["x"], w["y"], w["z"])))
        elif w["planet"] not in keys:
            unplaced.add(w["planet"])
    return {"bodies": placed, "points": points, "unplaced": sorted(unplaced)}


def body_points(waypoints, body):
    """Waypoints on `body`, as (id, (x, y, z)) in the planet frame. By id:
    names repeat (many places are a Cave)."""
    return [(w["id"], (w["x"], w["y"], w["z"])) for w in waypoints
            if w["planet"] == body and w.get("frame", "planet") == "planet"]


def body_radius(body, bodies, waypoints, table):
    """The body's radius in metres: its analysis, the built-in table, or the
    radius saved with one of its waypoints. None if nothing knows it."""
    rec = bodies.get(body) or {}
    if rec.get("radius_m"):
        return rec["radius_m"]
    if table.get(body):
        return table[body]
    return next((w["radius"] for w in waypoints
                 if w["planet"] == body and w.get("radius")), None)


def rotate(p, yaw, pitch):
    """Turn point p by `yaw` about z, then tip it by `pitch` about x (radians).

    The viewer looks along +y after the turn, so of the result x is screen
    right, z is screen up and y is depth: larger y is farther away.
    """
    x, y, z = p
    cy, sy = math.cos(yaw), math.sin(yaw)
    x, y = x * cy - y * sy, x * sy + y * cy
    cp, sp = math.cos(pitch), math.sin(pitch)
    y, z = y * cp - z * sp, y * sp + z * cp
    return x, y, z


def project(p, yaw, pitch, scale, cx, cy):
    """World point -> (screen x, screen y, depth). Orthographic: no
    perspective, so sizes do not change with distance and depth only orders
    the drawing."""
    x, y, z = rotate(p, yaw, pitch)
    return cx + x * scale, cy - z * scale, y


def fit_scale(points, width, height, fill=0.42):
    """Pixels per metre so the farthest point from the origin sits `fill` of
    the smaller window side from the centre. 1.0 if there is nothing to fit."""
    far = max((math.sqrt(sum(c * c for c in p)) for p in points), default=0.0)
    if far <= 0:
        return 1.0
    return fill * min(width, height) / far


def fit_view(spots, yaw, pitch, width, height, top=0):
    """Pixels per metre that just fit every spot in the window, seen from
    yaw/pitch with the origin in the middle, or None if nothing limits it.

    spots: [(point, (left, right, up, down))], how many px the spot's
    drawing (marker and name) reaches past its point each way. `top` px at
    the top are kept clear.
    """
    cx, cy = width / 2, height / 2
    best = None
    for p, (left, right, up, down) in spots:
        x, _, z = rotate(p, yaw, pitch)
        for room, d in ((width - cx - right, x), (cx - left, -x),
                        (cy - top - up, z), (height - cy - down, -z)):
            if d > 0:
                s = max(room, 1) / d
                best = s if best is None else min(best, s)
    return best


def hidden_by_sphere(sx, sy, depth, cx, cy, radius_px):
    """Is a projected point behind a sphere drawn at (cx, cy)?"""
    return depth > 0 and math.hypot(sx - cx, sy - cy) < radius_px


def graticule(radius, step=30, n=48):
    """Latitude and longitude lines on a sphere, as lists of 3D points.

    Without them a turning sphere looks exactly like a still one.
    """
    lines = []
    for lat in range(-90 + step, 90, step):
        r, z = radius * math.cos(math.radians(lat)), \
            radius * math.sin(math.radians(lat))
        lines.append([(r * math.cos(2 * math.pi * i / n),
                       r * math.sin(2 * math.pi * i / n), z)
                      for i in range(n + 1)])
    for lon in range(0, 360, step):
        a = math.radians(lon)
        lines.append([(radius * math.cos(t) * math.cos(a),
                       radius * math.cos(t) * math.sin(a),
                       radius * math.sin(t))
                      for t in (math.pi * (i / n - 0.5) for i in range(n + 1))])
    return lines


def front_segments(points, yaw, pitch, scale, cx, cy):
    """Project a line on a sphere and keep only the runs on the near side,
    as lists of flat (x, y, x, y, ...) coordinates ready for a canvas line."""
    runs, run = [], []
    for p in points:
        sx, sy, depth = project(p, yaw, pitch, scale, cx, cy)
        if depth <= 0:
            run += [sx, sy]
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return [r for r in runs if len(r) >= 4]


def orbit_ring(distance, n=96):
    """A circle about the star in the system plane, at `distance`."""
    return [(distance * math.cos(2 * math.pi * i / n),
             distance * math.sin(2 * math.pi * i / n), 0.0)
            for i in range(n + 1)]


# --- around one body ----------------------------------------------------------

# Which moons belong to which planet. Membership only: where a moon is comes
# from its own analysis, as a planet's does.
MOONS = {
    "hurston": ("aberdeen", "arial", "ita", "magda"),
    "crusader": ("cellin", "daymar", "yela"),
    "arccorp": ("lyria", "wala"),
    "microtech": ("calliope", "clio", "euterpe"),
    "pyrov": ("adir", "fairo", "fuego", "ignis", "vatra", "vuur"),
}

# A place in space this close to a body is shown around it, unless placed by
# hand. Close enough for stations and bases by a planet or moon (Z48-013 is 93
# Mm from Crusader), short of the Lagrange points, which sit Gm out.
POI_RANGE_M = 1.0e9


def placed_bodies(bodies, system):
    """{key: centre} for the bodies of `system` whose centre is known."""
    return {b["key"]: b["centre"] for b in
            system_objects(bodies, [], system)["bodies"]}


def attached_body(w, placed):
    """The body a deep-space waypoint is shown around, or None.

    A body picked by hand ("parent") wins, and "none" keeps it off every body;
    otherwise it goes to the nearest placed body within POI_RANGE_M.
    """
    if w.get("frame") != "system":
        return None
    if "parent" in w:
        return None if w["parent"] == "none" else w["parent"]
    p = (w["x"], w["y"], w["z"])
    best = min(((math.dist(p, c), k) for k, c in placed.items()), default=None)
    return best[1] if best and best[0] <= POI_RANGE_M else None


def waypoint_view(w, bodies):
    """Where the star map shows waypoint w, as (system, body, (lat, lon)), or
    None when its system is not known.

    body is None for the system view. (lat, lon) is the spot to look straight
    down on, so the waypoint faces the viewer; None when the view need not
    turn, as for a point in space.
    """
    system = w.get("system")
    if system not in SYSTEMS:
        return None
    if w.get("frame", "planet") == "planet":
        return system, w["planet"], facing((w["x"], w["y"], w["z"]))
    return system, attached_body(w, placed_bodies(bodies, system)), None


def facing(p):
    """(lat, lon) in degrees of planet-frame point p: the spot to look
    straight down on to have p face the viewer."""
    x, y, z = p
    lat = math.degrees(math.asin(z / math.sqrt(x * x + y * y + z * z)))
    return lat, math.degrees(math.atan2(y, x))


def spin_angle(rec, t):
    """Radians to turn a body's own frame about +z into the system's
    orientation at time t. 0 when its spin has not been measured.

    The analysis stores the longitude the star was over at t_ref and how fast
    that moves; the star's direction from the centre is known, so the turn is
    the difference. The axis is taken as the system z, which every analysis so
    far has found to well under a degree.
    """
    if (rec or {}).get("kind") != "rotating" or not rec.get("centre"):
        return 0.0
    cx, cy = rec["centre"][0], rec["centre"][1]
    sub = rec["subsolar_lon_ref"] - rec["omega_deg_s"] * (t - rec["t_ref"])
    return math.atan2(-cy, -cx) - math.radians(sub)


# Nothing moves faster than light, so a step on the star map that would is a
# misread planet-frame fix: Crusader once read 77.6 Gm out among 19.1 Gm.
LIGHT_M_S = 299_792_458.0


def faster_than_light(prev, cur):
    """prev, cur: (time, system point). Would getting from one to the other
    outrun light? Readings less than a second apart are allowed a second."""
    (t0, p0), (t1, p1) = prev, cur
    return math.dist(p0, p1) > LIGHT_M_S * max(t1 - t0, 1.0)


def system_point(rec, p, t):
    """Planet-frame point p on the body analysed as `rec`, in the system
    frame at time t, or None when its centre and spin are not both known.

    What puts you on the system map during quantum travel: the game stops
    updating its own system position for the whole jump, while the
    planet-frame fix carries on.
    """
    if (rec or {}).get("kind") != "rotating" or not rec.get("centre"):
        return None
    x, y, z = rotate(p, spin_angle(rec, t), 0.0)
    cx, cy, cz = rec["centre"]
    return cx + x, cy + y, cz + z


def compress(offset, inner):
    """Pull a point beyond `inner` in toward it: its direction kept, its
    distance on a log scale, so moons and stations fit beside a true-size
    globe. Points within `inner` are left where they are."""
    r = math.sqrt(sum(c * c for c in offset))
    if r <= inner:
        return tuple(offset)
    k = inner * (1 + 0.6 * math.log10(r / inner)) / r
    return tuple(c * k for c in offset)


def surroundings(body, bodies, waypoints, system):
    """What is shown around `body`, as offsets from its centre (system
    orientation, metres, not yet compressed).

    {"moons": [(key, offset)], "unplaced_moons": [key],
     "points": [(id, offset)]}
    Without a known centre for the body nothing can be placed around it, and
    all its moons are unplaced.
    """
    placed = placed_bodies(bodies, system)
    centre = placed.get(body)
    out = {"moons": [], "unplaced_moons": [], "points": []}
    for moon in MOONS.get(body, ()):
        if centre is not None and moon in placed:
            out["moons"].append((moon, tuple(a - b for a, b in
                                             zip(placed[moon], centre))))
        else:
            out["unplaced_moons"].append(moon)
    if centre is None:
        return out
    for w in waypoints:
        if w.get("system") == system and attached_body(w, placed) == body:
            out["points"].append((w["id"], (w["x"] - centre[0],
                                              w["y"] - centre[1],
                                              w["z"] - centre[2])))
    return out


def view_from(lat, lon, spin):
    """(yaw, pitch) that look at a body straight down from above (lat, lon)
    in its own frame, given how far it has turned (spin_angle).

    Used for the orbital marker views. From either pole the 0 meridian (OM-5)
    ends up at the bottom of the screen.
    """
    return -math.pi / 2 - math.radians(lon) - spin, math.radians(lat)


def turn_per_px(zoom, slowdown, base=0.01):
    """Radians a body view turns per pixel dragged at `zoom`: `base` at zoom
    1 or less, divided by zoom ** slowdown above it. 0 turns the same at
    every zoom; at 1 the ground under the mouse keeps pace with it, so a
    POI being zoomed into is not dragged past."""
    return base / max(1.0, zoom) ** slowdown
