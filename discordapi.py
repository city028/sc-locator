"""Discord's REST API, as far as sharing needs it, and Test connection.

The logic of sharing is in discordshare.py; this is only the calls. They
block, so the window runs them on a thread of its own. `opener` is
urllib's urlopen, or a stand-in in the tests.

A webhook URL is all that posting needs: Discord takes it as the password.
Reading a channel needs a bot's token, sent as "Bot <token>". That is not a
bot that runs, only an application with a token: Import calls the API with
it. It needs View Channel and Read Message History on the share channels,
and the Message Content intent on, or every message comes back without its
text and attachments.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

import discordshare
import updater

API = "https://discord.com/api/v10"
# Discord asks bots to say who they are in this form.
USER_AGENT = (f"DiscordBot (https://github.com/{updater.REPO}, "
              f"{updater.VERSION})")
WEBHOOK_RE = re.compile(
    r"https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api(?:/v\d+)?"
    r"/webhooks/(\d+)/([A-Za-z0-9_-]+)/?")
# Application flags: the Message Content intent, on for a bot in fewer
# than 100 servers (LIMITED) or approved for more.
MESSAGE_CONTENT = (1 << 18) | (1 << 19)
PAGE = 100                       # the most messages Discord gives at once
TEST_READ = 500                  # how far back Test connection looks


class DiscordError(Exception):
    """A call that failed, with the HTTP status if Discord answered."""

    def __init__(self, text, status=None):
        super().__init__(text)
        self.status = status


def webhook_parts(url):
    """(id, token) of a webhook URL, None if it is not one."""
    m = WEBHOOK_RE.fullmatch(url.strip())
    return m.groups() if m else None


def request(path, token=None, opener=urllib.request.urlopen, timeout=15,
            tries=3, sleep=time.sleep, data=None, content_type=None):
    """GET `path` (under API), or POST `data` to it, and return its JSON.
    When Discord says to slow down, waits as long as it asks, up to
    `tries` times."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bot {token}"
    if content_type:
        headers["Content-Type"] = content_type
    for attempt in range(tries):
        req = urllib.request.Request(API + path, data=data, headers=headers)
        try:
            with opener(req, timeout=timeout) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read() or b"{}")
            except ValueError:
                body = {}
            if exc.code == 429 and attempt + 1 < tries:
                sleep(min(float(body.get("retry_after", 1)), 30))
                continue
            raise DiscordError(body.get("message") or exc.reason,
                               exc.code) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise DiscordError(f"Discord could not be reached: "
                               f"{getattr(exc, 'reason', exc)}") from None
    raise DiscordError("Discord kept asking to slow down", 429)


def get_webhook(url, opener=urllib.request.urlopen):
    """The webhook a URL is: its name, channel_id, guild_id."""
    parts = webhook_parts(url)
    if not parts:
        raise DiscordError("not a Discord webhook URL")
    return request(f"/webhooks/{parts[0]}/{parts[1]}", opener=opener)


def messages(channel_id, token, most=None, opener=urllib.request.urlopen):
    """A channel's messages, newest first, `most` of them (None: all)."""
    out, before = [], None
    while most is None or len(out) < most:
        size = PAGE if most is None else min(PAGE, most - len(out))
        query = f"?limit={size}" + (f"&before={before}" if before else "")
        page = request(f"/channels/{channel_id}/messages{query}", token,
                       opener=opener)
        out += page
        if len(page) < size:
            break
        before = page[-1]["id"]
    return out


NOT_A_CHANNEL = ("Link channel: not a channel link (right-click the channel "
                 "> Copy Link)")


def link_channel(cfg, opener=urllib.request.urlopen):
    """(channel id, problem) for the Links box: "" for an empty box, None
    with a problem when it cannot be used. Besides the channel's link or
    id it takes a webhook URL, as the boxes above it do: the webhook says
    which channel it is in."""
    text = cfg.get("link_channel", "").strip()
    found = discordshare.link_channel_id(text)
    if found is not None:
        return found, None
    if webhook_parts(text):
        try:
            return get_webhook(text, opener)["channel_id"], None
        except DiscordError as exc:
            return None, f"Link channel: the webhook there is {_problem(exc)}"
    return None, NOT_A_CHANNEL


