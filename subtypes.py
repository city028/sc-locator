"""What kind of place a waypoint is (its SUB), and the icon each kind has.

A waypoint's type says what it is for (POI or survey); its SUB says what is
there: a city, a cave, a summit. The SUB picks the icon the star map and the
height map draw for it. An empty SUB, or one not in ICONS, keeps the plain
pin.

To add a kind: add its name to SUBTYPES and its drawing to ICONS. Nothing
else needs to change.

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
}


def icon_items(sub, x, y, size):
    """The icon for `sub` centred on (x, y), `size` px from centre to edge,
    as (kind, paint, screen coords); [] for no SUB or one without an icon."""
    return [(kind, paint, [v * size + (x if i % 2 == 0 else y)
                           for i, v in enumerate(coords)])
            for kind, paint, coords in ICONS.get(sub, [])]
