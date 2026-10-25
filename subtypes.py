"""What kind of place a waypoint is (its SUB), and the icon each kind has.

A waypoint's type says what it is for (POI or survey); its SUB says what is
there: a city, a cave, a summit. The SUB picks the icon the star map and the
height map draw for it. An empty SUB, or one with no icon, keeps the plain
pin.

The SUBs in use are a list the user manages (Settings tab): [name, icon]
pairs, `icon` a key of ICONS or "" for the plain pin. SUBTYPES are the
built-in ones, each with its own drawing in ICONS, and the list to start
from; `use` puts the user's list in place (sc_locator keeps it in
subtypes.json). A built-in renamed keeps its icon, as the icon is its own
key, not the name.

To add a built-in kind: add its name to SUBTYPES and its drawing to ICONS.

An icon is a list of (kind, paint, coords) in a box from -1 to 1 each way,
y pointing down as on the screen:
    kind   "poly" (x, y, x, y, ...), "rect" / "oval" (x0, y0, x1, y1) filled,
           "ring" (x0, y0, x1, y1) outlined, "line" (x, y, x, y, ...) stroked
    paint  "fg" in the pin's colour, "bg" in the badge's (cut-outs)
"""
import math

SUBTYPES = (
    "Space Station", "Spaceport", "City", "Village", "Building", "Tower",
    "Turret", "Landing Pad", "Wreck", "Ruin", "Summit", "Jump Point",
    "Town Hall", "Radio Dish", "Cargo Elevator", "Shop", "Cave", "Minable",
    "Settlement", "Forward Operating Base (FOB)",
    "Planetary Alignment Facility (PAF)", "Orbital Laser Platform (OLP)",
    "PAF Bunker", "Ground Race Track", "Crash Site", "Derelict Ship",
)


def _dish():
    """A radio dish's bowl: half an ellipse below a chord from top left to
    bottom right, so it faces up and to the right."""
    (ax, ay), (bx, by) = (-0.85, -0.45), (0.45, 0.85)
    mx, my = (ax + bx) / 2, (ay + by) / 2
    half = math.dist((ax, ay), (bx, by)) / 2
    ux, uy = (bx - ax) / (2 * half), (by - ay) / (2 * half)
    nx, ny = -uy, ux                    # towards the bottom left
    pts = []
    for i in range(13):
        t = math.pi * i / 12
        pts += [mx + ux * half * math.cos(t) + nx * 0.45 * math.sin(t),
                my + uy * half * math.cos(t) + ny * 0.45 * math.sin(t)]
    feed = (mx - nx * 0.55, my - ny * 0.55)
    foot = (mx + nx * 0.45, my + ny * 0.45)
    return [("poly", "fg", pts),
            ("line", "fg", [mx, my, *feed]),
            ("line", "fg", [*foot, foot[0] + 0.2, 0.95]),
            ("line", "fg", [-0.6, 0.95, 0.6, 0.95])]


