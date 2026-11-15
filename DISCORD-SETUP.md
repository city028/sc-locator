# Setting up Discord sharing for your org

SC Locator members share waypoints, reports, surveys and maps through a
few Discord channels. One admin sets this up once, in about 15 minutes;
each member then pastes what the admin gives them into **Settings >
Discord**.

Nothing has to run on a server. Posting uses webhooks, and reading uses a
bot's token that each copy of SC Locator calls Discord with when you
press **Import...**.

## What you need

- A Discord server where you can manage channels and roles.
- A Discord account to own the bot application, at
  https://discord.com/developers/applications.

## 1. Five channels

Make five text channels, for example:

| channel | what goes in it | members may post |
|---|---|---|
| `#scl-waypoints` | waypoints | no |
| `#scl-surveys` | traces (railways, lakes, buildings...) and height maps | no |
| `#scl-reports` | body and place reports | no |
| `#scl-maps` | maps to print | no |
| `#scl-links` | each member's link code, posted by hand once | yes |

The names are up to you. The first four are filled by SC Locator only,
through webhooks; nobody needs to type in them. Close them to members'
own messages so they stay tidy: in each one's **Permissions**, for
`@everyone` (and any role that would allow it), set **Send Messages**,
**Send Messages in Threads**, **Create Public Threads** and **Attach
Files** to deny. Webhooks still post: channel permissions do not apply to
them. Leave **View Channel** and **Read Message History** on, so members
can see what was shared.

`#scl-links` stays open to members: it is where each one posts their link
code (see 5). Chat there does no harm.

Chat or pasted text in the share channels would not break anything:
SC Locator only takes posts carrying its own file. But every message is
read on each Import, so a busy channel makes Import slower.

## 2. A webhook in each share channel

For each of the four share channels (not `#scl-links`): hover it > gear (Edit Channel) > **Integrations** >
**Webhooks** > **New Webhook**, name it (say `SC Locator`), then **Copy
Webhook URL**.

A webhook URL is a password: anyone who has it can post in that channel.
Give the URLs only to members, privately.

## 3. A read-only bot

1. https://discord.com/developers/applications > **New Application**, for
   example `SCL Reader`.
2. **Bot** tab:
   - **Reset Token** and copy it. Discord shows it once.
   - Under **Privileged Gateway Intents**, turn **Message Content Intent**
     on, and **Save Changes**. Without it every post reads back empty.
   - Turn **Public Bot** off, so nobody else can add it to a server.
   - The **Bot Permissions** box on this page grants nothing; leave it.
     The permissions come from the invite in step 4.
   - The bot's name can be changed only about twice an hour. If Save
     says you are changing it too fast, put the name back and save the
     rest.
3. **Do not** use your own Discord token, or a bot that already runs
   something else: this token goes to every member.

## 4. Invite the bot with read rights only

1. Same application > **OAuth2** > **URL Generator**.
2. Scopes: **bot**.
3. Bot permissions: **View Channels** and **Read Message History** only.
4. Open the URL, pick your server, **Authorize**.

That gives the bot a role with those two rights across the server. To
keep it to the five channels: **Server Settings > Roles** > the bot's role
> take **View Channels** off; then in each of the five channels'
**Permissions**, add the bot's role and allow **View Channels** and
**Read Message History**. The bot must be able to read `#scl-links` too,
or nobody's link code is found.

## 5. Hand out the settings

Send each member, privately:

- the four webhook URLs, saying which is which;
- the link to `#scl-links` (right-click it > **Copy Link**);
- the bot's token (the **read token**).

Each member then, in **Settings > Discord**:

1. pastes the four URLs, the `#scl-links` link (in **Links**) and the
   read token;
2. clicks **Link account**, and posts the code it copies (`SCL-LINK ...`)
   as an ordinary message in `#scl-links`, **from their own account**,
   and leaves it there;
3. clicks **Test connection**. A good result has four "webhook ... works"
   lines, "Read token: bot '...'", five "#... can be read" lines and
   "Your account: linked as '<their name>' (your code is in
   #scl-links)".

Codes posted in one of the four share channels before they were closed
still count.

The link code is how everyone knows a post is theirs: each post is signed
with the key the code belongs to, and Discord records who posted the
code. Deleting the code message unlinks that member, and their posts come
in as errors from then on.

## 6. Allow list and blacklist

Each member keeps their own, in **Settings > Discord > Who is let in**:
Discord user ids, one per line (Discord: **Settings > Advanced >
Developer Mode**, then right-click someone > **Copy User ID**). An empty
allow list lets every linked member in; the blacklist always wins.

## If something leaks

| leaked | do this |
|---|---|
| a webhook URL | delete that webhook, make a new one, send members the new URL |
| the bot token | **Reset Token** on the Bot tab, send members the new token |
| a member's key (their `discord.json`) | they delete their code message; a new key means installing afresh, or deleting `discord.json` and linking again |

`discord.json` (in `%LocalAppData%\SC Locator`) holds all of the above
and is never put in a backup.

## What Test connection tells you

| it says | it means |
|---|---|
| `no webhook URL` | that channel's box is empty |
| `not found: deleted, or the URL is wrong` | the webhook is gone, or the URL was cut short |
| `the token is wrong` | the token was reset, or mistyped |
| `Message Content intent is OFF` | step 3, the intent switch |
| `not allowed: give the bot View Channel and Read Message History` | step 4, the channel's permissions |
| `The webhooks are not all in the same server` | a URL from another server was pasted |
| `Link channel: none` | the Links box is empty: codes are only looked for in the share channels |
| `Link channel: not a channel link` | paste the channel's link (right-click > Copy Link), not its name. A webhook URL of that channel works too, though it needs no webhook |
| `The link channel is not in the webhooks' server` | the link is to a channel on another server |
| `your link code is not in the last 500 messages` | not posted yet, or buried under newer posts: Import still finds it there, but posting it again lets the test see it |