def _problem(exc):
    """What a failed call means, in words."""
    if exc.status == 401:
        return "the token is wrong (Discord does not know it)"
    if exc.status == 403:
        return ("not allowed: give the bot View Channel and Read Message "
                "History on this channel")
    if exc.status == 404:
        return "not found: deleted, or the URL is wrong"
    return str(exc)


# Test connection's steps, weighed by about how many calls each makes: a
# channel is read up to TEST_READ messages, a page of PAGE at a time.
W_HOOK, W_TOKEN, W_READ, W_CODE = 1, 2, 1 + TEST_READ // PAGE, 1


def test_connection(cfg, opener=urllib.request.urlopen, progress=None):
    """What Test connection finds, as lines to show: each webhook, the read
    token, whether it can read each channel, and our link code.
    progress: f(text, fraction), told before each step what it is doing
    and how much of the whole is done, 0 to 1. The whole is planned from
    the settings, each step weighed by about how many calls it makes; a
    step that will not happen (a channel behind a broken webhook) is
    taken out of it, so the fraction only grows."""
    token = cfg.get("token", "").strip()
    named = [n for n in discordshare.CHANNELS
             if cfg["webhooks"].get(n, "").strip()]
    has_link = bool(cfg.get("link_channel", "").strip())
    plan = {"done": 0, "total": len(named) * W_HOOK}
    if token:
        plan["total"] += (W_TOKEN + (len(named) + has_link) * W_READ
                          + (W_CODE if cfg.get("key") else 0))

    def say(text):
        if progress:
            progress(text, min(1.0, plan["done"] / max(1, plan["total"])))

    def did(weight):
        plan["done"] += weight

    def dropped(weight):
        plan["total"] -= weight

    lines, hooks = [], {}
    for name in discordshare.CHANNELS:
        url = cfg["webhooks"].get(name, "").strip()
        label = f"{name.capitalize()}:"
        if not url:
            lines.append(f"{label} no webhook URL, so nothing can go there")
            continue
        say(f"Checking the {name.capitalize()} webhook")
        try:
            hooks[name] = get_webhook(url, opener)
        except DiscordError as exc:
            lines.append(f"{label} {_problem(exc)}")
            did(W_HOOK)
            if token:
                dropped(W_READ)         # its channel cannot be found
            continue
        did(W_HOOK)
        lines.append(f"{label} webhook '{hooks[name].get('name')}' works")
    if len({h.get("guild_id") for h in hooks.values()}) > 1:
        lines.append("The webhooks are not all in the same server.")

    lines.append("")
    if not token:
        lines.append("Read token: none, so Import cannot read the channels.")
        return lines
    say("Checking the read token")
    try:
        bot = request("/users/@me", token, opener)
        app = request("/applications/@me", token, opener)
    except DiscordError as exc:
        lines.append(f"Read token: {_problem(exc)}")
        return lines
    did(W_TOKEN)
    lines.append(f"Read token: bot '{bot.get('username')}'")
    if not (app.get("flags", 0) & MESSAGE_CONTENT):
        lines.append("  Message Content intent is OFF: turn it on in the "
                     "Developer Portal (Bot > Privileged Gateway Intents), "
                     "or every post comes back empty.")

    to_read = [(f"{name.capitalize()} channel", hooks[name].get("channel_id"))
               for name in discordshare.CHANNELS if name in hooks]
    link, link_problem = link_channel(cfg, opener)
    if link:
        to_read.append(("Link channel", link))
    elif has_link:
        dropped(W_READ)
    read, guilds = [], {h.get("guild_id") for h in hooks.values()}
    for label, channel in to_read:
        say(f"Reading the {label.lower()}")
        try:
            info = request(f"/channels/{channel}", token, opener)
            got = messages(channel, token, TEST_READ, opener)
        except DiscordError as exc:
            lines.append(f"  {label}: {_problem(exc)}")
            did(W_READ)
            continue
        did(W_READ)
        lines.append(f"  {label}: #{info.get('name')} can be read")
        if label == "Link channel" and guilds and \
                info.get("guild_id") not in guilds:
            lines.append("  The link channel is not in the webhooks' "
                         "server.")
        read += [(info.get("name"), m) for m in got]
    if link is None:
        lines.append(f"  {link_problem}.")
    elif not link:
        lines.append("  Link channel: none, so link codes are looked for "
                     "in the four share channels.")

    lines.append("")
    if not cfg.get("key"):
        lines.append("Your account: not linked yet (Link account).")
        return lines
    say("Looking for your link code")
    mine = discordshare.public_of(cfg["key"])
    who = discordshare.linked_keys([m for _c, m in read]).get(mine)
    if who:
        where = next(c for c, m in read
                     if mine in (m.get("content") or "")
                     and (m.get("author") or {}).get("id") == who["user"])
        lines.append(f"Your account: linked as '{who['name']}' (your code "
                     f"is in #{where}).")
    else:
        lines.append(f"Your account: your link code is not in the last "
                     f"{TEST_READ} messages of the channels read. Post it "
                     "in the link channel from your own Discord account.")
    return lines


