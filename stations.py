"""Space stations from the UEX Corp API, to put a name and services on a place.

An analysis can say that a place sits still or turns with its body; it cannot
say what the place is. UEX lists every station with its body, faction and
services, but no coordinates, so a station is linked to a report by the player
picking it, not by position. What was picked is copied into the report, so a
linked report reads the same offline and after UEX changes.

UEX is a community trading site: the data is entered by its users, not mined
from the game files.
"""
import json
import re
import time
import urllib.request

UEX_URL = "https://api.uexcorp.space/2.0/space_stations"
# Both community APIs refuse Python's default User-Agent with a 403.
USER_AGENT = "sc-locator/1.0"
MAX_AGE_S = 7 * 24 * 3600       # stations change with patches, not daily

# UEX flag -> word in the report, in the order shown.
SERVICES = [("has_refinery", "refinery"), ("has_repair", "repair"),
            ("has_refuel", "refuel"), ("has_clinic", "clinic"),
            ("has_shops", "shops"), ("has_trade_terminal", "trade"),
            ("has_habitation", "habs"), ("has_cargo_center", "cargo")]


def fetch(timeout=30):
    """Every station UEX lists, as its raw records. Raises on any failure."""
    req = urllib.request.Request(UEX_URL, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.load(resp)
    if body.get("status") != "ok" or not isinstance(body.get("data"), list):
        raise ValueError(f"unexpected reply from UEX: {body.get('status')!r}")
    return body["data"]


def stale(cache, now=None):
    """Does the cached list need fetching again?"""
    now = time.time() if now is None else now
    return not cache or now - cache.get("fetched", 0) > MAX_AGE_S


def _canon(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def choices(stations, system, body, resolve=lambda name: None):
    """Station names for this system, those at `body` first, then by name.

    `resolve` turns a UEX body name into the program's body key ("Pyro V" ->
    "pyrov"), so the two spellings can be compared.
    """
    def at_body(s):
        return body is not None and any(
            resolve(s.get(f)) == body or _canon(s.get(f)) == body
            for f in ("planet_name", "moon_name"))

    mine = [s for s in stations
            if _canon(s.get("star_system_name")) == _canon(system)]
    mine.sort(key=lambda s: (not at_body(s), s["name"].lower()))
    return [s["name"] for s in mine]


def summary(station, fetched):
    """What a report keeps of a station: enough to read, nothing to look up."""
    return {
        "name": station["name"],
        "body": station.get("moon_name") or station.get("orbit_name")
        or station.get("planet_name"),
        "faction": station.get("faction_name"),
        "services": [word for flag, word in SERVICES if station.get(flag)],
        "pads": station.get("pad_types"),
        "armistice": bool(station.get("is_armistice")),
        "source": "UEX " + time.strftime("%Y-%m-%d", time.localtime(fetched)),
    }


def report_lines(info, width):
    """The station block of a report, each line at most `width` characters."""
    lines = [f"Station  {info['name']}"]
    about = [x for x in (info.get("body"), info.get("faction")) if x]
    if info.get("armistice"):
        about.append("armistice")
    if about:
        lines.append("         " + ", ".join(about))
    if info.get("pads"):
        lines.append(f"Pads     {info['pads'].replace(',', ' ')}")
    # Services wrap rather than run off the panel.
    row = "Services"
    for word in info.get("services") or ["none listed"]:
        if len(row) + 1 + len(word) > width:
            lines.append(row)
            row = "        "
        row += " " + word
    lines.append(row)
    return [line[:width] for line in lines]
