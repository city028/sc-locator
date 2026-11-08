"""All of the program's data in one zip file, and putting it back or wiping it.

Nothing here knows the program: it is handed the data files and the folder of
automatic backups, and in that folder touches only the backups of those files.
Run from source, that folder also holds other things.

A restore or wipe cannot be done while the program runs, as what it holds in
memory is written back over the files as it closes. So each is only requested
here, in a `pending` folder, and carried out by apply_pending at the next
start, before anything is loaded. If that is cut short it runs again.
"""
import json
import shutil
import time
import zipfile
import zlib
from pathlib import Path

APP = "sc-locator"
FORMAT = 1
MANIFEST = "manifest.json"
BAK = "bak"
WIPE_MARK = "wipe-requested"
SAFETY_MARK = "safety-copy-made"
SAFETY_PREFIX = "before-restore-"
SAFETY_KEEP = 5
MAX_BYTES = 2_000_000_000       # unpacked; far above any real backup


class BackupError(Exception):
    """Why something cannot be backed up or restored, in words to show."""


def default_name(now=None):
    return time.strftime("sc-locator-backup-%Y-%m-%d-%H%M.zip",
                         time.localtime(now))


def is_automatic_backup(name, names):
    """Is `name` a plain file name, of a kept version of one of `names`?"""
    return (name.endswith(".bak") and not set(name) & set("/\\:")
            and any(name.startswith(n + ".") for n in names))


def automatic_backups(bak_dir, names):
    if not bak_dir.is_dir():
        return []
    return sorted(p for p in bak_dir.iterdir()
                  if p.is_file() and is_automatic_backup(p.name, names))


def _read_whole(path, attempts=5, pause=0.1):
    """The bytes of a JSON file, None if it does not exist. The program may
    be writing it this moment, so a file that reads as broken is read again
    rather than stored half-written."""
    last = None
    for _ in range(attempts):
        try:
            data = path.read_bytes()
            json.loads(data.decode("utf-8"))
            return data
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as exc:
            last = exc
            time.sleep(pause)
    raise BackupError(f"{path.name} could not be read: {last}")


def create(dest, files, bak_dir, version, now=None):
    """Write the zip at `dest`. It only gets that name once complete, so an
    unfinished one never looks like a backup."""
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    names = [f.name for f in files]
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as z:
            for path in files:
                data = _read_whole(path)
                if data is not None:
                    z.writestr(path.name, data)
            for path in automatic_backups(bak_dir, names):
                try:
                    z.writestr(f"{BAK}/{path.name}", path.read_bytes())
                except FileNotFoundError:   # pruned by a save just now
                    pass
            z.writestr(MANIFEST, json.dumps({
                "app": APP, "format": FORMAT, "version": version,
                "created": time.strftime("%Y-%m-%d %H:%M",
                                         time.localtime(now))}))
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)


def verify(path, names):
    """The manifest of the backup at `path`, plus "counts": how many records
    each data file holds. Raises BackupError unless every part of it is
    sound and named as a backup's parts are; only those names are ever
    written, so a zip cannot put anything outside the data folder."""
    try:
        with zipfile.ZipFile(path) as z:
            return _check(z, names)
    except BackupError:
        raise
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError,
            NotImplementedError, ValueError, zlib.error) as exc:
        raise BackupError(f"This file cannot be read as a backup: {exc}")


def _check(z, names):
    listed = z.namelist()
    if MANIFEST not in listed:
        raise BackupError("This is not an SC Locator backup.")
    if len(set(listed)) != len(listed):
        raise BackupError("This backup holds the same file twice.")
    prefix = BAK + "/"
    for name in listed:
        if (name != MANIFEST and name not in names
                and not (name.startswith(prefix) and is_automatic_backup(
                    name[len(prefix):], names))):
            raise BackupError(f"This backup holds a file it should not: {name}")
    if sum(i.file_size for i in z.infolist()) > MAX_BYTES:
        raise BackupError("This backup is far too large to be a real one.")
    bad = z.testzip()
    if bad:
        raise BackupError(f"This backup is damaged: {bad}")
    manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("app") != APP:
        raise BackupError("This is not an SC Locator backup.")
    if manifest.get("format") != FORMAT:
        raise BackupError("This backup was made by a newer version of SC "
                          "Locator. Update first, then restore it.")
    counts = {}
    for name in listed:
        if name in names:
            try:
                data = json.loads(z.read(name).decode("utf-8"))
            except ValueError:
                raise BackupError(f"This backup is damaged: {name}")
            counts[name] = len(data) if hasattr(data, "__len__") else 1
    if not counts:
        raise BackupError("This backup holds no data.")
    manifest["counts"] = counts
    return manifest