# --- posting ----------------------------------------------------------------

MAX_FILE = 10 * 1024 * 1024     # the most Discord takes in a server without
                                # boosts


def _multipart(fields, boundary):
    """A multipart/form-data body: fields are (name, filename or None,
    content type, bytes)."""
    out = []
    for name, filename, ctype, body in fields:
        disposition = f'form-data; name="{name}"'
        if filename:
            disposition += f'; filename="{filename}"'
        out += [f"--{boundary}".encode(),
                f"Content-Disposition: {disposition}".encode(),
                f"Content-Type: {ctype}".encode(), b"", body]
    out += [f"--{boundary}--".encode(), b""]
    return b"\r\n".join(out)


def post_webhook(url, text, data, opener=urllib.request.urlopen,
                 sleep=time.sleep):
    """Post `text` with the post file `data` through a webhook, and return
    the message Discord made. Nobody is pinged, whatever the text says."""
    parts = webhook_parts(url)
    if not parts:
        raise DiscordError("not a Discord webhook URL")
    payload = {"content": text, "allowed_mentions": {"parse": []},
               "attachments": [{"id": 0,
                                "filename": discordshare.POST_FILE}]}
    boundary = uuid.uuid4().hex
    body = _multipart([("payload_json", None, "application/json",
                        json.dumps(payload).encode("utf-8")),
                       ("files[0]", discordshare.POST_FILE,
                        "application/json", data)], boundary)
    return request(f"/webhooks/{parts[0]}/{parts[1]}?wait=true",
                   opener=opener, tries=5, sleep=sleep, data=body,
                   content_type=f"multipart/form-data; boundary={boundary}")


def _webhook_problem(exc, channel):
    if exc.status in (401, 404):
        return (f"the {channel} webhook is gone: deleted, or its URL is "
                "wrong")
    return str(exc)


