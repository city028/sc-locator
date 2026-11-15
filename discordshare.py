"""Sharing items with org mates through Discord channels: the logic only.

No network and no tkinter here, so all of it can be tested on sample
messages. The design is in NOTES.md ("Sharing to Discord: the design").

An item goes out as one Discord post, one item per post, never a data file:
a short readable text, for people, and an attachment POST_FILE, for the
program, holding the item and a signature:

    {"v": 1, "kind": "waypoint", "id": "<its id with the sharer>",
     "item": {...}, "at": time, "key": "<sharer's public key>", "sig": "..."}

A webhook post's author is the webhook, so who shared it cannot be read off
the post. Instead each member links their key once: Link account makes an
Ed25519 key pair, and the member posts its code (link_code) in a share
channel from their own account, where Discord records who wrote it. A post
counts as theirs when its signature checks out against a key they posted.
Deleting the code message withdraws the key: posts signed with it then
fail as "not linked".

An item is known everywhere by its uid: the sharer's key, its kind and its
id with them (a waypoint's or map's id, a report's key, a trace's name, a
height map's body). Two histories, kept in discord.json by the caller:

    posted:   {"kind/id": {"hash", "message", "at"}}   what we shared
    imported: {uid: {"hash", "ours", "at"}}            what we took in

cryptography is imported only where a key is used, so the program starts
without it.
"""
import base64
import hashlib
import json
import os
import re
from pathlib import Path

VERSION = 1
POST_FILE = "scl-item.json"
KINDS = ("waypoint", "report", "trace", "heightmap", "map")
LINK_PREFIX = "SCL-LINK"
MAX_TEXT = 2000                 # Discord's limit on a message's text
FROM = "from"                   # on an item imported from someone else

# What sort_imports sorts a channel's posts into. A post in none of them is
# not shown: our own, an older version of an item, a message that is not a
# post.
GROUPS = ("new", "update", "already", "clash", "skipped", "error",
          "blacklisted", "not_allowed")

_LINK_RE = re.compile(LINK_PREFIX + r"\s+([A-Za-z0-9_-]{43})(?![A-Za-z0-9_-])")


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def canonical(obj):
    """The bytes a payload is signed and hashed as: the same for the same
    data however its keys were ordered, and the same after a JSON round
    trip (Python writes floats so they read back exactly)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def item_hash(item):
    """What tells one version of an item from another."""
    return hashlib.sha256(canonical(item)).hexdigest()


def clean(rec):
    """An item as it is shared: without our own Private and Share marks."""
    return {k: v for k, v in rec.items() if k not in ("private", "share")}


# --- keys -----------------------------------------------------------------

def new_key():
    """A new private key, as text to keep in discord.json."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    from cryptography.hazmat.primitives import serialization
    raw = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    return _b64(raw)


def _private(text):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey)
    return Ed25519PrivateKey.from_private_bytes(_unb64(text))


def public_of(private_text):
    """The public key, as text, that goes with a private one."""
    from cryptography.hazmat.primitives import serialization
    return _b64(_private(private_text).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def link_code(private_text):
    """What the member posts from their own account to link their key."""
    return f"{LINK_PREFIX} {public_of(private_text)}"


def sign(payload, private_text):
    """The signature of a payload (everything in the post file but "sig")."""
    return _b64(_private(private_text).sign(canonical(payload)))


def verify(payload, sig, public_text):
    """Whether `sig` is the signature of `payload` by `public_text`'s key."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey)
    try:
        key = Ed25519PublicKey.from_public_bytes(_unb64(public_text))
        key.verify(_unb64(sig), canonical(payload))
        return True
    except (InvalidSignature, ValueError):
        return False


def _snowflake(msg):
    """A message's id as a number: Discord's ids grow with time."""
    try:
        return int(msg["id"])
    except (KeyError, TypeError, ValueError):
        return 0


def linked_keys(messages):
    """{public key: {"user": id, "name": name}} from the link codes in
    `messages`, as Discord gives them. Only codes a member wrote count, not
    a webhook's. A key posted by more than one member is the first one's:
    copying someone's code later does not make their posts yours."""
    out = {}
    for msg in sorted(messages, key=_snowflake):
        if msg.get("webhook_id") or not isinstance(msg.get("content"), str):
            continue
        author = msg.get("author") or {}
        if not author.get("id"):
            continue
        for key in _LINK_RE.findall(msg["content"]):
            if len(_unb64(key)) == 32 and key not in out:
                out[key] = {"user": str(author["id"]),
                            "name": author.get("username", "")}
    return out


# --- posting ----------------------------------------------------------------

def make_post(kind, item_id, item, private_text, now, text=""):
    """(message text, attachment bytes) for one item, signed.

    `item` is shared as clean() leaves it. `text` is the readable part,
    such as Copy makes; it is cut to Discord's limit."""
    if kind not in KINDS:
        raise ValueError(f"'{kind}' is not a kind of item")
    payload = {"v": VERSION, "kind": kind, "id": str(item_id),
               "item": clean(item), "at": now,
               "key": public_of(private_text)}
    payload["sig"] = sign(payload, private_text)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT - 1] + "…"
    return text, canonical(payload)


