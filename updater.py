"""Is a newer SC Locator out, and fetching its installer.

GitHub's "latest release" is the only source: drafts and pre-releases never
show there, so neither reaches anyone by accident. Nothing here touches a
window; main_window's Updates shows the results.
"""
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

VERSION = "1.4.0"               # bump with the tag (vX.Y.Z); build.ps1 reads it
REPO = "city028/sc-locator"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "sc-locator-setup.exe"     # the name every release must attach
USER_AGENT = f"sc-locator/{VERSION}"
CHUNK = 256 * 1024


def parse_version(text):
    """(1, 2, 3) from "v1.2.3" and the like; None if it holds no version."""
    m = re.match(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    return tuple(int(g or 0) for g in m.groups()) if m else None


def is_newer(remote, local):
    """Is `remote` later than `local`? Unreadable is never an update."""
    remote, local = parse_version(remote), parse_version(local)
    return bool(remote and local and remote > local)


def fetch_release(timeout=15):
    """GitHub's record of the latest release. Raises on any failure."""
    req = urllib.request.Request(API_URL, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def read_release(data, current=VERSION):
    """What is wanted from GitHub's record. `available` needs the installer
    and its checksum as well as a later version: what cannot be verified is
    never offered."""
    tag = data.get("tag_name") or ""
    asset = next((a for a in data.get("assets", [])
                  if a.get("name") == ASSET_NAME), {})
    digest = asset.get("digest") or ""
    sha256 = digest[7:] if digest.startswith("sha256:") else None
    url = asset.get("browser_download_url")
    return {"error": None, "latest": tag.lstrip("v"),
            "published": (data.get("published_at") or "")[:10],
            "notes": data.get("body") or "", "url": url,
            "size": asset.get("size"), "sha256": sha256,
            "available": bool(url and sha256 and is_newer(tag, current))}


def check(fetch=fetch_release, current=VERSION):
    """read_release of the latest release. Never raises: a failure comes back
    in "error"."""
    try:
        return read_release(fetch(), current)
    except Exception as exc:
        return {"error": str(exc) or type(exc).__name__, "available": False}


def download(info, dest, progress=None, opener=urllib.request.urlopen):
    """Fetch the installer in `info` to `dest` and return `dest`. It only
    gets that name once its SHA-256 matches GitHub's; anything else raises
    and leaves nothing behind. `progress` is given 0..1 as it comes in."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(info["url"],
                                 headers={"User-Agent": USER_AGENT})
    sha, done = hashlib.sha256(), 0
    try:
        with opener(req, timeout=60) as resp, open(part, "wb") as out:
            while chunk := resp.read(CHUNK):
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if progress and info.get("size"):
                    progress(min(1.0, done / info["size"]))
        if sha.hexdigest().lower() != info["sha256"].lower():
            raise ValueError("the download does not match its checksum")
        part.replace(dest)
        return dest
    finally:
        part.unlink(missing_ok=True)


def can_install(exe=None):
    """Was this copy put here by the installer? Run from source or from a
    loose .exe, there is nothing for an update to replace."""
    exe = Path(exe or sys.executable)
    return bool(getattr(sys, "frozen", False)
                and (exe.parent / "unins000.exe").exists())


def start_install(setup):
    """Start the installer, which waits for this program to close (see
    installer/sc-locator.iss). It shows its progress, asks nothing and
    starts the new version when done."""
    subprocess.Popen([str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                      "/SP-"])