def publish(cfg, items, describe, now, everything=False, progress=None,
            lock=None, save=None, opener=urllib.request.urlopen,
            sleep=time.sleep):
    """Post the items that need it (discordshare.to_publish), each through
    its channel's webhook, noting each in cfg["posted"] as it goes.

    items:    (kind, id, record) of the items to share, copies: this runs
              on a thread of its own.
    describe: f(kind, id, item, is_update) -> the readable text.
    progress: f(done, total), called before each post.
    lock, save: held while cfg is read or changed, and called after each
              post, so a run cut short does not post again what went.
    Returns (sent, failed): [(kind, id, is_update)], [(kind, id, why)].
    A channel whose webhook is gone is not tried again; when Discord
    cannot be reached at all, the run stops there.
    """
    lock = lock or threading.Lock()
    with lock:
        todo = discordshare.to_publish(items, cfg["posted"], everything)
        hooks, key = dict(cfg["webhooks"]), cfg["key"]
    sent, failed, dead = [], [], {}
    for n, (kind, item_id, item, update) in enumerate(todo):
        if progress:
            progress(n, len(todo))
        channel = discordshare.CHANNEL_OF[kind]
        name = channel.capitalize()
        if channel not in dead and not webhook_parts(hooks.get(channel, "")):
            dead[channel] = f"no webhook for {name}"
        if channel in dead:
            failed.append((kind, item_id, dead[channel]))
            continue
        text, data = discordshare.make_post(
            kind, item_id, item, key, now,
            describe(kind, item_id, item, update))
        if len(data) > MAX_FILE:
            failed.append((kind, item_id, "too big for Discord "
                           f"({len(data) / 1e6:.1f} MB)"))
            continue
        try:
            msg = post_webhook(hooks[channel], text, data, opener, sleep)
        except DiscordError as exc:
            if exc.status is None:
                failed += [(k, i, str(exc)) for k, i, _it, _u in todo[n:]]
                break
            failed.append((kind, item_id, _webhook_problem(exc, name)))
            if exc.status in (401, 403, 404):
                dead[channel] = _webhook_problem(exc, name)
            continue
        with lock:
            discordshare.record_posted(cfg["posted"], kind, item_id, item,
                                       msg["id"], now)
            if save:
                save()
        sent.append((kind, item_id, update))
    if progress:
        progress(len(todo), len(todo))
    return sent, failed


# --- reading the channels ---------------------------------------------------

DOWNLOADS = 8                   # post files fetched at once


def download(url, opener=urllib.request.urlopen, timeout=30):
    """A post file from Discord's file server: no token, refused over
    MAX_FILE."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener(req, timeout=timeout) as resp:
            data = resp.read(MAX_FILE + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DiscordError(f"could not be downloaded: "
                           f"{getattr(exc, 'reason', exc)}") from None
    if len(data) > MAX_FILE:
        raise DiscordError("too big to be a post")
    return data


def read_channels(cfg, progress=None, opener=urllib.request.urlopen):
    """(messages, files, problems) from the share channels, for
    discordshare.sort_imports.

    Each webhook says which channel it posts in; all of each channel is
    read with the token, and the link channel too, where members post
    their link codes (codes in the share channels count as well). The
    post files are downloaded a few at a time,
    except our own latest posts, which Import leaves out anyway. A file
    that cannot be downloaded is left out of `files`, so its post shows as
    an error. `problems` are lines saying what could not be read; with no
    token, or Discord not reached, nothing is read.
    progress: f(text), with how far it has got.
    """
    token = cfg.get("token", "").strip()
    if not token:
        return [], {}, ["No read token: set one above."]
    channels, problems = {}, []
    for name in discordshare.CHANNELS:
        url = cfg["webhooks"].get(name, "").strip()
        if not url:
            continue
        try:
            channels.setdefault(get_webhook(url, opener)["channel_id"], name)
        except DiscordError as exc:
            if exc.status is None:
                return [], {}, [str(exc)]
            problems.append(f"{name.capitalize()}: {_problem(exc)}")
    link, link_problem = link_channel(cfg, opener)
    if link:
        channels.setdefault(link, "links")
    elif link is None:
        problems.append(link_problem)
    msgs = []
    for channel, name in channels.items():
        if progress:
            progress(f"Reading #{name}...")
        try:
            msgs += messages(channel, token, None, opener)
        except DiscordError as exc:
            if exc.status is None:
                return [], {}, [str(exc)]
            problems.append(f"{name.capitalize()} channel: {_problem(exc)}")
    ours = {p["message"] for p in cfg.get("posted", {}).values()}
    wanted = [(m["id"], discordshare.post_file(m)["url"]) for m in msgs
              if discordshare.post_file(m) and m["id"] not in ours]
    files, done = {}, [0]

    def fetch(job):
        mid, url = job
        try:
            files[mid] = download(url, opener)
        except DiscordError:
            pass
        done[0] += 1
        if progress:
            progress(f"Downloading {done[0]} of {len(wanted)}...")

    with ThreadPoolExecutor(DOWNLOADS) as pool:
        list(pool.map(fetch, wanted))
    return msgs, files, problems