def to_publish(items, posted, everything=False):
    """[(kind, id, item, is_update)] of `items` to post, `items` being
    (kind, id, record) of the items to share (sc_locator.shareable).

    Publish new leaves out what was posted as it is now; Share all
    (`everything`) posts each of them again."""
    out = []
    for kind, item_id, rec in items:
        item = clean(rec)
        before = posted.get(f"{kind}/{item_id}")
        if everything or not before or before["hash"] != item_hash(item):
            out.append((kind, item_id, item, before is not None))
    return out


def record_posted(posted, kind, item_id, item, message_id, now):
    """Note in `posted` that `item` went out as message `message_id`."""
    posted[f"{kind}/{item_id}"] = {"hash": item_hash(clean(item)),
                                   "message": str(message_id), "at": now}


# --- importing --------------------------------------------------------------

def read_post(data):
    """(payload, sig) from a post file's bytes. ValueError if it is not
    one."""
    try:
        post = json.loads(data)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"not a post: {exc}") from None
    if not isinstance(post, dict):
        raise ValueError("not a post")
    if post.get("v") != VERSION:
        raise ValueError(f"post version {post.get('v')!r}, not {VERSION}")
    if post.get("kind") not in KINDS:
        raise ValueError(f"kind {post.get('kind')!r} unknown")
    for field, kind in (("id", str), ("key", str), ("sig", str),
                        ("item", dict)):
        if not isinstance(post.get(field), kind) or not post[field]:
            raise ValueError(f"{field} missing")
    if not isinstance(post.get("at"), (int, float, str)) \
            or isinstance(post.get("at"), bool):
        raise ValueError("at missing")
    sig = post.pop("sig")
    return post, sig


def post_file(msg):
    """The attachment of `msg` that is a post file, None if it has none."""
    for att in msg.get("attachments") or ():
        if att.get("filename") == POST_FILE:
            return att
    return None


def uid(key, kind, item_id):
    """An item's name across the org."""
    return f"{key}/{kind}/{item_id}"


def sort_imports(messages, files, *, mine=None, allow=(), block=(),
                 imported=None, checks=None, clash=None, skipped=None):
    """{group: [entry]} for the posts in `messages`, for GROUPS.

    messages: the share channels' messages, as Discord gives them, with
              the link codes among them.
    files:    {message id: bytes} of their post files, as downloaded.
    mine:     our own public key: our own posts are left out.
    allow, block: Discord user ids. The blacklist wins; an empty allow
              list lets everyone in.
    imported: the imported history, to tell updates and what we already
              have from new items.
    checks:   {kind: f(id, item) -> [problems]}, sc_locator's checks for
              shared files. A kind without one is an error for now.
    clash:    f(kind, id, item) -> [what of ours it clashes with].
    skipped:  {uid: hash} of what was skipped: shown apart, until the
              sharer changes it.

    Each entry is a dict: message, kind, id, item, at, uid, hash, user,
    name; "problems" on an error, "clashes" on a clash, "ours" (what it
    was imported as) on an update or what we already have. Only the
    newest post of an item counts; a post that fails its signature cannot
    hide an older one that does not.
    """
    imported, skipped = imported or {}, skipped or {}
    checks = checks or {}
    allow, block = {str(u) for u in allow}, {str(u) for u in block}
    keys = linked_keys(messages)
    out = {g: [] for g in GROUPS}
    newest = {}
    for msg in messages:
        att = post_file(msg)
        if att is None:
            continue
        mid = str(msg.get("id"))
        entry = {"message": mid}
        try:
            if mid not in files:
                raise ValueError("its file could not be downloaded")
            payload, sig = read_post(files[mid])
        except ValueError as exc:
            entry["problems"] = [str(exc)]
            out["error"].append(entry)
            continue
        entry.update(kind=payload["kind"], id=payload["id"],
                     item=payload["item"], at=payload["at"],
                     uid=uid(payload["key"], payload["kind"], payload["id"]),
                     hash=item_hash(payload["item"]))
        who = keys.get(payload["key"])
        if payload["key"] == mine:
            continue
        if who is None:
            entry["problems"] = ["not linked: no link code for its key"]
            out["error"].append(entry)
            continue
        entry.update(user=who["user"], name=who["name"])
        if not verify(payload, sig, payload["key"]):
            entry["problems"] = ["its signature does not match"]
            out["error"].append(entry)
            continue
        old = newest.get(entry["uid"])
        if old is None or _snowflake(msg) > _snowflake({"id": old["message"]}):
            newest[entry["uid"]] = entry

    for entry in sorted(newest.values(),
                        key=lambda e: _snowflake({"id": e["message"]})):
        if entry["user"] in block:
            out["blacklisted"].append(entry)
            continue
        if allow and entry["user"] not in allow:
            out["not_allowed"].append(entry)
            continue
        check = checks.get(entry["kind"])
        problems = (check(entry["id"], entry["item"]) if check
                    else [f"a {entry['kind']} cannot be imported yet"])
        if problems:
            entry["problems"] = problems
            out["error"].append(entry)
            continue
        before = imported.get(entry["uid"])
        if before and before["hash"] == entry["hash"]:
            entry["ours"] = before["ours"]
            out["already"].append(entry)
            continue
        if skipped.get(entry["uid"]) == entry["hash"]:
            if before:
                entry["ours"] = before["ours"]
            out["skipped"].append(entry)
            continue
        if before:
            entry["ours"] = before["ours"]
            out["update"].append(entry)
            continue
        found = clash(entry["kind"], entry["id"], entry["item"]) if clash \
            else []
        if found:
            entry["clashes"] = found
            out["clash"].append(entry)
        else:
            out["new"].append(entry)
    return out


