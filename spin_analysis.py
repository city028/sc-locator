"""What a ship parked on a body can learn about it. Pure maths: no I/O, no Tk.

Parked on the ground you turn with the body, so the planet-frame position holds
still while the **system-frame** position sweeps a circle about the body's spin
axis, at exactly the body's rotation rate. Sampling it is therefore a direct
measurement of the spin, and one circle fixes:

  - the rotation rate, hence the period, and its sense
  - the spin axis, from the circle's plane
  - the body's centre, from the circle plus the planet-frame position taken at
    the same instant -- which carries the height of the ship above the circle
  - and with the centre, where the star sits in the body's sky

Assumptions, each stated in the report rather than buried here:
  - the star is at the system frame's origin (confirmed)
  - the planet frame's +z is the spin axis, and the body turns counter-clockwise
    about it (measured true for Crusader and Hurston). A mirror-image body
    fits the same circle equally well, so a single landing cannot test it. It
    decides the side of the equator the centre is on, which barely moves the
    sun (0.06 s of sunset on Hurston); the centre it implies is checked against
    the system plane instead.
  - planets do not orbit, so the star's direction is fixed in the system frame
    and a solar day is exactly one rotation.
"""
import math
import random

# Two readings of the same quantity this far apart are the same reading.
PARKED_TOLERANCE_M = 3.0

# A fitted circle whose radius disagrees with the planet-frame x, y by more than
# this is not a spin about the body's own axis.
RADIUS_AGREEMENT = 0.02

# Motion below this over the whole run is the system frame's own noise, which
# is 0.1 m of display rounding.
STILL_M = 1.0

STRAYS_TO_RESTART = 3           # off-spot readings in a row that mean "moved"

MIN_SAMPLES = 10
MIN_SECONDS = 30.0
MAX_SECONDS = 300.0
TARGET_SIGMA = 0.01             # relative error on the rate worth stopping at
NOT_ROTATING_AFTER = 120.0      # seconds without measurable motion

# How far off the system plane a centre may sit before the spin-sense
# assumption is doubted. Crusader's is within about a kilometre.
PLANE_TOLERANCE_M = 100_000.0


# --- 3-vectors -------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    return (a[0] / n, a[1] / n, a[2] / n)


def _eigh3(m):
    """Eigenvalues (descending) and unit eigenvectors of a symmetric 3x3.

    Cyclic Jacobi. Small enough to write out rather than take a dependency for.
    """
    a = [list(r) for r in m]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(60):
        off = abs(a[0][1]) + abs(a[0][2]) + abs(a[1][2])
        if off < 1e-300:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if abs(a[p][q]) < 1e-300:
                continue
            theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
            t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
            c = 1.0 / math.sqrt(t * t + 1.0)
            s = t * c
            for k in range(3):
                akp, akq = a[k][p], a[k][q]
                a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
            for k in range(3):
                apk, aqk = a[p][k], a[q][k]
                a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
            for k in range(3):
                vkp, vkq = v[k][p], v[k][q]
                v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    order = sorted(range(3), key=lambda i: -a[i][i])
    return ([a[i][i] for i in order],
            [(v[0][i], v[1][i], v[2][i]) for i in order])