ICONS = {
    # A satellite: a body, two solar panels and an antenna.
    "Space Station": [("rect", "fg", [-0.3, -0.3, 0.3, 0.3]),
                      ("rect", "fg", [-1.0, -0.25, -0.5, 0.25]),
                      ("rect", "fg", [0.5, -0.25, 1.0, 0.25]),
                      ("line", "fg", [-0.5, 0.0, 0.5, 0.0]),
                      ("line", "fg", [0.0, -0.3, 0.0, -0.75]),
                      ("oval", "fg", [-0.15, -0.95, 0.15, -0.65])],
    # A ship standing on its tail, ready to go.
    "Spaceport": [("poly", "fg", [0.0, -1.0, 0.3, -0.3, 0.3, 0.4, 0.75, 0.85,
                                  -0.75, 0.85, -0.3, 0.4, -0.3, -0.3]),
                  ("oval", "bg", [-0.13, -0.4, 0.13, -0.14])],
    # A skyline.
    "City": [("rect", "fg", [-0.95, -0.2, -0.42, 0.9]),
             ("rect", "fg", [-0.32, -0.95, 0.18, 0.9]),
             ("rect", "fg", [0.28, -0.5, 0.95, 0.9])],
    # Two houses.
    "Village": [("poly", "fg", [-1.0, 0.9, -1.0, 0.1, -0.55, -0.35, -0.1, 0.1,
                                -0.1, 0.9]),
                ("poly", "fg", [0.1, 0.9, 0.1, -0.1, 0.55, -0.6, 1.0, -0.1,
                                1.0, 0.9])],
    # A block with windows.
    "Building": [("rect", "fg", [-0.6, -0.95, 0.6, 0.95])]
    + [("rect", "bg", [x, y, x + 0.25, y + 0.25])
       for x in (-0.4, 0.15) for y in (-0.7, -0.2, 0.3)],
    # A mast with a platform on top.
    "Tower": [("poly", "fg", [-0.22, -0.55, 0.22, -0.55, 0.55, 0.95,
                              -0.55, 0.95]),
              ("rect", "fg", [-0.5, -0.95, 0.5, -0.55])],
    # A dome on a base, its barrel up to the right.
    "Turret": [("rect", "fg", [-0.85, 0.45, 0.85, 0.95]),
               ("oval", "fg", [-0.55, -0.15, 0.55, 0.85]),
               ("line", "fg", [0.1, 0.2, 0.95, -0.7])],
    # A ring with an H in it.
    "Landing Pad": [("ring", "fg", [-0.95, -0.95, 0.95, 0.95]),
                    ("line", "fg", [-0.4, -0.5, -0.4, 0.5]),
                    ("line", "fg", [0.4, -0.5, 0.4, 0.5]),
                    ("line", "fg", [-0.4, 0.0, 0.4, 0.0])],
    # A ship nose down in the ground, cracked across, a wing sticking up.
    "Wreck": [("poly", "fg", [-0.95, -0.4, -0.7, -0.7, 0.75, 0.55, 0.5,
                              0.85]),
              ("poly", "fg", [-0.35, -0.2, -0.1, -0.95, 0.12, -0.85,
                              0.05, 0.05]),
              ("line", "bg", [-0.05, 0.35, 0.3, -0.05]),
              ("line", "fg", [-1.0, 0.92, 1.0, 0.92])],
    # Columns broken off at different heights.
    "Ruin": [("poly", "fg", [-0.85, 0.75, -0.85, -0.3, -0.65, -0.5, -0.5,
                             -0.25, -0.5, 0.75]),
             ("poly", "fg", [-0.18, 0.75, -0.18, -0.85, 0.0, -0.65, 0.18,
                             -0.9, 0.18, 0.75]),
             ("poly", "fg", [0.5, 0.75, 0.5, 0.15, 0.68, 0.3, 0.85, 0.05,
                             0.85, 0.75]),
             ("rect", "fg", [-1.0, 0.75, 1.0, 0.95])],
    # A peak with a flag on it.
    "Summit": [("poly", "fg", [-1.0, 0.85, -0.1, -0.4, 0.25, 0.05, 0.45,
                               -0.15, 1.0, 0.85]),
               ("line", "fg", [-0.1, -0.4, -0.1, -1.0]),
               ("poly", "fg", [-0.1, -1.0, 0.45, -0.82, -0.1, -0.64])],
    # Rings round a centre.
    "Jump Point": [("ring", "fg", [-0.95, -0.95, 0.95, 0.95]),
                   ("ring", "fg", [-0.55, -0.55, 0.55, 0.55]),
                   ("oval", "fg", [-0.2, -0.2, 0.2, 0.2])],
    # A pediment on columns.
    "Town Hall": [("poly", "fg", [-1.0, -0.35, 0.0, -0.95, 1.0, -0.35]),
                  ("rect", "fg", [-0.9, -0.3, 0.9, -0.12]),
                  ("rect", "fg", [-0.78, -0.05, -0.5, 0.65]),
                  ("rect", "fg", [-0.14, -0.05, 0.14, 0.65]),
                  ("rect", "fg", [0.5, -0.05, 0.78, 0.65]),
                  ("rect", "fg", [-1.0, 0.72, 1.0, 0.95])],
    "Radio Dish": _dish(),
    # A crate with an arrow up.
    "Cargo Elevator": [("line", "fg", [-0.75, -0.95, 0.75, -0.95, 0.75, 0.95,
                                       -0.75, 0.95, -0.75, -0.95]),
                       ("poly", "fg", [0.0, -0.7, 0.45, -0.15, 0.15, -0.15,
                                       0.15, 0.7, -0.15, 0.7, -0.15, -0.15,
                                       -0.45, -0.15])],
    # A shopping bag.
    "Shop": [("rect", "fg", [-0.75, -0.3, 0.75, 0.95]),
             ("line", "fg", [-0.35, -0.3, -0.35, -0.75, 0.35, -0.75, 0.35,
                             -0.3])],
    # Rock with a dark mouth in it.
    "Cave": [("poly", "fg", [-1.0, 0.9, -0.8, -0.1, -0.4, -0.7, 0.2, -0.85,
                             0.75, -0.35, 1.0, 0.9]),
             ("poly", "bg", [-0.45, 0.9, -0.35, 0.2, 0.0, -0.15, 0.35, 0.2,
                             0.45, 0.9])],
    # A cut gem.
    "Minable": [("poly", "fg", [-0.9, -0.3, -0.5, -0.8, 0.5, -0.8, 0.9, -0.3,
                                0.0, 0.95]),
                ("line", "bg", [-0.9, -0.3, 0.9, -0.3]),
                ("line", "bg", [-0.5, -0.8, -0.25, -0.3, 0.0, 0.95]),
                ("line", "bg", [0.5, -0.8, 0.25, -0.3, 0.0, 0.95])],
    # Two domed habs on the ground, a door in the bigger one.
    "Settlement": [("poly", "fg", [-1.0, 0.85, -1.0, 0.2, -0.75, -0.35,
                                   -0.35, -0.55, 0.05, -0.35, 0.3, 0.2,
                                   0.3, 0.85]),
                   ("poly", "fg", [0.4, 0.85, 0.4, 0.4, 0.55, 0.1, 0.8, 0.0,
                                   1.0, 0.25, 1.0, 0.85]),
                   ("rect", "bg", [-0.5, 0.35, -0.2, 0.85])],
    # A low bunker with a flag on a pole.
    "Forward Operating Base (FOB)": [
        ("poly", "fg", [-1.0, 0.9, -0.8, 0.35, 0.8, 0.35, 1.0, 0.9]),
        ("rect", "bg", [-0.5, 0.5, 0.5, 0.62]),
        ("line", "fg", [-0.45, 0.35, -0.45, -0.95]),
        ("poly", "fg", [-0.45, -0.95, 0.5, -0.7, -0.45, -0.45])],
    # A mast on legs with a ring on top, as an alignment beacon.
    "Planetary Alignment Facility (PAF)": [
        ("line", "fg", [-0.7, 0.95, 0.0, -0.3, 0.7, 0.95]),
        ("line", "fg", [-0.35, 0.35, 0.35, 0.35]),
        ("ring", "fg", [-0.4, -0.95, 0.4, -0.15]),
        ("oval", "fg", [-0.12, -0.67, 0.12, -0.43])],
    # A satellite firing a beam down to the ground.
    "Orbital Laser Platform (OLP)": [
        ("rect", "fg", [-0.35, -0.95, 0.35, -0.5]),
        ("rect", "fg", [-1.0, -0.85, -0.5, -0.6]),
        ("rect", "fg", [0.5, -0.85, 1.0, -0.6]),
        ("poly", "fg", [-0.12, -0.5, 0.12, -0.5, 0.35, 0.8, -0.35, 0.8]),
        ("line", "fg", [-0.8, 0.9, 0.8, 0.9])],
    # A half-buried dome with a dark doorway.
    "PAF Bunker": [("poly", "fg", [-1.0, 0.9, -0.85, 0.2, -0.5, -0.25,
                                   0.0, -0.4, 0.5, -0.25, 0.85, 0.2,
                                   1.0, 0.9]),
                   ("rect", "bg", [-0.25, 0.3, 0.25, 0.9])],
    # An oval track with a start line across it.
    "Ground Race Track": [("ring", "fg", [-1.0, -0.6, 1.0, 0.6]),
                          ("ring", "fg", [-0.6, -0.25, 0.6, 0.25]),
                          ("line", "fg", [0.0, 0.25, 0.0, 0.6])],
    # A burst where something came down.
    "Crash Site": [("poly", "fg", [
        v for k in range(16)
        for v in ((0.95 if k % 2 == 0 else 0.4)
                  * math.sin(math.pi * k / 8),
                  -(0.95 if k % 2 == 0 else 0.4)
                  * math.cos(math.pi * k / 8))])],
    # A ship hull side on, holed and dark.
    "Derelict Ship": [("poly", "fg", [-1.0, 0.15, -0.6, -0.35, 0.55, -0.35,
                                      1.0, 0.0, 0.55, 0.35, -0.6, 0.35]),
                      ("poly", "fg", [-0.35, -0.35, -0.1, -0.85, 0.2, -0.85,
                                      0.1, -0.35]),
                      ("oval", "bg", [-0.45, -0.15, -0.2, 0.1]),
                      ("oval", "bg", [0.15, -0.12, 0.35, 0.08])],
}

