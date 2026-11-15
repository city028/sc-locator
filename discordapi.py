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


def test_connection(cfg, opener=urllib.request.urlopen):
    """What Test connection finds, as lines to show: each webhook, the read
    token, whether it can read each channel, and our link code."""
    lines, hooks = [], {}
    for name in discordshare.CHANNELS:
        url = cfg["webhooks"].get(name, "").strip()
        label = f"{name.capitalize()}:"
        if not url:
            lines.append(f"{label} no webhook URL, so nothing can go there")
            continue
        try:
            hooks[name] = get_webhook(url, opener)
        except DiscordError as exc:
            lines.append(f"{label} {_problem(exc)}")
            continue
        lines.append(f"{label} webhook '{hooks[name].get('name')}' works")
    if len({h.get("guild_id") for h in hooks.values()}) > 1:
        lines.append("The webhooks are not all in the same server.")

    lines.append("")
    token = cfg.get("token", "").strip()
    if not token:
        lines.append("Read token: none, so Import cannot read the channels.")
        return lines
    try:
        bot = request("/users/@me", token, opener)
        app = request("/applications/@me", token, opener)
    except DiscordError as exc:
        lines.append(f"Read token: {_problem(exc)}")
        return lines
    lines.append(f"Read token: bot '{bot.get('username')}'")
    if not (app.get("flags", 0) & MESSAGE_CONTENT):
        lines.append("  Message Content intent is OFF: turn it on in the "
                     "Developer Portal (Bot > Privileged Gateway Intents), "
                     "or every post comes back empty.")

    read = []
    for name in discordshare.CHANNELS:
        if name not in hooks:
            continue
        channel = hooks[name].get("channel_id")
        label = f"  {name.capitalize()} channel:"
        try:
            info = request(f"/channels/{channel}", token, opener)
            got = messages(channel, token, TEST_READ, opener)
        except DiscordError as exc:
            lines.append(f"{label} {_problem(exc)}")
            continue
        lines.append(f"{label} #{info.get('name')} can be read")
        read += [(info.get("name"), m) for m in got]

    lines.append("")
    if not cfg.get("key"):
        lines.append("Your account: not linked yet (Link account).")
        return lines
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
                     "in one of them from your own Discord account.")
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