def _solve3(m, b):
    """Solve a 3x3 linear system by elimination, or None if singular."""
    a = [list(m[i]) + [b[i]] for i in range(3)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-300:
            return None
        a[col], a[piv] = a[piv], a[col]
        for r in range(col + 1, 3):
            f = a[r][col] / a[col][col]
            for c in range(col, 4):
                a[r][c] -= f * a[col][c]
    x = [0.0] * 3
    for r in (2, 1, 0):
        x[r] = (a[r][3] - sum(a[r][c] * x[c] for c in range(r + 1, 3))) / a[r][r]
    return tuple(x)


# --- the fit ---------------------------------------------------------------

def fit_circle(points):
    """Circle through 3-D points: (centre, unit normal, radius, spread) or None.

    `spread` is the extent of the points out of the circle's plane against the
    extent along the arc -- how far the fit is from being a straight line.
    """
    n = len(points)
    if n < 3:
        return None
    mean = _mul(tuple(sum(p[i] for p in points) for i in range(3)), 1.0 / n)
    rel = [_sub(p, mean) for p in points]
    cov = [[sum(p[i] * p[j] for p in rel) for j in range(3)] for i in range(3)]
    vals, vecs = _eigh3(cov)
    e1, normal = vecs[0], vecs[2]
    e2 = _cross(normal, e1)
    xy = [(_dot(p, e1), _dot(p, e2)) for p in rel]
    sx = sum(x for x, _ in xy)
    sy = sum(y for _, y in xy)
    sxx = sum(x * x for x, _ in xy)
    syy = sum(y * y for _, y in xy)
    sxy = sum(x * y for x, y in xy)
    sz = [x * x + y * y for x, y in xy]
    rhs = (-sum(x * z for (x, _), z in zip(xy, sz)),
           -sum(y * z for (_, y), z in zip(xy, sz)),
           -sum(sz))
    sol = _solve3([[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, float(n)]], rhs)
    if sol is None:
        return None
    d, e, f = sol
    a, b = -d / 2.0, -e / 2.0
    r2 = a * a + b * b - f
    if r2 <= 0:
        return None
    centre = _add(mean, _add(_mul(e1, a), _mul(e2, b)))
    spread = math.sqrt(max(vals[1], 0.0) / vals[0]) if vals[0] > 0 else 0.0
    return centre, normal, math.sqrt(r2), spread, (e1, e2, (a, b))


def _unwrap(angles):
    out = [angles[0]]
    for a in angles[1:]:
        prev = out[-1]
        while a - prev > math.pi:
            a -= 2 * math.pi
        while a - prev < -math.pi:
            a += 2 * math.pi
        out.append(a)
    return out


def _regress(ts, ys):
    """Slope, intercept and the slope's standard error of a straight line."""
    n = len(ts)
    tm, ym = sum(ts) / n, sum(ys) / n
    stt = sum((t - tm) ** 2 for t in ts)
    if stt == 0:
        return None
    slope = sum((t - tm) * (y - ym) for t, y in zip(ts, ys)) / stt
    icpt = ym - slope * tm
    res = [y - (icpt + slope * t) for t, y in zip(ts, ys)]
    rms = math.sqrt(sum(r * r for r in res) / max(n - 2, 1))
    return slope, icpt, rms / math.sqrt(stt)


def _fit_spin_once(samples, local):
    """The spin of a body from `samples`, [(t, system position), ...].

    `local` is the planet-frame position the ship held throughout. Returns a
    dict, or None while the arc is too short to say anything. Keys:

      omega        rad/s, positive about `axis`
      sigma        relative standard error of omega
      axis         unit vector, system frame, +z of the body
      centre       the body's centre, system frame
      rho          radius of the circle the ship sweeps
      rho_local    the same from the planet-frame x, y -- the independent check
      arc_deg      how far the body turned over the samples
    """
    if len(samples) < 4:
        return None
    ts = [t for t, _ in samples]
    fit = fit_circle([p for _, p in samples])
    if fit is None:
        return None
    centre, normal, rho, spread, (e1, e2, (a, b)) = fit
    mean = _mul(tuple(sum(p[i] for _, p in samples) for i in range(3)),
                1.0 / len(samples))
    angles = _unwrap([math.atan2(_dot(_sub(p, mean), e2) - b,
                                 _dot(_sub(p, mean), e1) - a)
                      for _, p in samples])
    line = _regress(ts, angles)
    if line is None:
        return None
    slope, icpt, sigma = line
    # How far each reading sits from the fitted circle, across it and along it.
    # Along it includes the timestamp's own jitter, so it is the looser of the
    # two; both are used only to find readings that are plainly wrong.
    off, along = [], []
    for (t, p), ang in zip(samples, angles):
        rel = _sub(p, mean)
        x, y, z = _dot(rel, e1) - a, _dot(rel, e2) - b, _dot(rel, normal)
        off.append(math.hypot(math.hypot(x, y) - rho, z))
        along.append(abs(ang - (icpt + slope * t)) * rho)
    if slope < 0:                       # point the axis so the spin is positive
        normal, slope = _mul(normal, -1.0), -slope
    if slope == 0:
        return None

    lx, ly, lz = local
    rho_local = math.hypot(lx, ly)
    r_local = _norm(local)
    # Height of the centre below the circle's plane. With the planet-frame z as
    # the spin axis this is just lz; from the magnitudes alone it is the same
    # number without trusting that, and it needs the circle radius to be right.
    drop = math.copysign(math.sqrt(max(r_local ** 2 - rho ** 2, 0.0)), lz)
    return {
        "omega": slope,
        "sigma": sigma / slope,
        "axis": normal,
        "centre": _sub(centre, _mul(normal, drop)),
        "rho": rho,
        "rho_local": rho_local,
        "axis_agrees": abs(rho - rho_local) <= RADIUS_AGREEMENT * max(rho_local, 1.0),
        "arc_deg": math.degrees(abs(angles[-1] - angles[0])),
        "spread": spread,
        "samples": len(samples),
        "duration_s": ts[-1] - ts[0],
        "_off": off,
        "_along": along,
    }


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _circle_through(p1, p2, p3):
    """(centre, unit normal, radius) of the circle through three points."""
    a, b = _sub(p2, p1), _sub(p3, p1)
    n = _cross(a, b)
    n2 = _dot(n, n)
    if n2 < 1e-9:
        return None
    num = _cross(_sub(_mul(b, _dot(a, a)), _mul(a, _dot(b, b))), n)
    off = _mul(num, 1.0 / (2.0 * n2))
    return _add(p1, off), _mul(n, 1.0 / math.sqrt(n2)), _norm(off)


def _inliers(samples, tolerance=3.0, tries=80):
    """The samples that lie on the circle most of them agree on.

    A least-squares circle is wrecked by one reading a kilometre out, so it
    cannot be asked which readings are wrong. Instead: circles through triples
    spread across the run, keeping the one the most readings sit on. A fixed
    seed, so the same data always gives the same answer.
    """
    n = len(samples)
    if n < 9:
        return list(samples)
    rnd = random.Random(20261005)
    pts = [p for _, p in samples]
    best = list(range(n))
    best_n = -1
    for _ in range(tries):
        i = rnd.randrange(0, n // 3)
        j = rnd.randrange(n // 3, 2 * n // 3)
        k = rnd.randrange(2 * n // 3, n)
        circ = _circle_through(pts[i], pts[j], pts[k])
        if circ is None:
            continue
        centre, normal, rho = circ
        hits = []
        for idx, p in enumerate(pts):
            v = _sub(p, centre)
            z = _dot(v, normal)
            inplane = _norm(_sub(v, _mul(normal, z)))
            if math.hypot(inplane - rho, z) <= tolerance:
                hits.append(idx)
        if len(hits) > best_n:
            best, best_n = hits, len(hits)
    return [samples[i] for i in best] if best_n >= max(4, n // 2) else list(samples)


def fit_spin(samples, local):
    """_fit_spin_once, with the plainly wrong readings thrown out first.

    The settling that admits a system position only refuses jumps over 100 km,
    so a digit misread in the middle of the number -- a kilometre, ten metres --
    gets through, and one of those drags a least-squares circle visibly. A
    reading is dropped when it is far from the circle by the spread of the rest
    (with a floor, since the HUD rounds to 0.1 m and the timestamps jitter),
    and the fit is run again without it. Once: a fit that needed more than that
    is not a fit.
    """
    total = len(samples)
    samples = _inliers(samples)
    fit = _fit_spin_once(samples, local)
    if fit is None:
        return None
    off, along = fit.pop("_off"), fit.pop("_along")
    # Floors: a few metres across the circle; along it, a second and a half of
    # timestamp jitter at the speed the ship is going.
    speed = fit["omega"] * fit["rho"]
    off_limit = max(3.0, 8.0 * 1.4826 * _median(off))
    along_limit = max(3.0, 1.5 * speed, 8.0 * 1.4826 * _median(along))
    keep = [s for s, o, al in zip(samples, off, along)
            if o <= off_limit and al <= along_limit]
    fit["rejected"] = total - len(samples)
    if len(keep) < len(samples) and len(keep) >= 4:
        again = _fit_spin_once(keep, local)
        if again is not None:
            again.pop("_off"), again.pop("_along")
            again["rejected"] = total - len(keep)
            return again
    return fit


def sun_geometry(centre, axis, star=(0.0, 0.0, 0.0)):
    """(declination deg, distance to the star m) seen from the body's centre."""
    to_star = _sub(star, centre)
    dist = _norm(to_star)
    return (math.degrees(math.asin(max(-1.0, min(1.0, _dot(_unit(to_star), axis))))),
            dist)


def subsolar_longitude(ship_lon, ship_sys, centre, axis, star=(0.0, 0.0, 0.0)):
    """Longitude (deg) the star is directly over, when the ship is at ship_lon.

    Longitude is taken to grow counter-clockwise about the axis, which is how
    the planet frame's atan2(y, x) behaves with +z as the axis.
    """
    ref = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = _unit(_cross(_cross(axis, ref), axis))
    e2 = _cross(axis, e1)

    def az(v):
        return math.atan2(_dot(v, e2), _dot(v, e1))

    shift = az(_sub(star, centre)) - az(_sub(ship_sys, centre))
    return (ship_lon + math.degrees(shift) + 180.0) % 360.0 - 180.0


# --- re-checking the spin from live readings --------------------------------
#
# An analysis measures the rate over half a minute, and the small error in it
# adds up: nine days after Crusader's, its planet frame was 51.5 deg short
# of where the record said (2026-10-14), which put the star map's cross
# 16.6 Gm out and the sun times some 44 minutes. Every frame that carries both
# positions shows where the star really is, so the anchor can be kept current.

# How closely the two positions must agree before one frame is used: on the
# distance from the centre and the height along the axis, which the spin does
# not change. Parked 19 Gm from Crusader they agreed to 1.5 km and 1.9 km; a
# Root row frozen by a jump, or a misread digit, is off by far more.
SPIN_MATCH_M = 10_000.0
SPIN_MATCH_FRACTION = 1e-6      # of the distance from the centre, if larger

# Nor closer to the spin axis than this. The angle is measured around the
# axis, and the centre is only known to some hundreds of metres (Nyx I's fit:
# 370 m), so near a pole it can come out anywhere: 0.49 km from Nyx I's axis
# it re-anchored by -42.8 deg (2026-10-16). At 1,000 km, 370 m is 0.02 deg.
SPIN_MIN_AXIS_M = 1_000_000.0

# The rate is corrected from the old anchor only when that is this far back --
# closer, the timing of a frame is a noticeable part of the drift -- and only
# by up to this much of itself, beyond which whole turns may have been lost.
SPIN_RATE_MIN_GAP_S = 3600.0
SPIN_RATE_MAX_CHANGE = 0.01


def wrap180(deg):
    return (deg + 180.0) % 360.0 - 180.0


def subsolar_at(rec, t):
    """Longitude (deg) the star is over at t, by the record's anchor."""
    return rec["subsolar_lon_ref"] - rec["omega_deg_s"] * (t - rec["t_ref"])


def measured_subsolar(rec, sys_pos, pos):
    """Longitude (deg) the star is over, from one frame's system-frame and
    planet-frame positions on the body analysed as `rec`. None when the body
    has no centre and spin, or when the two positions disagree on what the
    spin cannot change."""
    if (rec or {}).get("kind") != "rotating" or not rec.get("centre"):
        return None
    centre, axis = rec["centre"], rec.get("axis") or (0.0, 0.0, 1.0)
    v = _sub(sys_pos, centre)
    r = _norm(pos)
    if math.hypot(pos[0], pos[1]) < SPIN_MIN_AXIS_M:
        return None
    tol = max(SPIN_MATCH_M, SPIN_MATCH_FRACTION * r)
    if abs(_norm(v) - r) > tol or abs(_dot(v, axis) - pos[2]) > tol:
        return None
    lon = math.degrees(math.atan2(pos[1], pos[0]))
    return subsolar_longitude(lon, sys_pos, centre, axis)


def reanchor(rec, sub, t):
    """(record anchored on subsolar longitude `sub` measured at t, degrees
    the old anchor was out). The rate is corrected from the old anchor too,
    when that is SPIN_RATE_MIN_GAP_S back and the change is believable."""
    err = wrap180(subsolar_at(rec, t) - sub)
    new = dict(rec, subsolar_lon_ref=wrap180(sub), t_ref=t)
    gap = t - rec["t_ref"]
    if gap >= SPIN_RATE_MIN_GAP_S:
        omega = rec["omega_deg_s"] + err / gap
        if abs(omega / rec["omega_deg_s"] - 1.0) <= SPIN_RATE_MAX_CHANGE:
            new["omega_deg_s"] = omega
            new["period_s"] = 360.0 / abs(omega)
    return new, err


# --- the sun at a place, from the stored anchor -----------------------------

def sun_at(lat, lon, t, rec):
    """Where the star is for a place on a body, from its record.

    Needs only the rotation rate, the sun's declination and one phase anchor
    -- the longitude the star was over at `t_ref` -- which is why a body has to
    be analysed once and never again. Returns a dict: elevation in degrees, and
    seconds to the next sunrise, sunset and local noon (None where the sun
    never does that at this latitude).
    """
    omega = math.radians(rec["omega_deg_s"])
    decl = math.radians(rec["declination_deg"])
    phi = math.radians(lat)
    sub = rec["subsolar_lon_ref"] - rec["omega_deg_s"] * (t - rec["t_ref"])
    hour = math.radians(lon - sub)                      # hour angle, east positive
    sin_e = (math.sin(phi) * math.sin(decl)
             + math.cos(phi) * math.cos(decl) * math.cos(hour))
    out = {"elevation": math.degrees(math.asin(max(-1.0, min(1.0, sin_e)))),
           "sunrise_in": None, "sunset_in": None, "noon_in": None,
           "day_length_s": None, "polar": None}
    two_pi = 2 * math.pi
    x = -math.tan(phi) * math.tan(decl)
    if x >= 1.0:
        out["polar"] = "night"
        return out
    if x <= -1.0:
        out["polar"] = "day"
        return out
    half = math.acos(x)
    out["day_length_s"] = 2 * half / omega
    # The hour angle grows at omega: noon at 0, sunset at +half, sunrise at -half.
    out["noon_in"] = ((-hour) % two_pi) / omega
    out["sunset_in"] = ((half - hour) % two_pi) / omega
    out["sunrise_in"] = ((-half - hour) % two_pi) / omega
    return out


# --- a session -------------------------------------------------------------

class SpinSession:
    """Samples gathered while parked, and the decision of when they suffice."""

    def __init__(self, planet, started):
        self.planet = planet
        self.started = started
        self.samples = []          # (t, system position)
        self.locals = []           # planet-frame positions
        self.restarts = 0
        self.strays = 0            # readings in a row at one new spot
        self.stray_at = None

    def add(self, t, sys_pos, local):
        """Take a sample. Returns False if the ship moved and it was reset."""
        if self.locals and math.dist(local, self.locals[0]) > PARKED_TOLERANCE_M:
            # A misread digit lands somewhere different every time; a ship
            # that has moved stays at its new spot. Only readings that agree
            # with each other count as a move.
            if self.stray_at and math.dist(local, self.stray_at) <= PARKED_TOLERANCE_M:
                self.strays += 1
            else:
                self.strays, self.stray_at = 1, local
            if self.strays < STRAYS_TO_RESTART:
                return True
            self.samples, self.locals = [], []
            self.started = t
            self.restarts += 1
            self.strays, self.stray_at = 0, None
            return False
        self.strays, self.stray_at = 0, None
        self.samples.append((t, sys_pos))
        self.locals.append(local)
        return True

    def local(self):
        n = len(self.locals)
        return tuple(sum(p[i] for p in self.locals) / n for i in range(3))

    def fit(self):
        return fit_spin(self.samples, self.local()) if self.locals else None

    def elapsed(self, now):
        """Seconds since the first sample -- the span the result is judged on,
        so the decision to stop and the decision to keep cannot disagree."""
        return now - self.samples[0][0] if self.samples else 0.0

    def still(self):
        """Has the ship's system position not moved measurably at all?"""
        if not self.samples:
            return True
        first = self.samples[0][1]
        return all(math.dist(first, p) <= STILL_M for _, p in self.samples)

    def verdict(self, now):
        """(done, why) -- done when the result is as good as it will get."""
        n, elapsed = len(self.samples), self.elapsed(now)
        if elapsed >= NOT_ROTATING_AFTER and n >= MIN_SAMPLES and self.still():
            return True, "still"
        if elapsed >= MAX_SECONDS:
            return True, "time"
        span = self.samples[-1][0] - self.samples[0][0] if n else 0.0
        if n >= MIN_SAMPLES and span >= MIN_SECONDS:
            fit = self.fit()
            if fit and fit["sigma"] <= TARGET_SIGMA:
                return True, "precise"
        return False, ""

    def progress(self, now):
        """One line saying how it is getting on."""
        n, elapsed = len(self.samples), self.elapsed(now)
        fit = self.fit()
        moved = f" (restarted {self.restarts}x: ship moved)" if self.restarts else ""
        if fit is None:
            return (f"analysing: {n} samples, {elapsed:.0f} s - waiting "
                    f"for motion{moved}")
        return (f"analysing: {elapsed:.0f} s, {fit['arc_deg']:.2f} deg turned, "
                f"rate +/-{fit['sigma'] * 100:.1f}% (aiming for "
                f"{TARGET_SIGMA * 100:.0f}%){moved}")


# --- a place with no ground ---------------------------------------------------
# A station, a Lagrange point or a jump point has no surface to turn with, so
# there is no circle to fit. What can be said is whether the system position
# stays put -- and, inside a body's zone, whether the position over the body
# does. An orbital station can hold still over its body (Grim HEX over Yela,
# Everus Harbor over Hurston) and so sweep through the system frame as the
# body turns: matched on its system position it is found again only for the
# minute or so it takes to leave the matching range.

SPACE_SECONDS = 60.0
SPACE_MIN_SAMPLES = 6
SPACE_MATCH_M = 20_000.0        # a record this near is the same place
SPACE_STILL_SHARE = 0.7         # of readings within STILL_M of the median


def _median(values):
    v = sorted(values)
    n = len(v)
    return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])


def _median_pos(points):
    return tuple(_median([p[i] for p in points]) for i in range(3))


def _still(points):
    """Do most points sit within STILL_M of their median?"""
    if not points:
        return True
    mid = _median_pos(points)
    near = sum(1 for p in points if math.dist(mid, p) <= STILL_M)
    return near >= SPACE_STILL_SHARE * len(points)


class SpaceSession:
    """Sampling a parked ship's system position where there is no body."""

    def __init__(self, started):
        self.started = started
        self.samples = []          # (t, system position)
        self.over = []             # (body, position over it) when in a zone

    def add(self, t, sys_pos):
        self.samples.append((t, sys_pos))
        return True

    def add_over(self, body, pos):
        self.over.append((body, pos))

    def held_over(self):
        """(body, position) if the place holds still over a body, else None.

        Far out in a zone the position over the body sweeps as the body turns,
        so it fails the stillness test and nothing is claimed.
        """
        if not self.over:
            return None
        names = [b for b, _ in self.over]
        body = max(set(names), key=names.count)
        pts = [p for b, p in self.over if b == body]
        if len(pts) < 4 or not _still(pts):
            return None
        return body, _median_pos(pts)

    def elapsed(self, now):
        return now - self.samples[0][0] if self.samples else 0.0

    def centre(self):
        """The median position: one misread frame does not move it."""
        return _median_pos([p for _, p in self.samples])

    def still(self):
        """Do most readings sit within the system frame's noise of the median?

        A share and not all of them: the position reader misses a digit now
        and then, and one wild reading is not a ship that moved.
        """
        return _still([p for _, p in self.samples])

    def verdict(self, now):
        n, elapsed = len(self.samples), self.elapsed(now)
        if n >= SPACE_MIN_SAMPLES and elapsed >= SPACE_SECONDS:
            return True, "time"
        if elapsed >= MAX_SECONDS:
            return True, "time"
        return False, ""

    def progress(self, now):
        n, elapsed = len(self.samples), self.elapsed(now)
        return (f"analysing: {n} samples, {elapsed:.0f} s of "
                f"{SPACE_SECONDS:.0f} - hold still")

    def drift(self):
        """(speed in m/s, unit direction) from the first third to the last."""
        n = len(self.samples)
        third = max(n // 3, 1)
        head, tail = self.samples[:third], self.samples[-third:]
        a, b = _median_pos([p for _, p in head]), _median_pos([p for _, p in tail])
        dt = _median([t for t, _ in tail]) - _median([t for t, _ in head])
        gap = math.dist(a, b)
        if dt <= 0 or gap == 0:
            return 0.0, (0.0, 0.0, 0.0)
        return gap / dt, _unit(_sub(b, a))


def nearest_space(bodies, pos, body=None, body_pos=None):
    """(key, record) of the stored place nearest, if within matching range.

    `pos` is the system position; `body` and `body_pos` the body in range and
    the position over it, if any. A place that holds still over a body is
    matched there and only there: its system position is just where it was
    while the analysis ran.
    """
    best = None
    for key, rec in bodies.items():
        if not isinstance(rec, dict) or rec.get("scope") != "space":
            continue
        if rec.get("site_over") is not None:
            if body_pos is None or body != rec.get("over"):
                continue
            d = math.dist(rec["site_over"], body_pos)
        elif pos is None:
            continue
        else:
            d = math.dist(rec["site_sys"], pos)
        if d <= SPACE_MATCH_M and (best is None or d < best[0]):
            best = (d, key, rec)
    return (best[1], best[2]) if best else (None, None)


def space_key(pos):
    """A name for a new place: its position in km, so the same place repeats."""
    return "space " + ",".join(f"{c / 1000.0:.0f}" for c in pos) + " km"


def make_space_record(session, system, stamp):
    """What gets stored for a place with no ground, or None for a short run."""
    n = len(session.samples)
    duration = session.samples[-1][0] - session.samples[0][0] if n else 0.0
    if n < 4 or duration < MIN_SECONDS:
        return None
    rec = {"scope": "space", "system": system, "analysed": stamp,
           "site_sys": list(session.centre()), "samples": n,
           "duration_s": duration}
    held = session.held_over()
    if held is not None:
        rec["over"], rec["site_over"] = held[0], list(held[1])
    if session.still():
        rec["kind"] = "fixed"
        return rec
    speed, direction = session.drift()
    rec.update(kind="moving", speed_mps=speed, direction=list(direction))
    return rec


# --- the record and the report ---------------------------------------------

# Past this the rate is too uncertain to call a measurement.
USABLE_SIGMA = 0.5


def classify(session, fit):
    """rotating, static, moving or inconclusive, for what was gathered."""
    if len(session.samples) < 4 or (session.samples[-1][0]
                                    - session.samples[0][0]) < MIN_SECONDS:
        return "inconclusive"
    if session.still():
        return "static"
    if fit is None:
        return "inconclusive"
    if not fit["axis_agrees"]:
        return "moving"
    if fit["sigma"] > USABLE_SIGMA:
        return "inconclusive"
    return "rotating"


def make_record(session, fit, planet, system, radius, stamp, star=(0.0, 0.0, 0.0)):
    """What gets stored for a body, or None when the run said nothing.

    Everything needed to place the sun anywhere on the body at any time is
    here -- the rate, the declination and one phase anchor -- so the analysis
    is done once per body and never repeated.
    """
    kind = classify(session, fit)
    if kind == "inconclusive":
        return None
    lx, ly, lz = session.local()
    t_last, sys_last = session.samples[-1]
    site_lat = math.degrees(math.asin(lz / _norm((lx, ly, lz))))
    site_lon = math.degrees(math.atan2(ly, lx))
    rec = {
        "planet": planet, "system": system, "radius_m": radius,
        "analysed": stamp, "kind": kind,
        "site": {"lat": site_lat, "lon": site_lon},
        "samples": len(session.samples),
        "duration_s": session.samples[-1][0] - session.samples[0][0],
    }
    if kind == "static":
        rho = math.hypot(lx, ly)
        rec["period_longer_than_s"] = (2 * math.pi * max(rho, 1.0)
                                       * rec["duration_s"] / STILL_M)
        return rec
    rec.update(rho_m=fit["rho"], rho_local_m=fit["rho_local"])
    if kind == "moving":
        return rec
    centre, axis = fit["centre"], fit["axis"]
    decl, star_dist = sun_geometry(centre, axis, star)
    omega_deg = math.degrees(fit["omega"])
    rec.update({
        "omega_deg_s": omega_deg,
        "period_s": 2 * math.pi / fit["omega"],
        "sigma": fit["sigma"],
        "arc_deg": fit["arc_deg"],
        "axis": list(axis),
        "axis_tilt_deg": math.degrees(math.acos(max(-1.0, min(1.0, axis[2])))),
        "centre": list(centre),
        "star_distance_m": star_dist,
        "declination_deg": decl,
        "t_ref": t_last,
        "subsolar_lon_ref": subsolar_longitude(site_lon, sys_last, centre,
                                               axis, star),
    })
    return rec


def fmt_duration(s):
    """Seconds as 2h 30m, 12m 05s or 40s -- whichever reads at that size."""
    s = int(round(s))
    if s >= 86400:
        return f"{s // 86400}d {s % 86400 // 3600}h"
    if s >= 3600:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    if s >= 60:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s}s"


def _km(m):
    return f"{m / 1000.0:,.1f} km"


def _far(m):
    """A length in the unit that keeps it short: km, Mm or Gm."""
    if m >= 1e9:
        return f"{m / 1e9:.2f} Gm"
    if m >= 1e7:
        return f"{m / 1e6:.2f} Mm"
    return _km(m)


def _sun_line(sun):
    """What the sun is doing, in the order you would ask."""
    if sun["polar"]:
        return "sun never rises here" if sun["polar"] == "night" \
            else "sun never sets here"
    parts = []
    for key, label in (("sunset_in", "sets"), ("sunrise_in", "rises"),
                       ("noon_in", "noon")):
        parts.append(f"{label} {fmt_duration(sun[key])}")
    return ", ".join(parts)


def report_lines(rec, lat=None, lon=None, now=None):
    """The analysis as lines for the panel.

    `lat`, `lon` and `now` place the live sun figures; without them the site
    the analysis was taken at is used.
    """
    if rec is None:
        return ["no analysis for this place yet",
                "land or park, hold still, press ANALYSE",
                "look at a dark area while it runs"]
    if rec.get("scope") == "space":
        x, y, z = rec["site_sys"]
        lines = [f"{rec.get('label') or 'place in space'}  "
                 f"({rec.get('system', '?')})  {rec['analysed']}",
                 f"At       x {x / 1e9:+.3f}  y {y / 1e9:+.3f}  "
                 f"z {z / 1e9:+.4f} Gm"]
        if rec["kind"] == "fixed":
            lines += [f"Result   does not move in the system frame",
                      f"         over {fmt_duration(rec['duration_s'])}: a "
                      "fixed point"]
        elif rec.get("over"):
            lines += [f"Result   moving {rec['speed_mps']:.2f} m/s in the "
                      "system frame,",
                      f"         but fixed over {rec['over']}: it turns with it"]
        else:
            lines += [f"Result   moving {rec['speed_mps']:.2f} m/s over "
                      f"{fmt_duration(rec['duration_s'])}",
                      "         not a fixed point (orbiting, or drifting?)"]
        return lines
    lines = [f"{rec['planet']}  ({rec.get('system', '?')})  {rec['analysed']}",
             f"Radius   {_km(rec['radius_m'])}   diameter "
             f"{_km(2 * rec['radius_m'])}"]
    if rec.get("label"):
        lines.append(f"Label    {rec['label']}")
    kind = rec["kind"]
    if kind == "static":
        lines += [f"Spin     none seen in {fmt_duration(rec['duration_s'])}",
                  f"         period longer than "
                  f"{fmt_duration(rec['period_longer_than_s'])}",
                  "Sun      a body that does not turn keeps one",
                  "         face to the star: permanent day on one",
                  "         side, night on the other"]
        return lines
    if kind == "moving":
        lines += ["Spin     not a plain turn about its own axis",
                  f"         path {_far(rec['rho_m'])}, "
                  f"a turn would give {_far(rec['rho_local_m'])}",
                  "         probably moving as well (an orbit?)"]
        return lines
    omega = rec["omega_deg_s"]
    lines += [f"Spin     {fmt_duration(rec['period_s'])} per turn"
              f"  ({omega:.5f} deg/s)",
              f"         +/-{rec['sigma'] * 100:.1f}% from "
              f"{fmt_duration(rec['duration_s'])}, {rec['arc_deg']:.1f} deg",
              f"Axis     {rec['axis_tilt_deg']:.1f} deg off the system z",
              f"Star     {rec['star_distance_m'] / 1e9:.2f} Gm, "
              f"declination {rec['declination_deg']:+.1f} deg"]
    lat = rec["site"]["lat"] if lat is None else lat
    lon = rec["site"]["lon"] if lon is None else lon
    now = rec["t_ref"] if now is None else now
    sun = sun_at(lat, lon, now, rec)
    lines.append(f"Here     lat {lat:+.1f}  lon {lon:+.1f}")
    if sun["day_length_s"] is not None:
        lines.append(f"         daylight {fmt_duration(sun['day_length_s'])}"
                     f" of {fmt_duration(rec['period_s'])}")
    lines.append(f"Sun      {sun['elevation']:+.0f} deg, "
                 f"{'up' if sun['elevation'] > 0 else 'down'}")
    lines.append("         " + _sun_line(sun))
    notes = []
    if abs(rec["centre"][2]) > PLANE_TOLERANCE_M:
        notes.append(f"centre {_km(abs(rec['centre'][2]))} off the system plane"
                     " - spin sense unconfirmed")
    if notes:
        lines.append("Check    " + notes[0])
    return lines