# The SUBs in use, as [name, icon]; the built-ins until `use` is called.
_current = [[name, name] for name in SUBTYPES]


def defaults():
    """The built-in SUBs as a fresh list of [name, icon]."""
    return [[name, name] for name in SUBTYPES]


def use(subs):
    """Put `subs` ([name, icon] pairs) in place as the SUBs in use."""
    _current[:] = [[n, i] for n, i in subs]


def current():
    """A copy of the SUBs in use, as [name, icon] pairs."""
    return [[n, i] for n, i in _current]


def names():
    """The SUB names in use, A to Z, as every list of them shows them."""
    return sorted((n for n, _ in _current), key=str.lower)


def icon_of(sub):
    """The ICONS key `sub` is drawn with, or "" for the plain pin."""
    icon = next((i for n, i in _current if n == sub), "")
    return icon if icon in ICONS else ""


def icon_items(sub, x, y, size, icon=None):
    """The icon for `sub` (or the ICONS key `icon`, if given) centred on
    (x, y), `size` px from centre to edge, as (kind, paint, screen coords);
    [] for no SUB or one without an icon."""
    key = icon_of(sub) if icon is None else icon
    return [(kind, paint, [v * size + (x if i % 2 == 0 else y)
                           for i, v in enumerate(coords)])
            for kind, paint, coords in ICONS.get(key, [])]