def mark_imported(rec, entry):
    """Mark an item taken in from `entry` as someone else's, so it cannot
    be shared on."""
    for flag in ("private", "share"):
        rec.pop(flag, None)
    rec[FROM] = {"user": entry["user"], "name": entry["name"],
                 "uid": entry["uid"]}
    return rec


def record_imported(cfg, entry, ours, now):
    """Note in cfg["imported"] that `entry` was taken in as our item
    `ours`; it is no longer skipped."""
    cfg["imported"][entry["uid"]] = {"hash": entry["hash"],
                                     "ours": str(ours), "at": now}
    cfg["skipped"].pop(entry["uid"], None)


def record_skipped(cfg, entry):
    """Note that `entry` was skipped: not offered again as it is."""
    cfg["skipped"][entry["uid"]] = entry["hash"]


# --- settings: discord.json -------------------------------------------------
#
# The org's share channels, the read token, our private key and both
# histories. It holds secrets (anyone with a webhook URL can post as it,
# anyone with the key can sign as you), so it is kept out of backups and
# out of bak/, and written without a copy.

CHANNELS = ("waypoints", "surveys", "reports", "maps")
CHANNEL_OF = {"waypoint": "waypoints", "trace": "surveys",
              "heightmap": "surveys", "report": "reports", "map": "maps"}

_ID_RE = re.compile(r"\s*(\d{17,20})(?!\d)")


def blank_settings():
    return {"webhooks": {c: "" for c in CHANNELS}, "token": "", "key": "",
            "allow": [], "block": [], "posted": {}, "imported": {},
            "skipped": {}}


def load_settings(path):
    """discord.json, with whatever it lacks as new. A missing file is a
    blank one; one that cannot be read raises ValueError, so that it is
    not saved over (it may hold the only copy of the key)."""
    out = blank_settings()
    path = Path(path)
    if not path.exists():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path.name} cannot be read: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not Discord settings")
    for key, blank in out.items():
        if isinstance(data.get(key), type(blank)):
            out[key] = data[key]
    out["webhooks"] = {c: str(out["webhooks"].get(c) or "")
                       for c in CHANNELS}
    return out


def save_settings(path, cfg):
    """Write discord.json in one step: whole before and whole after."""
    path = Path(path)
    temporary = path.with_name(path.name + ".saving")
    temporary.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def user_ids(lines):
    """The Discord user ids in an allow list or blacklist: one at the start
    of each line, anything after it (a name, say) only a note."""
    return [m.group(1) for m in map(_ID_RE.match, lines) if m]


def id_problems(lines):
    """The numbers of the lines, from 1, that are not blank and do not
    start with a user id."""
    return [n for n, line in enumerate(lines, 1)
            if line.strip() and not _ID_RE.match(line)]