def _fresh_part(pending):
    part = pending.with_name(pending.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    return part


def _publish(part, pending):
    """Only a complete request ever carries the name apply_pending looks for."""
    shutil.rmtree(pending, ignore_errors=True)
    part.rename(pending)


def stage_restore(path, pending, names):
    """Check the backup at `path` and unpack it to `pending`, for the next
    start. Returns its manifest."""
    info = verify(path, names)
    part = _fresh_part(pending)
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            target = part / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(name))
    _publish(part, pending)
    return info


def stage_wipe(pending):
    """Ask for everything to be wiped at the next start."""
    part = _fresh_part(pending)
    (part / WIPE_MARK).write_text("", encoding="utf-8")
    _publish(part, pending)


def _safety_copies(bak_dir):
    return sorted(bak_dir.glob(SAFETY_PREFIX + "*.zip")) if bak_dir.is_dir() else []


def _safety_copy(files, bak_dir, version, now):
    """The data as it stands, in a backup like any other, so that a restore
    can itself be undone with Restore."""
    bak_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    create(bak_dir / f"{SAFETY_PREFIX}{stamp}.zip", files, bak_dir, version, now)
    copies = _safety_copies(bak_dir)
    for old in copies[:max(0, len(copies) - SAFETY_KEEP)]:
        old.unlink(missing_ok=True)


def _wipe(files, bak_dir, extras):
    names = [f.name for f in files]
    for path in [*files, *extras, *automatic_backups(bak_dir, names),
                 *_safety_copies(bak_dir)]:
        path.unlink(missing_ok=True)
    try:
        bak_dir.rmdir()
    except OSError:                     # not empty, or never made
        pass


def _replace(source, target):
    """Copy over `target` in one step: it is whole before and whole after."""
    temporary = target.with_name(target.name + ".restoring")
    shutil.copy2(source, temporary)
    temporary.replace(target)


def apply_pending(files, bak_dir, pending, extras=(), version="", now=None):
    """Carry out what was requested: "restored", "wiped", or None if nothing
    was. Call before any data is loaded. The request is removed last, so if
    this is cut short the next start does it again; the safety copy is made
    only the first time, while the data is still all as it was."""
    if not pending.is_dir():
        return None
    if (pending / WIPE_MARK).exists():
        _wipe(files, bak_dir, extras)
        shutil.rmtree(pending)
        return "wiped"
    if not (pending / MANIFEST).exists():       # never to be taken for a wipe
        shutil.rmtree(pending, ignore_errors=True)
        return None
    if not (pending / SAFETY_MARK).exists():
        try:
            _safety_copy(files, bak_dir, version, now)
            (pending / SAFETY_MARK).write_text("", encoding="utf-8")
        except (OSError, BackupError) as exc:
            shutil.rmtree(pending, ignore_errors=True)
            raise BackupError("Your present data could not be saved first, so "
                              f"nothing was restored: {exc}")
    for path in files:
        new = pending / path.name
        if new.exists():
            _replace(new, path)
        else:
            path.unlink(missing_ok=True)
    names = [f.name for f in files]
    for old in automatic_backups(bak_dir, names):
        old.unlink(missing_ok=True)
    kept = pending / BAK
    if kept.is_dir():
        bak_dir.mkdir(parents=True, exist_ok=True)
        for new in kept.iterdir():
            _replace(new, bak_dir / new.name)
    shutil.rmtree(pending)
    return "restored"