# --- managing the list (pure: each returns a new list, or raises) -------------
# ValueError messages are fit to show the user directly.

def _check_name(subs, name, but=None):
    name = " ".join(name.split())
    if not name:
        raise ValueError("a SUB needs a name")
    if any(n.lower() == name.lower() and n != but for n, _ in subs):
        raise ValueError(f"'{name}' is a SUB already")
    return name


def _check_icon(icon):
    if icon and icon not in ICONS:
        raise ValueError(f"no icon called '{icon}'")
    return icon or ""


def added(subs, name, icon=""):
    """`subs` with a new SUB at the end."""
    name = _check_name(subs, name)
    return [list(p) for p in subs] + [[name, _check_icon(icon)]]


def edited(subs, old, name, icon):
    """`subs` with SUB `old` renamed to `name` and drawn with `icon`, in the
    same place in the list."""
    if old not in [n for n, _ in subs]:
        raise ValueError(f"no SUB called '{old}'")
    name, icon = _check_name(subs, name, but=old), _check_icon(icon)
    return [[name, icon] if n == old else [n, i] for n, i in subs]


def removed(subs, name):
    """`subs` without SUB `name`."""
    if name not in [n for n, _ in subs]:
        raise ValueError(f"no SUB called '{name}'")
    return [[n, i] for n, i in subs if n != name]


def restored(subs):
    """`subs` with any built-in SUB that is missing added back, under its
    own name and icon; the rest, renamed or added, kept as they are."""
    have = {n.lower() for n, _ in subs}
    return [list(p) for p in subs] + [[n, n] for n in SUBTYPES
                                      if n.lower() not in have]
