"""Builds the sample data a release attaches, sc-locator-sample-data.zip:
a backup zip that Settings > Backup > Restore everything reads.

    python make_sample_data.py [data folder] [zip]

The data folder defaults to this one (run from source, the data is here);
the zip to dist\\sc-locator-sample-data.zip. Reads the data, never writes
it.

What goes in: waypoints, reports, height maps, surveys, maps, the station
list and SUB categories. Not settings (screen calibration is per PC), not
the survey being resumed, not bak/ (old versions could hold what was made
private later), not discord.json. Left out: private items, and items
taken in from someone else on Discord, which are not ours to hand out.

Every item but a height map is marked as coming from the sample data
(discordshare.FROM), so a restored copy cannot share it on to an org's
channels as its own. Height maps are not marked, as on a Discord import:
the new owner's own surveying adds to the same record.
"""
import json
import sys
import time
import zipfile
from pathlib import Path

import databackup
import discordshare
import updater

HERE = Path(__file__).resolve().parent
OUT = HERE / "dist" / "sc-locator-sample-data.zip"
SAMPLE_FROM = {"user": "", "name": "SC Locator sample data", "uid": ""}
LISTS = ("waypoints.json", "traces.json", "maps.json")      # lists of items
BY_KEY = ("bodies.json",)                                   # {key: item}
AS_THEY_ARE = ("stations.json", "subtypes.json")


def _mine(rec):
    """An item to hand out: ours, and not private."""
    return (isinstance(rec, dict) and not rec.get("private")
            and not rec.get(discordshare.FROM))


def _marked(rec):
    out = {k: v for k, v in rec.items() if k not in ("private", "share")}
    out[discordshare.FROM] = dict(SAMPLE_FROM)
    return out


def sample(data):
    """The sample's files, {file name: data}, from ours as read:
    {file name: data}, a file that is missing left out."""
    out = {}
    for name in LISTS:
        if name in data:
            out[name] = [_marked(r) for r in data[name] if _mine(r)]
    for name in BY_KEY:
        if name in data:
            out[name] = {k: _marked(r) for k, r in data[name].items()
                         if _mine(r)}
    if "heightmap.json" in data:
        out["heightmap.json"] = {
            body: {k: v for k, v in rec.items()
                   if k not in ("private", "share")}
            for body, rec in data["heightmap.json"].items()
            if not rec.get("private")}
    if "maps.json" in out and "waypoints.json" in out:
        kept = {w["id"] for w in out["waypoints.json"]}
        for m in out["maps.json"]:
            if "numbers" in m:
                m["numbers"] = {k: n for k, n in m["numbers"].items()
                                if k in kept}
    for name in AS_THEY_ARE:
        if name in data:
            out[name] = data[name]
    return out


def write(dest, files, version, now=None):
    """`files` as a backup zip at `dest`, as databackup.create writes one,
    and checked as a restore would check it."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, json.dumps(data, indent=2))
        z.writestr(databackup.MANIFEST, json.dumps({
            "app": databackup.APP, "format": databackup.FORMAT,
            "version": version,
            "created": time.strftime("%Y-%m-%d %H:%M", time.localtime(now))}))
    return databackup.verify(dest, list(files))


def main(folder=HERE, dest=OUT):
    folder = Path(folder)
    data = {}
    for name in LISTS + BY_KEY + AS_THEY_ARE + ("heightmap.json",):
        path = folder / name
        if path.exists():
            data[name] = json.loads(path.read_text(encoding="utf-8"))
    info = write(dest, sample(data), updater.VERSION)
    print(f"Wrote {dest}")
    for name, count in sorted(info["counts"].items()):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
