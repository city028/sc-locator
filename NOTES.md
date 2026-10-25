# SC Locator — findings

Working notes from building and debugging this against the live game.
Everything below was verified in Star Citizen, not assumed.

## The debug HUD

Enable in the console (`~`) with **`r_DisplayInfo 2`**. It resets every time the
game restarts.

The HUD prints several position rows in **different reference frames**:

| row | frame | units |
|---|---|---|
| `Zone: ObjectContainer-… Pos:` | ship / building interior | m |
| `Zone: <body> Pos:` | planet | **km** |
| `Zone: SolarSystem… / Root Pos:` | system | km |
| `CamPos Planet Zone:` | **planet, metres, full precision** | m |

The `CamPos` row is the one to use. Reading the interior or system rows
silently reports a position in the wrong frame — the interior row would place
you ~20 m from the planet core. The parser identifies it by carrying both
"Planet" and "Zone".

**On foot the `CamPos` row can vanish** (microTech, 2026-10-11, walking out of
a ship). The `Zone: <body> Pos:` row stays, in the same planet frame, in km to
four places — 0.1 m, plenty for a waypoint. Its |pos| − Radius agreed with
ElevationUnderCam + Altitude to 0.07 m. It is used only when `CamPos` is
missing, and is told from the ship, container and system rows by its name
matching the `Planet:` row (or the body table, if that row is unread).

`Planet:` and `Radius:` rows exist and are authoritative. Verified identity:

```
|CamPos| - Radius == ElevationUnderCam + Altitude     (matched to 0.0 m)
```

Body names vary in form for the same body — `nyx1`, `OOC Stanton 4 Microtech` —
so they are canonicalised before use.

## The HUD block is pinned to the top-right corner

How far down the `CamPos` row sits depends on how many rows precede it, and
that **changes with location and graphics settings** (Nyx showed an extra
GI/Reflections line that microTech did not). A region calibrated in one place
silently stops working in another.

So the capture region snaps to the top and right screen edges rather than
trusting measured offsets. OCR also drops the last glyph or two of each row,
which truncates the final coordinate if the right edge is not at the screen
edge.

## OCR is unreliable and fails in specific, repeatable ways

All observed live, all handled:

- **Bright backgrounds lose the fix, and no global cut-off can fix that.**
  The HUD is light text and a sunlit planet surface is light too, so no single
  brightness value separates them — the old threshold inverted the bright
  ground into a solid black block that swallowed whole rows.

  Text is now found by **local contrast**: each pixel is compared with the mean
  of its own neighbourhood (`contrast_radius`), and counts as ink if it is
  `contrast_cut` brighter. Background brightness then stops mattering. Measured
  on ten frames captured over Euterpe:

  | | fixes | with ground height |
  |---|---|---|
  | global threshold ladder | 1/10 | 0/10 |
  | local contrast | 8/10 | 6/10 |

  The ladder is kept, now over `contrast_cuts`: local contrast is not
  unconditional, and a glaring background still needs a lower cut.
- **Decimal points vanish**: `-261592703280` is `-261592.703280`. Coordinates
  always carry six decimals, and nine digits exceeds the largest body radius,
  so a long digit run with no point is unambiguous.
- **Zeros read as 6 and 8.** The `Radius` row is the worst affected — one
  session produced 13 different values for a true 1,000,000.

  **For a body in `BODY_RADII`, the table decides and the reading is ignored.**
  Voting on noise only picks the least bad of several wrong answers: a session
  on Euterpe settled on 213,601 m against a true 213,000 m and carried that
  601 m error into every height. The table is exact; the reading never is.

  The table value is applied whether or not the position agrees with it, so the
  plausibility gate judges each fix against the body's real size. Conditioning
  it on agreement left an unreadable `Radius` row falling back to
  `default_radius`, whose window spans 100 km–50,000 km — which is how a fix
  4,000 km from Clio was once accepted and saved as a waypoint.

  **The table can itself be wrong, and the heights can say so.** Crusader's
  entry reads 7,450,000 m where the HUD prints 7,450,010 m, and the HUD's own
  height values satisfy the identity exactly against 7,450,010 and miss by
  exactly 10 m against the table. Ten metres on a 7,450 km body is nothing to
  navigate by and everything to the identity check, so making the table
  authoritative silently threw away every height reading on Crusader.

  So where the two height values agree with the frame's radius and disagree
  with the table's, **the reading wins** — that is evidence, where preferring
  the table is assumption. A frame that cannot tell them apart leaves the
  table in charge, which is what guards against the noisy readings the table
  was introduced for.

  For a body the table does not know, the old rule still applies: readings that
  the position contradicts are dropped, then the roundest wins (game radii are
  round numbers; OCR noise destroys roundness). Readings are collected for
  known bodies too, since they are the only source if a table entry is ever
  distrusted.
- **Colons read as `i`, `l`, `1`, `;`, `.`** — `Planet:nyx1` became
  `Planetinyxl`.
- **Letters read as digits**: `S0` repairs to `50` and would shift every later
  coordinate, so a number must be ≥3 chars and fill most of its token.
- **Rows fail independently.** The `Planet:` row is not the `CamPos` row and
  need not survive the same threshold, so a frame can carry a perfect position
  and no body name at all. That parses as `"unknown"`, which is not a body.

  Worse, the name itself OCRs badly. The repair table catches `nyxl` and
  `micr0Tech`, but not `microTecn` or `rnicroTech` — and each unrepaired
  variant is a *different body* to anything that filters by body.

  Both emptied the waypoint panel whenever the view changed in flight, and a
  save during one of those frames would have been filed under a body that does
  not exist. So **a new body name must repeat for `PLANET_SWITCH_FRAMES`
  frames before it is believed**: a garbled name rarely repeats identically, a
  real body change always does. Keeping the old body also keeps its agreed
  radius, and with it the height, instead of dropping to `default_radius`.

  Confirmed in the wild — a live session logged `planet row unread - keeping
  clio` while the threshold ladder was churning between 150, 120 and 190.

  **Keeping the body means keeping its radius too.** A name that does not
  resolve makes `parse_readout` fall back to `default_radius` and mark it
  unknown — and an unknown radius widens `plausible` from 0.9x–5x the body to
  100 km–50,000 km. Restoring the name without the radius therefore let
  through exactly the readings that gate exists to reject: a fix 2,712 km from
  Clio's centre was saved as though a 1,000,000 m body made it reasonable,
  giving a waypoint with a 1.7 million metre height. Only the fallback is
  replaced, never a radius actually read from the HUD — the Radius row is
  independent of the Planet row and is often right when the name is not.

  Both faults reached the saved file before they were noticed, which is the
  real lesson: a bad frame does not announce itself, it just writes a waypoint
  that looks ordinary until the numbers are read. `h` far outside a few
  thousand metres, or a `planet` that is not a real body, are the tells.

- **The body name is the worst-read row of all.** Of the names logged up to
  2026-10-14, 770 distinct ones in 2,631 readings matched no body: `pyra5`,
  `pyroa5`, `pyro55` for Pyro V, `gocstanton1hurstan` for Hurston,
  `gocstantonlaarie1` for Arial (`OOC_Stanton_1a_Arial`). An unmatched name
  used to become the body, even as the first reading of a run. Now a name the
  table cannot place goes to `fuzzy_body`: the body whose name it holds at a
  similarity of 0.8 or more, 0.05 clear of the next, in the system the name
  itself is written in (`py...` for Pyro, a damaged `stanton` for Stanton).
  Pyro's planets are printed by number, so their letters cannot tell Pyro I
  from Pyro V (`pyroa5`: 0.89 against 0.91) and the first digit 1-6 decides.
  "(Load Complete)" runs into the name and once matched the moon Adir, so the
  name is cut where it starts. That recovers about 2,260 of the 2,631. A name
  still unmatched is never believed: it counts as an unread row. Changing body
  still takes `PLANET_SWITCH_FRAMES` identical readings, so a rare wrong match
  changes nothing. A body missing from `SYSTEM_BODIES` now reads as unknown
  until it is added.

## Hotkeys do not work, and will not

Star Citizen (Easy Anti-Cheat) blocks other processes from receiving keyboard
input while it has focus. Tested exhaustively:

| approach | result |
|---|---|
| pynput keyboard hook | blocked |
| `GetAsyncKeyState` polling | blocked while game focused |
| `RegisterHotKey`, normal | registers, never delivers |
| `RegisterHotKey`, elevated | registers, never delivers |

Confirmed not to be a keyboard or key-code problem: with the game focused, no
key is seen; with any other window focused, every key reads correctly. The
whole chain (register → real keystroke → handler) works outside the game.

**Do not pursue this further.** Defeating the anti-cheat input block is both
fragile and not worth doing. Use the pin.

Reasonable objection, raised more than once: the NVIDIA overlay answers
`ALT+Z` while the game is focused, so the block is clearly not absolute. It is
not — NVIDIA ships a kernel-mode component and is explicitly allowlisted by the
anti-cheat vendors. Nothing a user-mode Python process can do reaches that
position, and all four approaches above were user-mode.

The bindings are still configured and registered, as a best effort: they work
whenever the game does *not* have focus, which covers most waypoint admin.
`panel` defaults to `<alt>+7` rather than `<alt>+8` only to avoid colliding
with the `save` binding already in `locator_config.json`; two actions on one
combo silently collapse into whichever registers last, so that case is now
logged.

## Heights are only trusted when the HUD contradicts itself nowhere

The `Radius:` row also carries `ElevationUnderCam` (terrain height over the
reference sphere) and `Altitude` (height over that terrain). Height over
*terrain* is the useful one in flight: landed on Nyx, the HUD reads 407.6 m
over the sphere and 13.7 m over the ground.

Unlike `Radius`, neither has a roundness heuristic or a lookup table to fall
back on, so there is no way to tell a good reading from a mangled one in
isolation. The identity is the whole defence:

```
|CamPos| - Radius == ElevationUnderCam + Altitude      (exact in game)
```

Both are parsed, the sum is checked against the position, and **both are
dropped if it fails** — a 0 read as a 6 breaks it by hundreds of metres. A
failed check never rejects the fix itself, only the heights, and logs once.

**A failed check refuses a save, though.** Dropping only the heights assumes
the heights are the mangled half, and nothing in the identity says which side
is wrong. At Everus Harbor it was the other side: the heights read 147.8 km
over the sphere on frame after frame while `CamPos` came back 822 km, then
1,030 km, then 107 km, and the save took the position at 1,030 km — a waypoint
117 km from the station, labelled `sphere` and otherwise indistinguishable from
a good one. The live readout can afford a wrong frame because the next one
replaces it; a waypoint is a durable record, so `save_waypoint` now refuses
while the last frame's heights contradict the position, and says so. The cost
is pressing save again a second later.

The refusal follows the **latest** frame rather than `HEIGHT_HOLD_SECONDS`.
The hold exists so the displayed line does not flicker, and a held height is
evidence about the frame it came from, not about this one — accepting a save on
a held height is exactly the hole this closes, because the position garbles
frame by frame while the heights sit still.

It is still evidence rather than a requirement: heights that do not parse at
all contradict nothing, and a save with no heights goes through as before.
Deep space and any body whose rows will not read have to stay markable.

**Height over the reference sphere is not a number a player can use.** A hangar
standing on the ground at microTech reads 1,771 m. So a save records the
terrain's own elevation and how far above it you were, and `list` reports those
instead — "on the ground", or "air 820 m" for one taken in flight. Waypoints
saved before this fall back to the old figure, labelled `sphere` so it is not
mistaken for a height above ground.

They are recorded only when the identity below held. A waypoint with no height
is better than one with an invented height.

**Lead with height above the ground, not with the ground's own elevation.**
`ElevationUnderCam` is the terrain *below* you, which is beside the point once
you are any distance up: 900 km out from Calliope it reads 55 m while your
altitude reads 955 km, and a line that led with the 55 m put the wrong number
in front of the reader.

**Hold a believed height for a few seconds.** The identity check fails on some
frames and passes on others, and dropping the heights the instant one failed
made the line alternate between two different quantities several times a
second — "55 m" one frame, "955 km over reference sphere" the next — which
reads as the altitude itself jumping about. Holding for `HEIGHT_HOLD_SECONDS`
rides out the failures, and because both forms of the line now lead with the
same quantity, the fallback differs by metres rather than by a factor of
17,000.

**A large altitude is not a mistake.** Stations in low orbit, asteroid bases
and other structures are ordinary places to mark -- Port Thresler sits 124 km
above microTech -- so the height is reported plainly rather than flagged. An
earlier wording called anything over 25 m "air", which read as a warning about
a reading that was in fact correct.

Deep space proper is a different matter: see "Still open".

Heights need a shorter dropped-decimal rule than coordinates. They carry the
same six decimals but are far smaller, so an altitude under 10 m leaves only
seven digits once the point vanishes (`13710187` is `13.710187`) and never
trips the nine-digit coordinate rule.

**Match labels on the part that survives, not the whole word.** The outer
letters are the ones that go. One session produced `ElevaticnUnderCam`,
`ElevaticnUndegCam`, `Glevationundercan:.` and `ElevationUnderGam:` — E read as
G, C as G, m as n, and in seven frames of ten the *o* of "Elevation" as a c.
Pinned to the full label, elevation went unread on every frame, so the live
readout showed no ground height at all while `Altitude` was sitting there
legible. The anchor is now just `levat`, which survived all of them.

The same applies to `Radius`. Its digits garble into `Zi3666.G6G6G@`, so
`find_region` corroborates that row by its label rather than by a parseable
number — demanding a number meant it located nothing. Quoted HUD text in a chat
window carries no Radius row at all, so the label alone still rules it out.

Reach the number over `[^\d]` rather than a class of plausible separators.

**Elevation can be negative; keep its minus.** Ground below the reference
sphere -- a frozen sea on microTech, 6 m over the sphere while standing on
the ice -- prints a negative `ElevationUnderCam`. Skipping `[^\d]` to reach
the digits passed over the minus as if it were a separator, so -2.25 + 8.25
read as 2.25 + 8.25 and the identity failed on every frame: no heights shown
and every save refused. The skip is now lazy and the number may start with a
minus. Altitude is never negative, so it is left as it was. Fixed in v1.0.1.

Watch the separator class when matching these labels. A colon also OCRs as
`1`, so a pattern that allows whitespace *before* the separator lets the class
eat the value's first digit: `Altitude 13.710187` silently reads as `3.710187`.
A real colon sits flush against its label, so match it there and nowhere else.

## Do not OCR when the game is not running

Each miss costs one Tesseract process **per threshold in the ladder**, not one
per tick — four, by default. One idle session logged 2,365 misses, so roughly
9,500 Tesseract processes against an empty desktop. That churn is the likeliest
cause of the heap corruption (`0xC0000374`) Tesseract was seen dying of.

So the capture loop looks for `starcitizen.exe` first and skips the grab
entirely when it is absent, polling at `idle_interval` instead. Verified
against a live process list.

This fails **soft** on purpose: an unreadable process list returns "cannot
tell", which is treated as running. Only an explicit "not running" idles the
loop, so a detection fault can never stop the program working — it can only
make it slower.

The status it produces is worth having on its own. "No game" and "game up but
no HUD" used to look identical; only the second one is something you can act
on.

## Star systems

The HUD does name the system, but not where you would expect. The planet zone
row is written `Zone: OOC_Stanton_4c_Euterpe` ("OOC" is Object Container), and
"Stanton" survived OCR in every frame of a ten-frame sample. The `Planet:` row
carries the same name and returned "Stanten" every single time, o read as e --
so the row already being parsed is the wrong one to take it from.

Two rows that look promising are not:

- `Zone: SolarSystem_871500387367` is an instance id, not a name, and it
  differs between sessions. Useless for identifying a system.
- `Current player location : FOB Abandoned Stanton4c FOB2` has the system in
  it, but its label garbles badly ("logatiogy", "locazion").

And it is not always there at all: Nyx prints `Zone: nyx1`, with no system
anywhere on the row.

So **the body-to-system table is primary and the zone row is the fallback**,
for bodies added in later patches rather than for the ones already listed.
Every body in `BODY_RADII` has an entry, which a test enforces. Waypoints saved
before systems existed are given one on load, by table lookup on their body; a
body the table does not know is left without one rather than filed under a
system it may not be in.

## A body's zone reaches a very long way

`plausible` used to require a position within five radii of the body, and an
unread radius widened that only to 50 Gm. Both were guesses about how far a
zone extends and both were wrong by orders of magnitude: a real reading
**55.85 Gm from Crusader** — whose own height values agreed with it to 0.000 m
— was thrown away, leaving no fix at all across a vast stretch of space.

That cuts both ways, and the other side needs its own answer: if a zone
reaches that far, then being inside one says almost nothing about where you
are. "stanton, crusader" is a useless thing to print at 55 Gm.

So there is a **space line**, `space_line_m`, 100 km over the reference sphere
by default -- the Karman line idea, an arbitrary height that makes the answer
useful. Above it the readout says `In Space near <body>` instead of naming the
body as your location. The nearest body is still named, because whose zone you
are in is the only bearing available out there.

It is measured over the reference sphere rather than over terrain, because the
sphere height can be worked out on every frame; a line that moved with whether
the HUD's height values parsed would flicker between "at the body" and "in
space".

**This line changes what is printed, not where a waypoint is saved.** A
station in low orbit keeps its latitude, longitude, orbital marker and height,
all of which are useful and correct there.

A second line further out, `far_line_m`, *does* decide the frame a save goes
to -- see "The planet frame turns; the system frame does not". Two lines, two
jobs: this one is about what to call where you are, that one about which frame
the place holds still in.

There is no proximity ceiling now. The floor still says you cannot be inside
the body, and one absolute ceiling catches magnitudes no coordinate in the
game reaches. Readings far from a body are corroborated by the height identity
instead, which is evidence rather than an assumption about geometry.

This also corrects an earlier conclusion recorded here: a waypoint 2,712 km
from Clio's centre was called an implausible position. It was not — that
distance is ordinary. What was wrong was its radius, which had fallen back to
`default_radius` because the body name did not resolve.

### What stations actually sit at

Measured here, not looked up: starcitizen.tools publishes no altitude for any
of them.

| place | body | over the reference sphere |
|---|---|---|
| Port Thresler | microTech | 124.24 km |
| Everus Harbor | Hurston | 147.79 km |
| Grim HEX | Yela | 373.38 km |

A working guess that 100-200 km covers every station in orbit held for the
first two and **failed on Grim HEX at 373.38 km** — 2.2 Yela radii out, because
it sits in the asteroid ring rather than in low orbit, and the ring is where it
is. There is no narrow band to key anything off. The 100 km line is a floor and
nothing above it is bounded.

**Grim HEX is fixed in Yela's rotating frame**, as Everus Harbor is over
Hurston. Five saves taken while seated in a hangar, spanning 5 min 16 s:

- the entire spread in `r` was **10 cm**; the largest separation between any
  two of the five, **15 cm**
- a non-synchronous orbit at that radius would have been unmissable — 7.2 km
  between two saves twelve seconds apart, on a two-hour period

The asteroids drift past the windows and the `Zone: Root` row changes
continuously, while the Yela-frame position does not move at all. The reason
is the one in the section below: Grim HEX turns with Yela, so it holds still
in Yela's rotating frame and sweeps in the static system frame. An earlier
note here explained it as Yela orbiting Crusader and carrying the station
along -- that was a guess, and nothing here has measured any body orbiting
anything.

That 15 cm is also **the measured precision of the whole chain** — capture,
OCR, parse — for an observer who is not moving, with every one of the five
readings corroborated by the height identity. Anything under a metre is noise
rather than movement. The terrain figure came back identical, 48.78125 m, on
all four saves that were kept.

## The planet frame turns; the system frame does not

This decides which frame a waypoint belongs in, and it was found by accident.
Two readings of the same jump point, 28 s apart, with the ship parked:

| | |
|---|---|
| xy angle | swept 0.589 deg |
| z | constant to 1.3 mm |
| `r`, the distance to Crusader's centre | constant to 1.7 cm |

A rotation about the spin axis turns the angle and leaves `r` and `z` alone,
which is exactly what that is: **the planet-local frame rotates with its
body**, at 0.02104 deg/s on Crusader -- a period of about 4.75 hours.

Two conclusions, both load-bearing:

**Planets do not orbit.** An orbit moves the body's centre, which would change
`r` from a static point. It changed 17 mm in 28 s. The star map shows the same
thing, and this is the measurement that agrees with it.

**The system frame is static.** Parked at the jump point, nine readings of the
`Zone: Root` row over 52 s: `moved 0.000 m`, every one, at the row's full
resolution of 0.1 m. Rotation would not have given a steady value at all --
25,542 km/s at that radius is 127,711 km per log interval against a 100 km
lock limit, so the lock would have broken on every frame. `SYSTEM_LOG_SECONDS`
exists to make that measurement repeatable.

So the two frames differ by a pure rotation about the spin axis, and a
waypoint is only valid in a frame where the thing it marks holds still:

| what | holds still in | why |
|---|---|---|
| ground locations | planet frame | they *are* the body |
| Everus Harbor, Grim HEX | planet frame | synchronous, so they turn with it |
| jump points, Lagrange stations | system frame | they do not turn with anything |

**The discriminator is co-rotation, not distance.** Distance is only a proxy
for it, and `far_line_m` is where that proxy is drawn: everything measured
that co-rotates is at or below 373 km, everything measured that does not is at
or above 6.66 Gm. A save above the line goes to the system frame, which is the
deep-space path that already existed for a ship parked between bodies.

Nothing below the line changed, which is the point. The planet frame keeps the
latitude, longitude, orbital marker and height that make a found location on a
planet findable again, and that is the part that was tested in game and works.

One more thing falls out of a centre that does not move. The jump point's `z`
reads 20,198,394,751.8 in the system frame and 20,198,395,410.921 in
Crusader's -- agreeing to 3 parts in 10^8, which places Crusader's centre at
`z` within about a kilometre of zero and confirms the spin axis is parallel to
the system frame's. The xy half needs two readings at different spin phases,
and would eventually allow converting a waypoint from one frame to the other.

## Orbital markers

The six OMs sit at the same points over every body, so which one is nearest a
waypoint is **geometry, not something to read or store**:

| marker | where |
|---|---|
| OM-1 / OM-2 | north pole / south pole |
| OM-5 / OM-3 / OM-6 / OM-4 | equator at longitude 0, +90, 180, -90 |

That matches the frame `to_latlon` already reports in — latitude `asin(z/r)`,
longitude `atan2(y, x)` — so the nearest marker falls out of coordinates every
waypoint already has. Computing it rather than storing it means waypoints saved
before this existed have one too, and it can never go stale against the file.

It is shown **with or without a fix**, because it is a property of the target
rather than a reading: deciding which OM to jump to is exactly what you do from
orbit, before any position is available. The distance beside it is how far is
left over the ground after dropping from that marker, which is the part that
decides whether the choice was right.

## The overlay

Star Citizen must be in **Borderless Window**; nothing can draw over exclusive
fullscreen.

**Staying on top.** `-topmost` set once was not enough: switching programs could
lose the overlay for good — another topmost window (Discord, Steam, GPU
overlays, Task Manager) or the game's own display change landed above it, or
Win+D minimized it, and with no taskbar button or alt-tab entry there was no
way back. The refresh loop now re-asserts topmost four times a second and
restores it if minimized. Restoring from minimized activates the window even
with `SW_SHOWNOACTIVATE` and `WS_EX_NOACTIVATE`, so focus is handed straight
back to whatever had it. Skipped while a dropdown menu is open, or the panel
would cover its own menu.

It lives in the **bottom-right** corner, for two reasons: the game shows little
there, and the capture region is top-right — an overlay inside it reads its own
text back as a fix.

Collapsed it is just a status pin:

| colour | meaning |
|---|---|
| grey | game not running |
| amber | game up, no HUD — press `~`, then `r_DisplayInfo 2` |
| green | fix live |
| amber | fix going stale |
| red | capture error |

- **Left-click the pin saves.** It is the one thing worth doing without opening
  anything, and it keeps working mid-flight.
- **Right-click opens the panel**, for naming, retargeting, renaming and
  deleting. Right-click again, or CLOSE, collapses it.
- Collapsed there is no room for a readout, so the result of a save appears
  beside the pin for a few seconds. Without it a click gives no sign of whether
  it worked, and that silence has cost whole debugging rounds.

Things that are easy to get wrong here, all found the hard way:

- Buttons must fire on **press, not release**, and not through `command`. A Tk
  button only fires once it sees press *and* release inside the widget, and
  over a fullscreen game the release goes astray: the press lands and the
  button never fires.
- The window background is set transparent while collapsed, which on Windows
  also makes those pixels click-through — so the pin floats with no card
  behind it and the space around it still belongs to the game. **Nothing
  inside the pin may be painted in that transparent colour.** The pin's inner
  hole was, which put a click-through hole in the exact middle of the pin, and
  clicks on its centre passed straight to the game.
- The window is **anchored by its corner, not its top-left**. Tk grows a window
  right and down, so a readout one line longer walks the panel off the right of
  the screen unless the anchored corner is reapplied on every resize.
  Reapplying it is not enough on its own (2026-10-14): a block opening after
  the panel (survey, target, report, rename) still grew the panel down past
  its corner. `wm geometry +x+y` only takes effect when Tk next idles, and
  `keep_on_top` ran first in the same tick; its `SetWindowPos` reports the
  window's old position back to Tk, which then kept the old top. Opening the
  panel escaped it only because the position was applied twice there.
  `reposition` now calls `update_idletasks()` straight after the move.
- **The panel must not resize at all.** A Tk `Label` sizes itself to its text,
  so the readout gaining a line when a target is set, or widening when a
  distance grows a digit, resized the whole window under the user — jarring,
  and it happened on ordinary frames. The readout now has a fixed box
  (`READOUT_COLS` × `READOUT_ROWS`) that the longest output it can produce
  still fits, and the status line has a fixed height. Tk clips silently, so a
  test asserts the worst case fits rather than trusting it. The panel now holds
  one geometry across a live session.
- The readout leaves out its own status and flash lines inside the panel: the
  panel has a line for both, and repeating them there changed its height frame
  to frame. Console mode still shows them.
- An empty waypoint list looks exactly like a fault, which is how filtering it
  to the current body was reported. The panel now lists **every** waypoint,
  grouped under `system / body`, with the body you are on first.
- Distance and bearing are shown only for the body you are on. Coordinates are
  planet-local, so a distance to a waypoint on another moon is not merely
  unhelpful, it is not computable; those rows show `--`.
- The readout leads with **where you are**, in the order it is wanted:

  ```
  Current location: stanton, euterpe
  Status: fix live
  FOB Abandoned Stanton4c FOB2
  LAT   -0.835
  LON +119.609
  H   457 m ground / 7.8 m up
  ```

  A stale fix is reported on the status line rather than as a marker on the
  location: the location is where you are, not how fresh the reading of it is.
  The status line is the only place the overlay explains itself now, so every
  health it can report has a caption and a test fails if one does not.

  The third line is the game's own `Current player location` row. Its label
  garbles every time ("logatiogy", "logation", "locazion"), so the match
  anchors on "player" and skips the next word whole.

  The value is not clean either, which only showed up once it was on screen
  for a while: **twelve frames of one unmoving location produced six different
  strings** — "NewBabbage" read as "KewBabbage" and "RewBabbage", "LZ" as
  "12", one reading as "Heyfphhrge", one with a stray leading ";". The line
  flickered between misreadings of a name that had not changed.

  Two things fix it. The row says the name twice, the second time in brackets
  (`Stanton4 NewBabbage (LZ : Stanton4 NewBabbage)`); only the first is kept,
  which halves the text that can garble and collapsed four of those six
  readings into one. And a new name must repeat before it replaces the one on
  screen, the same rule the body name uses — a garbled name rarely repeats
  identically, a real move always does. Two frames rather than three, because
  moving between places is far more common than moving between bodies.
- **"In Space" is only said when it is true.** Between bodies and "you forgot
  `r_DisplayInfo 2`" both produce no fix and used to look identical. Two or
  more debug-HUD-only rows being readable means the HUD is up, so no position
  means no body in range; otherwise the HUD is off and it says so. The old
  no-fix message sent you to `calibrate`, which is almost never the reason.
- The readout is two blocks with a rule between them, and another rule before
  the waypoint list: where you are, where you are going, what you have saved.
  Each block has its own fixed size (`LOCATION_ROWS`, `TARGET_ROWS`), for the
  same reason the whole readout did — a block that grows a line resizes the
  window.
- The target block is **never empty**. It has a fixed height and a rule above
  it either way, so returning nothing left a gap that read as a fault rather
  than as nothing to show. Without a position it names the target anyway: the
  target is not a reading and outlives the fix.
- **TARGET is a toggle**, and its label says which way it will go — it reads
  UNTARGET when the selected waypoint is already the target. Untargeting is
  the same thought as targeting and the panel has enough buttons; the label
  is what stops the gesture being hidden. The button is sized for the longer
  word so swapping it cannot resize anything.
- Group headings are lines in the same Listbox, so selection has to skip them:
  a heading is recorded with no name, and acting on one does nothing. The list
  is longer than the waypoint count now, hence the scrollbar — without one
  there is no sign that anything is below the fold.
- **The game only shows a mouse cursor while `F` is held**, which is a game
  default and cannot be changed. So every click on the overlay arrives with F
  down, and the click moves focus here — which sends those held keystrokes
  into whatever Tk widget has focus. Three consequences, all found in game:
  - A quick save that read the name field named a waypoint
    `fffffffffffffffff` (plus a trailing backtick, the console key). The pin
    therefore **auto-names and never reads the field**; the panel is where a
    name is chosen deliberately.
  - No text widget may hold focus by default. The panel opens with focus on
    the list, not the name field, or it fills with f's before it is wanted.
  - Backspace is not reliably available either, since clearing the field means
    holding F, which refills it. Hence the `X` button beside the field: it has
    to be possible to clear it with the mouse alone.
- Typing a name needs keyboard focus, and taking it takes focus off the game.
  The panel is therefore for when you are parked or alt-tabbed; the pin is what
  works in flight. Once the panel is open the game no longer has focus, so F
  should no longer be needed to keep the cursor — release it before typing.
- The cursor must be free to click at all (alt-tab, or release it in game).

Editing waypoints from the panel rather than the CLI also fixes a real bug:
a running overlay holds the list in memory and rewrites the whole file on every
save, so a CLI rename during a session was silently discarded. Both now go
through the same `add` / `rename` / `delete` helpers.

## Only one overlay at a time

Two copies ruin each other quietly, and neither symptom points at the cause:

- The second gets **no hotkeys at all** — `registered 0 hotkeys, 4 refused` —
  because Windows refuses a combo another process already holds.
- Both draw in the same corner, so the readout **ghosts**: two sets of text on
  top of each other, which reads as a rendering fault rather than two windows.

A forgotten instance from an earlier test cost a round of confused screenshots
before the process list was checked. `run` now claims a named mutex and exits
with an explanation if it cannot; `--allow-multiple` overrides it. The claim is
held in a module global for the life of the process, because a mutex handle
releases when the last reference to it goes.

It fails open: if the claim cannot be made or tested, the answer is yes. A
second overlay is a nuisance; refusing to start at all is worse. Only `run`
claims — editing waypoints from the command line while the overlay is up stays
possible.

## Backups

`save_json` keeps the last `BACKUP_KEEP` versions of each file in `bak/`,
timestamped. Thirty, not ten: ten is one session's worth of saves, and a run
of the test suite once consumed almost the whole rotation.
One overwritten `.bak` gave a single chance to notice a bad write — which was
spent, twice, during development; the second time the rotation was what got the
waypoints back.

Two details matter and both were bugs first. The counter must be zero-padded,
or `-10` sorts before `-2` and pruning drops the wrong versions. And it must be
one past the **highest** in use, never the lowest free slot: pruning empties the
early numbers, and reusing one makes the newest backup sort as the oldest and be
deleted on the very next save.

## The tests must never touch the real data

`save_waypoint` writes to disk, so a test module that loads `sc_locator` and
calls it overwrites `waypoints.json`. This happened **three times** during
development. The third time only one backup in the rotation still held the
full set, and the next save would have taken it.

Two of the four test modules redirected `WAYPOINT_FILE` themselves and two did
not, which is exactly the kind of thing that gets forgotten when a file is
added — the module that destroyed the data had never written to disk until a
new test in it happened to call `save_waypoint`.

So every test module now imports `scl` from `tests/_harness.py`, which loads
the program and points `WAYPOINT_FILE`, `CONFIG_FILE` and `BACKUP_DIR` at a
temporary directory. The harness then **refuses to run at all** if any of them
still resolves inside the project. A test that cannot run is a nuisance; a
test that quietly eats the data is not.

## Debugging

`run --log FILE` records state transitions, clicks, key events and save
outcomes. Added because silence was ambiguous: "never worked" and "nothing
happening" produced identical output more than once, each time costing a
round-trip. Any new failure path should log on its first occurrence, not only
on transition from a working state.

A click that lands beside a button is logged with its widget, so it is
distinguishable from one that never reached the window at all.

A save refused because the HUD heights contradict the position logs the
numbers that disagreed (elevation + altitude against |position| - radius, and
the radius used), so a log shows which side was misread.

`--log` belongs to `run`: `sc-locator.exe run --log session.log`. Placed
before the subcommand it is taken as the command name and refused.

The `.exe` reads `locator_config.json` from its own folder, not the project
root. A build in `dist/` therefore starts with the default capture area, not
the calibrated one: copy the calibrated `region` across (or run
`calibrate --find --save` from the `.exe`) after building. An uncalibrated,
wider area pulled in more HUD text and lost the `CamPos` row noticeably more
often (2026-10-10).

## Deep space

Planet-local coordinates exist only inside a body's zone, so a ship parked
between bodies could not be marked at all. The HUD prints a system-frame
position on two rows:

```
Zone: SolarSystem_871500387367 Pos: 22487898.6236km 37081092.2270km -4126.10m
Zone: Root Pos: 22487898.6236km 37081092.2270km -4126.10m
```

The units differ **within one row**, and not consistently: the third value was
metres near Euterpe and kilometres further out. So the suffix has to be read
per value rather than once for the row.

Two more things this row does, both found in flight rather than in the first
sample:

- **The values run together and the minus goes with the separator.** The same
  negative value was captured as `km-~73.0260km`, `km-+73.5125km` and
  `km--74.9744km`. Reading the sign from one character lost it and turned -73
  into +73 — which then made the two rows look like they disagreed, so the
  frame was thrown away. Reading it from the whole run before the digits took
  the parse rate from 4 frames in 14 to 8.
- **`Pos` reads as `Pas`**, often enough that the row pattern has to allow it.

### The two rows are not a cross-check

They look like free corroboration and are not. They are the same glyphs in the
same font, so they garble the same way. Across ten captured frames:

| | |
|---|---|
| rows agreed | 2 of 10 |
| frame00 | both read `37681092`, true value `37081092` — identical and wrong |
| frame07 | both read a truncated `4177km` |

Both agreements were on wrong values. A gate built on them would have passed
exactly the bad frames and rejected the good ones. Agreement is therefore used
only to **reject** a frame where the rows differ; it is never taken as evidence
that a reading is right.

### Time is the check that works

The true value moved smoothly — `37081092 → ...091 → ...090 → ...089` — while
a misread digit jumps 600,000 km. So a reading is believed only once it is
consistent with one already believed, and from nothing it takes
`SYSTEM_RELOCK_FRAMES` agreeing frames. Three, not two: one digit can be
misread the same way twice running, and a pair of matching errors is
indistinguishable from a pair of truths.

`SYSTEM_LOCK_KM` has to be tight. Measured flight ran about 2.4 km per frame,
and a first guess of 2,000 km let through a frame whose third value read
+882 km among neighbours at -77 km, putting the position 960 km out. At 100 km
that frame is rejected and ordinary flight still passes with forty times the
margin.

Quantum travel outruns the limit and drops the lock, which costs nothing:
**the game stops updating the Root row for the whole jump.** Euterpe to
Hurston (2026-10-14) read `22488274.2679km 37081070.9047km 614.3521km` for
226 s while the screen kept changing and the zone went Euterpe, microTech,
Crusader, Hurston. The planet-frame row kept updating throughout. On arrival
the Root row was mostly unreadable (11 of 11 frames failed in one stretch) and
misreads interleaved with good frames held off the three-frame relock for
minutes. A tracker that followed a jump's straight line through the Root row
was built and removed again: there is nothing on that row to follow.

What the star map does instead is under "Where you are on the star map".

Marking a point additionally requires the position to have held still, because
out there nothing else can contradict it: there is no radius, no terrain height
and no round number to check against, unlike every planet-local reading.

### What a deep-space waypoint has

No latitude, longitude, orbital marker or bearing — there is no surface to
measure over and no north to bear from. Only a straight-line distance, which
is what flying back to a ship needs. Distances run to tens of millions of km,
so `fmt_dist` carries the units the game itself uses out there: m, km, Mm, Gm.

The megametre tier came later, from the readings in between. Without it
everything from a Crusader mining base to a Lagrange station printed as five-
and six-digit kilometres -- `61,910.59 km`, which reads as noise where
`61.91 Mm` reads as a distance. The suffixes follow the HUD's own casing, as it
prints them on its position rows (`Pos: 22487898.6236km`).

## Where you are on the star map

A green `+` marks you on both maps, and one **follow me** switch beside
**HUD overlay** keeps both on you. It never changes the kind of view: the
system view stays the system view, a body view stays a body view (turned so
you face the viewer, and moving to the next body when you reach it). A drag
or zoom leaves it on -- the drag becomes an offset from you that travels with
you -- and ticking it again re-centres. Going elsewhere on purpose (another
system or body, SHOW ON MAP, the orbital-marker views, Go to) turns it off, or
it would pull the view back within a second.

**At system scale you sit on your body's pixels** (about 240,000 km a pixel
for Stanton), so the body's dot covered the cross; it is drawn last, labelled
YOU. Ordinary flight moves it a pixel in about three days; only a jump moves
it visibly.

**Where the system position comes from.** The Root row freezes for a whole
jump (see "Deep space"), so with a planet-frame fix under 10 s old on an
analysed body the cross is placed from that: the body's centre plus the fix
turned by the body's spin at the fix's own time (`starmap.system_point`). It
is the spin analysis's own model; parked 19 Gm out it matched the Root row to
1.5 km in distance and 1.9 km in height, and in angle once the spin was
re-anchored (see "The spin drifts"). It works only in the zones of analysed
bodies; elsewhere the Root row is used. A step faster than light is a misread
fix -- Crusader once read 77.6 Gm out among readings of 19.1 Gm -- and is
skipped, up to twice in a row.

Each `system pos` log line ends with the last planet fix and its time, which
is what the two were checked against each other with.

## Body analysis

Landed on a body you turn with it, so the planet-frame position holds still
while the **system-frame** position sweeps a circle about the spin axis at
exactly the body's rotation rate. The ANALYSE button samples that position and
fits the circle. One landing gives:

- the rate and period, and the spin axis (the circle's plane)
- the body's centre in system coordinates: the circle's centre, moved along the
  axis by the ship's height above the equator, which the planet-frame position
  supplies
- the sun's declination and one phase anchor (the longitude the star was over at
  a known moment), which is all it takes to place the sun anywhere on the body
  at any time -- but the anchor drifts, see "The spin drifts" below
- the radius and diameter, from the HUD's own `Radius` row

The maths is in `spin_analysis.py` and touches no I/O. Results are written to
`bodies.json`, one record per body, with a record kept if a later run says
nothing.

**What it assumes, and how each is checked.** The star is at the system
frame's origin (confirmed by the user, 2026-10-05). The planet frame's `+z` is
the spin axis and the body turns counter-clockwise about it: true of Crusader
and now of Hurston, but a mirror-image body fits the same circle, so a single
landing cannot test it. The centre the assumption implies is compared with the
system plane instead -- bodies measured sit on it to within a kilometre -- and
the report says so when it is far off. Planets do not orbit, so the star's
direction is fixed and a solar day is exactly one rotation.

**What the mirror ambiguity costs.** It puts the centre on one side of the
circle's plane or the other, about twice the ship's height above the equator
apart. The star is so far away that this barely moves the sun: on the Hurston
run the two candidates differ by 0.0045 deg in declination and 0.06 s in the
time of sunset. So the sunset and sunrise at the analysed site are good
whichever is right, and the assumption matters for the centre's coordinates
(a Lagrange point's name would need them). It also fixes which way longitude
runs against the rotation, which matters only when the record is used at a
different longitude from the one analysed -- at the analysed site it cancels.
A second landing at a different latitude settles the first, and one at a
different longitude the second, with no assumption at all.

**Sunset is the sun's centre crossing the horizon.** What you see is the upper
edge touching it, which comes earlier by the sun's angular radius. That is
degrees at these distances, so a minute or more, and the star's size is not
known, so it is not corrected for.

**It was checked against a real run before it was built into the app.** The
sixteen system positions logged while parked at Everus Harbor on 2026-10-05
give Hurston a 2.51 h period (+/-2% from 94 s), a spin axis of exactly the
system `z`, a centre at `y` 7 m, `z` -14.5 m, and a fitted circle radius within
7 m of the 1,035,726 m the planet-frame `x`, `y` give. That agreement is the
independent check the program makes on every run: if the circle is not the right
size for the body's own coordinates, the motion is not a plain spin and the
result is `moving`, not `rotating`.

**Four outcomes.** `rotating`; `static` (nothing moved by a metre in two
minutes, so no spin to find -- a body that does not turn keeps one face to the
star); `moving` (the circle is the wrong size, so probably an orbit as well);
and inconclusive, which writes nothing.

**Why it is quick.** The system position resolves to 0.1 m and the arc grows
with the ship's distance from the axis, so a few seconds is enough to see the
circle. What limits the rate is the timestamp, not the position: an OCR cycle's
latency jitters by a few tenths of a second, which is a metre-scale error along
the arc but none across it. So the circle is fitted on position alone and the
rate regressed on time, and it stops when the rate's standard error reaches 1%.

**Misread digits.** The settling that admits a system position only refuses
jumps over 100 km, so a digit misread in the middle of the number gets through
and drags a least-squares circle. The first pass is therefore a consensus fit
(circles through triples spread across the run, keeping the one most readings
sit on), then the ordinary fit on those, then one more rejection by residual.

### First runs through the button (Hurston, 2026-10-05)

Hurston came out `rotating`: period 8957 s (2.49 h), rate +/-0.91%, axis exactly
the system `z` (tilt 0.0008 deg), centre 26 m off the system plane, circle
radius 678,354 m against 678,335 m from the planet frame. It took 19 samples
over 152 s, not the 30 s minimum: a system position arrives only every 6-12 s,
and lag stretches that, so 1% needs about 2 minutes. Do not press STOP -- it
ends the run with no result.

Three things went wrong on the way, all fixed:

- **Stopped early, then discarded.** The run stopped on wall-clock time since
  the button, but the result is judged on first-to-last sample. At exactly 30 s
  the span was just under, so a run that was "precise" was thrown away as too
  short. Stopping now waits for the samples to span `MIN_SECONDS`.
- **The session was keyed to a misread name.** It started as
  `gocstanton1hurstan`; the reader corrected it to `hurston` later, and samples
  were only taken while the name matched, so none were taken at all. Samples are
  now taken whatever the name reads, and the record is saved under the held name.
- **Misread positions restarted the run.** The ship was parked, and the planet
  frame `y` read 198343.348 to a fraction of a millimetre on every good frame.
  The OCR drops digits from `x` now and then (`1804`, `48690`, `171595` for about
  648,000), and a single reading more than 3 m away restarted the run. It now
  takes 3 readings in a row **at the same new spot** (`STRAYS_TO_RESTART`): a
  moved ship stays where it went, a misread lands somewhere different each time.

**De-sync.** One run showed the system position frozen for 30 s (`moved 0.000 m`
five times), then jumping 19.7 km as the game caught up. The restart that
followed was right, since the frozen readings are stale. Nothing handles it
beyond that; if it turns out to be common, drop the frozen readings and keep the
good data either side rather than restarting.

**Crusader (2026-10-05), from the floating platform.** `rotating`: period 18,300 s
(5.08 h), rate +/-0.23%, axis exactly the system `z`, centre 1.9 km off the
system plane, circle radius 5,368,792 m against 5,366,936 m from the planet
frame (0.035%). It stopped after 30 s and 14 samples: the platform moves at
about 1.8 km/s against Hurston's 0.5 km/s, so the arc outgrows the 0.1 m
position resolution much sooner. That arc was only 0.6 deg, so the circle is
fitted from a short stretch and the centre is looser than Hurston's (26 m); a
2-minute run would tighten it. The axis is exactly `z` because every sample had
the same `z` height, not because of a fallback. 5.08 h agrees with the Z48 saves
(about 5.1 h) and replaces the 4.75 h from one 28 s pair, which was 7% short.
A gas giant needs no special handling: the run uses only the ship's planet-frame
and system positions, never the ground.

**ArcCorp (2026-10-05).** `rotating`: period 11,165 s (3.10 h), rate +/-0.96%,
axis exactly the system `z`, centre 218 m off the plane, circle radius 755,334 m
against 755,410 m from the planet frame (0.01%). 21 samples over 69 s, no
restarts. Stored so far: Hurston 2.49 h, Crusader 5.08 h, ArcCorp 3.10 h.

**microTech (2026-10-06).** `rotating`: period 14,847 s (4.12 h), rate +/-0.18%,
axis exactly the system `z`, centre 4 m off the plane, circle radius 680,230 m
against 680,225 m from the planet frame (0.0006%). 10 samples over 85 s, arc
2.06 deg, no restarts. The `Check` line did not fire, so counter-clockwise about
`+z` holds for a fourth body. Stanton is complete: Hurston 2.49 h, Crusader
5.08 h, ArcCorp 3.10 h, microTech 4.12 h.

The system position repeated (`moved 0.000 m`) six times in the run, for 6 to
13 s each, which cost samples (10 instead of about 14). Under `FreezeWatch`'s
20 s threshold, so nothing was logged about them. Looks like the Root row
refreshing in bursts, not a stuck capture, but that is not confirmed. Still
fitted cleanly. Sunset at the site was not compared with the sky.

**A frozen system position (ArcCorp, first attempt).** The position stopped
changing 9 s after ANALYSE and read `moved 0.000 m` for over 5 minutes; the run
hit its 5-minute limit and saved nothing. It was not called `static` because the
two readings before the freeze still moved. A retry minutes later was clean. The
player saw the number at the top of the screen change throughout. What the log
shows:

- No `fix lost`, `fix rejected`, planet-name garbling or contrast switching for
  the whole freeze. Elsewhere the OCR does one of these every few seconds, so
  perfectly clean, identical reads suggest the same pixels were read each time.
- Two freezes in this session (Hurston 30 s, ArcCorp 5+ min) began within 9 s of
  pressing ANALYSE, but three other presses did not freeze. Too few to call a
  pattern; the overlay click taking focus from the game is one guess.
- Candidate causes: the `mss` grab returning a stale frame; the Root row not
  updating while other HUD rows do; the game throttling after a focus change.
  Not settled.

`FreezeWatch` now writes one log line when the system position repeats for 20 s:
how many times the captured image changed meanwhile, and the Root row text the
reader saw. Image unchanged means a stale capture or a HUD that stopped drawing;
image changing means the row or the reader is stuck. It only logs; it does not
stop the run or warn on screen. Read it the next time a freeze happens.

**Clio (2026-10-06), a moon, and the false `moving`.** The system-frame path was
a clean circle of 336,962 m, which is Clio's 337 km at 0.15 deg latitude, so the
spin itself was measured. The planet-frame check gave 7,326,436 m for the same
circle, so the two disagreed and the run was called `moving`. The planet-frame
position was misread, and probably persistently: a parked ship's screen does not
change, so neither does a misread digit, which is why 16 samples within 3 m of
each other still carried it. The log showed the reader struggling throughout
(contrast cut flipping about 15 times, garbled planet name, `heights dropped`
with a 47,000 km height). The cause of the misread itself is not established.
Fix: a frame is no longer sampled while the HUD's own heights contradict the
position (`heights_conflict`, the same identity `|CamPos| - Radius == elev + alt`
that already vetoes saves). Frames with unreadable heights still pass, since
nothing contradicts them. Not tested against the game. The bad record was
removed from `bodies.json`.

**Clio re-run (2026-10-06, 17:41).** `rotating`: period 11,678 s (3.24 h), rate
+/-0.085%, axis exactly `z`, centre 5.9 km off the plane, circle radius 336,626 m
against 337,006 m from the planet frame (0.11%), 18 samples over 30 s. About
181 m/s at the equator, matching the 182 m/s drift in the earlier bad run's log,
so that run's system-frame circle was right and only its planet-frame check was
wrong. Labelled `moon` from the drop-down: the click works in game. It took four
minutes with three restarts while the ship was parked, with garbled planet-frame
positions (`pos=(288.0, 7856.0, ...)`, digits missing) and `fix rejected` lines.
Whether the heights gate helped cannot be told: the log prints only the first
`heights dropped` of a session.

**Likely cause of the misreads: light behind the `CamPos` row.** A bright cockpit
lamp sat behind the row at this spot. That is the bright-background failure
above, and it fits every symptom here: the same wrong position repeating on a
parked ship, `planet row unread`, the contrast cut flipping between values, and
the ruined first Clio run. Not proven, since the light was only noticed
afterwards and no second run was made with it removed. The analysis messages now
say `hold still, look at a dark area`. Open: three agreeing off-spot readings
count as "the ship moved", so a misread that repeats on an unchanged screen
restarts the run with the ship parked.

**Stalls in the system position.** On microTech, a second run read the system
position only every one to two minutes (jumps of 40 km, which is correct: about
290 m/s at 47 deg latitude). `FreezeWatch` stayed silent because it ignored
frames where the position did not parse. It now counts those, and its line says
how many frames were unparsed and what the Root row text looked like. Cause of
the stall still unknown; a bright background is the suspect.

### Places with no ground (2026-10-06)

ANALYSE works anywhere. With no body zone, or more than `space_line_m` (100 km)
above the body, it samples only the system position for about 60 s and stores a
**place**, not a body: `fixed` if most readings sit within 1 m of their median
(one wild frame does not count), otherwise `moving` with a speed and direction.
Park or dock first: a drifting ship reads as moving.

- Stored in `bodies.json` under `space <x>,<y>,<z> km` with `scope: "space"` and
  the median position. A run within 20 km of a stored place replaces it.
- The measurement cannot tell a jump point from a mining base from a Lagrange
  station: all sit still. That is what the **label** is for (planet, moon,
  orbital station, Lagrange station, jump point, mining base, PIO, other),
  chosen from a drop-down in the analysis block and written straight into the
  record. It survives re-analysis. It also works on planet records.
- Whether stations move in the system frame is not known yet; the first runs
  will say.
- Side effect: a point more than 100 km up near a body is space now. One test
  fixture used Everus Harbor (150 km up) as a landing site; it now raises
  `space_line_m` for itself.
- The game's `Current player location` text names a place but not its kind, and
  garbles; it is not used to classify.
- Not done: Lagrange points could be recognised from geometry (the star is at
  the origin and four bodies' centres are stored), and moon versus planet from
  a name table.

### The panel

The status line is at the top with a hairline under it; save and edit messages
replace it briefly, and the analysis progress goes into the analysis block, which
sits under that hairline and grows the window. The waypoint list and buttons stay
visible. After a run the block shows KEEP / DISCARD (the result is already saved;
KEEP only closes the block). The target block, with a compass arrow, shows only
while a target is set. The name field appears only for RENAME. SAVE auto-names.
Buttons are in two rows under the list, split by what they act on: waypoints
(SAVE, TARGET, RENAME, DELETE), then below a hairline the body (ANALYSE, REPORT)
on the left and the window (CLOSE, QUIT) on the right. The rename field opens
above the waypoint row.
The arrow is the same turn figure as the text, so it inherits the `HDG` problem
below: course over ground, not facing.

### The spin drifts, and is kept current (2026-10-14)

An analysis measures the rate over half a minute (Crusader's: 30.6 s), and a
small error in it adds up. Nine days on, parked at CRU-L4, the system-frame
position and the planet-frame fix agreed on the distance from Crusader's centre
to 1.5 km in 19.1 Gm and on the height to 1.9 km -- but the record put the
planet frame 51.5 deg off, the same on every line. That is a 0.33% rate error
over 9.2 days, and it put the star map's cross 16.6 Gm from the ship and the
sun times some 44 minutes out. The planet view's globe was turned wrong too.

So every frame that holds both positions re-checks the anchor
(`spin.measured_subsolar`, `State._recheck_spin`):

- a frame counts only when the two agree on distance from the centre and
  height along the axis (within 10 km, or 1e-6 of the distance) -- the two
  things the spin cannot change. A Root row frozen by a jump, or a misread,
  is off by far more. Not while the heights contradict the fix, nor from a
  fix that jumped: a dropped minus sign mirrors it at the same distance and
  height
- three frames in a row must put the star within 0.1 deg of each other
- the record is re-anchored and saved to `bodies.json` when it was 0.1 deg
  or more out, and its rate corrected from the old anchor when that is an hour
  or more back and the change is under 1%

First run, approaching Seraphim the same evening: `record -51.599 deg out`,
19 minutes after 51.51 was measured at CRU-L4 -- the extra 0.09 deg is the old
rate error over those 19 minutes, so the two measurements agree. The rate went
0.019672652 -> 0.019607844 deg/s: a day of **18,360.0 s, exactly 5.1 h**,
against the analysis's 5.083 h. A round number, so very likely the game's own.
It saved once and not again. microTech, 17 minutes later: `+11.075 deg out`
after eight days, a day of 4.1200 h against 4.1227 h -- round again.

**Not near the axis (2026-10-16).** The anchor is an angle around the spin
axis, and the centre is only known to some hundreds of metres (Nyx I's fit:
370 m). Close to a pole that is a large angle: 0.49 km from Nyx I's axis a
frame re-anchored the record by -42.8 deg. Frames within 1,000 km of the axis
(`SPIN_MIN_AXIS_M`) no longer count; at that distance 370 m is 0.02 deg.

## Height map (survey mode, 2026-10-14)

`ElevationUnderCam` is the ground's height over the reference sphere under
the camera, in flight as well as on foot, so the ground can be mapped without
landing. The ground under a sample is at radius + elevation from the centre.

**Grid.** A cube-sphere, equal-angle: the six faces of a cube pushed out
onto the body, each cut into n x n cells with n = round(pi/2 * r / 100 m).
A lat/lon grid was the alternative and squeezes its cells to nothing at the
poles. Equal-angle cells are 100 m at the middle of a face and 70.7 m along
its edges, on every body (measured: Hurston, Euterpe, Crusader). A body's n
is stored with its cells and fixed by the first sample, so a later radius
correction cannot shift cells already measured.

**What is recorded.** One sample per frame, only when the height came from
that frame and passed the identity check. The overlay holds the last good
height for `HEIGHT_HOLD_SECONDS`; recording the held value would copy it onto
ground flown over since. Nothing above the space line and nothing on a body
outside `BODY_RADII`. Cells keep a running mean and a sample count.

**Size.** Whole bodies at 100 m are out of reach -- Hurston is 1.26 billion
cells -- but only measured cells are stored. Saved compact (no indent: an
indented cell takes four lines), every 2 minutes during a survey and when it
stops.

**Gaps.** Cells are at most 100 m across, so samples 100 m apart skip some.
A gap-free strip needs samples under ~70 m apart. The map shows the gaps grey
rather than filling them: grey means unmeasured, and nothing else.

**First survey (microTech, around New Babbage, 2026-10-14).** 192 cells over
~50 km at ~200 m/s, so samples ~200 m apart: a dotted line on the map.
Heights -40 m to 1,488 m. No building-like spikes: every bump was 2-3 cells
wide and rose and fell over several samples, as terrain does.

**The ice sea between the city and the spaceport reads exactly -40.0 m**, for
~30 samples over ~12 km (confirmed by eye in game). Ground never holds a
height to the decimetre like that, so a run of identical heights marks a
water or ice surface. That is the lead for mapping shore lines.

**Second pass, over the city (2026-10-14).** 133 more cells: down from the
spaceport side (490 m), across the sea at -40.0 m again, then a loop on the
city side (22-155 m). There, neighbouring cells ~200 m apart never differ by
more than ~25 m, where New Babbage's towers would put jumps of 100 m or more
into a track that crossed them. So the HUD's ground looks like terrain, not
buildings. Confirmed: the loop was flown ~100 m over the rooftops.

**And directly, parked on a New Babbage roof (screenshot, 2026-10-14).** The
ship's radar altimeter (R-ALT, cockpit HUD) read 49 m to the roof, while the
debug HUD read `ElevationUnderCam: 21.437500 Altitude 1163.359750` -- the
roof stands ~1,114 m over what the HUD calls ground. Structures do not exist
for ElevationUnderCam, the whole city included: the height map shows the
terrain under a city, not the city. The debug HUD carries no radar altitude;
R-ALT is only on the ship's own HUD.

The same block has `XYPos: 701111.269927 563288.135477 Page: 4 (+Z)`: the
game's terrain seems to be paged by cube face too, with XYPos a position on
the face. Not used.

**A dropped minus sign gets past every check.** One sample landed at
longitude 140.3 instead of 39.7: x had lost its sign, which mirrors the fix
across the body at the same distance from the centre, so the radius gate and
the height identity both pass. Survey samples are now dropped when the fix
moved faster than `SURVEY_MAX_SPEED` (3 km/s) since the previous one.

**A sample about every 2 s, not every 1 s.** The city pass was flown at
~100 m/s and its samples came out 180-270 m apart (median 230 m), so a
recorded sample lands every ~2.3 s although `interval` is 1 s. Frames that
need the contrast ladder, or fail it, cost the difference. A gap-free strip
therefore needs ~30 m/s, not the 70 m/s first assumed.

**ElevationUnderCam high up (microTech, 2026-10-14, by eye).** It stays on
the HUD out of the atmosphere. A first report had it reading -40 there; that
was flying up and down over the ice sea, which is at -40 -- so it was the
ground, not a fallback. Corrected after the logged climb below. Near ~10 km
it seemed to change at random. A wrong value there would still satisfy the
height identity, since Altitude is derived from it, so survey samples are cut
off by height: `survey_max_height_m`, 5 km over the reference sphere,
measured from the position.

**The logged climb (microTech, 2026-10-14, 10:00-10:09).** Straight up from
ground at 108 m to 25 km, at ~50-80 m/s, with SURVEY on and every frame
logged (`survey h ... elev ... alt ...`). Every ElevationUnderCam that was
read, at any height, was the ground under the ship: 105-111 m, drifting a
little as the ship did, and passing the identity. No -40 and no random
values were read anywhere up to 25 km. What changes with height is whether
it reads at all, while Altitude on the same row keeps reading:

| height | frames | elevation read |
|---|---|---|
| 0-5 km | 59 | 83% |
| 5-7 km | 26 | 65% |
| 7-15 km | 98 | **0%** |
| 15-20 km | 53 | 36% |
| 20-25 km | 64 | 9% |

Between 7 and 15 km not one frame read -- the band where the "random number
changes" were seen by eye. Whether the HUD prints something unparseable there
or the OCR loses the value against the sky is not known: the raw row is not
logged. Either way nothing bad gets in -- what is read is right, and what is
not is dropped -- so the 5 km ceiling stays for a practical reason: below it
four frames in five give a sample.

Three frames in the climb failed the identity (two positions misread to
4,319 km and 51,691 km, one altitude to 59 km) and were dropped. The good
frame after the 4,319 km one was dropped as a jump, as designed.

**Speed test (Harper's Point, microTech, 2026-10-14, 10:24-10:30).** Three
legs at different speeds, ~2 km over the sphere. Spacing worked out from the
log times and the cells added, in order:

| leg | speed | a frame every | kept | a sample every | a new cell every |
|---|---|---|---|---|---|
| slow | 33 m/s | 2.1 s | 76% | 92 m | 135 m |
| medium | 107 m/s | 2.1 s | 50% | 450 m | 470 m |
| fast | 180 m/s | 1.5 s | 74% | 365 m | 370 m |

33 m/s still left gaps: a line crossing 70-100 m cells at an angle needs
samples closer than 92 m. The medium leg's 50% kept is more likely the light
than the speed.

**Low and slow (microTech, 2026-10-14, 10:41-10:43).** Flown at just under
20 m/s by the ship's indicator, ~650-820 m over ground. 1,738 m first to last
new cell in 98 s = 17.7 m/s, agreeing with the ship. 61 frames, one every
1.65 s; 52 kept (85%), so a sample every ~34 m. Cells there are ~75 m, and
18 of 22 steps between new cells went to the neighbouring cell: an almost
unbroken line. Five frames failed the identity (Altitude misread, e.g.
6,337 for 634) and two were dropped as jumps, each right after a misread.

Working the speed out from the cells needs care: matching log lines to new
cells in order runs out of cells early when most cells take two samples, and
that first gave 25 m/s here. Measure first to last over the whole survey.

Of 206 frames, 14 were dropped as jumps, and several of those followed
frames whose heights were unread. Such a frame still sets the position, with
nothing to check it, and the jump test measured from it -- so one misread
there cost the good frame after it. The test now measures from the last
position the heights backed (`checked_pos`).

So -40 is not a fallback value: it is the ice sea's level on microTech, and
a run of exactly -40.0 there is water. Other bodies' seas may sit at other
levels.

**Drawing (tkinter).** The globe was first all 24 x 24 tiles a face, grey
where unmeasured: 1,728 canvas polygons a frame for 3 measured tiles, 154 ms
a redraw while dragging, and the grid of squares did not read as a ball. Now
the body is one shaded ball (140 steps, about one colour shade apart, so no
rings when zoomed in) with only the measured tiles on it, 192 a face (8 km
on microTech), each the mean of the measured cells inside it -- each cell
counted once, so standing still does not outweigh ground flown over. Tile
corners are matched by position, so neighbouring tiles rise together across
a cube edge too and the mesh does not tear. 10-35 ms a redraw. The cost now
grows with the area surveyed, one polygon a tile in view. The height ramp's
low end is purple, not blue: blue vanished against the globe.

The Height map tab paints measured cells into a `PhotoImage` on a gnomonic
map around you; ~190 ms for 10,000 cells in view. Meridians are straight on
that map and parallels nearly so; the LAT/LON grid samples both, at a round
step chosen so lines are ~120 px apart (`map_grid`). It opens on the busiest
survey area (`centre_of`: the 50 km tile with most cells). The middle of all
the cells was the first choice and landed on empty ground between New
Babbage and Harper's Point, ~1,500 km apart -- further than the map shows
even zoomed out (~650 km).

**Display options (Height map tab, 2026-10-15).** Three switches, drawn
bottom to top: *Filled relief*, *Survey samples* (the coloured squares),
*Height lines*. Relief is on by default, with the lines; samples are off, since
they hide the relief. The relief is `height_grid` on a finer grid (50 m, or 4 px
when zoomed out) that reaches only 400 m from a measured cell, so it fills the
gaps between survey legs and stops at the edge of the survey. Hillshade is from
the upper left with slopes drawn twice as steep. The grid is kept like the
contours (`relief_grid`, until the survey or zoom changes or you pan off it)
and redrawn in 4 px blocks.

**Drag and zoom speed (2026-10-15).** A full redraw with the 6 km square in
view cost 0.35 s at 12.5 m a pixel, and every mouse-move event drew once, so
the events queued up and the map lagged behind the hand. Now: drag and wheel
events are folded into one *rough* draw per idle moment (0.08 s: kept contours
and relief used at whatever zoom they were made for, relief in 8 px blocks),
and the full draw follows 250 ms after the last event. The relief is worked
out one pixel to a block and blown up by Tk (`image copy -zoom`); putting
every pixel cost more than working the colours out. A settled redraw is
0.15 s; the one after a zoom is 0.5-0.7 s at 25 and 12.5 m a pixel, the two
height grids being worked out again.

**numpy (2026-10-15).** Profiled on microTech's 1,403 cells, Tk itself was
cheap (16 ms to put and zoom the relief image, 13 ms a globe redraw); the
time went on per-point Python loops: `height_grid` 290 ms, marching squares
170 ms, `relief_grid` 160 ms, `relief_spans` 65 ms at 12.5 m a pixel. Moving
those onto numpy took the full draw there from ~550 ms to ~77 ms, with the
same heights (within 1e-13), shading and contour lines. The relief is now an
RGB array handed to Tk through Pillow, coloured on a smooth scale rather than
the 101 steps the string version cached. Not faster: a 10 m contour interval
(~230 levels in view) still costs ~0.4 s, each level a pass over the grid;
the real interval is a twentieth of the body's range, so a handful of levels.
The fill is a guess between legs 200-300 m apart, not measured ground; the
samples switch shows what was measured.

**Drag step (2026-10-17).** Reported slow after railways were added; the
railways cost ~1 ms a draw, so not them. A drag step was ~100 ms, of which
the legend (160 canvas lines redrawn every step) took ~17 ms and
`local_cells` (cell centres worked out in Python, twice a draw) ~20 ms.
The legend's bar is the whole ramp whatever the range, so it is now drawn
only when it appears or goes flat, and its labels updated; cell centres are
kept per body (`cell_centres`, redone when the cell count changes) and
`local_cells` is numpy. Drag step ~70 ms, same cells to 1e-10 m. Only new
cells' centres are worked out (cells are only added, at the end of the
dict): redoing all of them took 0.7-0.8 s at 92,000 cells, one new cell now
5 ms. Scaling plan in TODO.md. What is
left: Tk's own redisplay ~30 ms, putting the relief image ~22 ms, LAT/LON
grid ~17 ms, contours ~13 ms.

**Survey waypoints.** A survey saves `survey-NNN` (type `survey`, so a
rename keeps it one) where it first records the ground on a body,
unless a survey waypoint is already within 5 km. Checked in game
(2026-10-14): a restart 2.6-3.1 km from survey-001 added none, one 19 km
away added survey-002.

**Waypoint types (2026-10-15).** Every waypoint has a `type`: `poi` (saved
by hand) or `survey` (a survey's start marker). It replaces the old
`"survey": true` flag; `load_waypoints` fills in a missing type and turns the
flag into one, so older files convert themselves. Survey markers crowded the
star map, so each view has its own default: star map POI only, height map
survey only, waypoint table both. SHOW ON MAP ticks the type of the waypoint
it shows, or the map would open on nothing. An older exe keeps the field on
save, since a waypoint is saved as the dict it was loaded as.

## Traced lines: railways (2026-10-17)

**Map symbols.** `examples/topomapsymbols.pdf` (USGS, "Topographic Map
Symbols") is the reference for how features are drawn on the height map.
`examples/` is kept local, not published. Railways use "Standard gauge
railroad, multiple track" (page 3): two parallel lines with ticks across.

**Survey modes.** A height survey records ElevationUnderCam into cells; a
trace records only where you went, as a line. `traces.py` holds the logic
(no Tk); `traces.json` is a list of `{"name", "kind", "planet", "started",
"points": [[x, y, z], ...]}` in the planet frame. Started from the SURVEY
menu (`trace a railway`), stopped with END TRACE. Only `railway` exists so
far; `kind` is there for shore lines next (see TODO.md).

**What a trace keeps.** The HUD's heights are not used: on foot they are
often not shown, and on the metro they contradicted good positions (below).
Each fix is judged against the last point kept instead. Dropped: a height
over the centre that changed faster than TRACE_MAX_CLIMB (100 m/s), a move
faster than SURVEY_MAX_SPEED (a dropped minus sign keeps the height), a fix
on another body, and one within 25 m of the last point (hovering). A
one-point trace is dropped on stop.

**First ride, New Babbage metro (2026-10-17).** railway-001: 54 points over
42 km, a fix every ~2 s at up to ~400 m/s, so points 700-900 m apart. The
first gate (drop when the heights contradict the position) dropped 45 of 107
fixes, though the positions logged in those stretches were continuous and
changed height at ~20 m/s; a 45 s stretch was lost and joined by a 5.6 km
straight line. The common misread, 0 read as 6 in x (520,018 -> 526,217),
moves the fix ~6 km and ~3.5 km up at New Babbage, so the height jump
catches it; replayed on the earlier flight it dropped all 9 and kept the
route. Weak spots: a misread digit in a coordinate near 0 barely changes the
height, and after a long gap the allowance grows (100 m per second of gap).
The first point has nothing to be judged against.

**Drawing.** A wide black line with a thin light one down its middle gives
the two rails with clean joins on curves; ticks every 40 px, 6 px either
side, drawn on top so they cross both rails.

## ElevationUnderCam over a liquid lake (microTech, 2026-10-17)

A free-fly survey of a small lake (~500 x 900 m), started skimming the water
in a small ship, with every frame logged. The ship held 1,231-1,251 m over
the reference sphere throughout; 392 frames had both heights read:

- 297: **elev - alt = ship's height**, to under 1 m. The HUD's "ground" was
  *above* the ship, by 0-73 m, and Altitude was the distance up to it.
  This "ground" is neither the water surface (not flat: 1,235-1,309 m) nor
  the bed (it cannot be above the ship). It rises smoothly to the west like
  terrain: most likely the planet's ground as if the lake were not there.
- 49: the usual elev + alt = height, ground 1,224-1,246 m, 0-18 m below the
  ship: mostly the south and south-east, where a settlement's piers stand
  in the water and the ship flew a little higher. Survey mode kept these.
- 52: neither, misreads.

The sweep went round the shore and zig-zagged across the middle. The
middle looked empty on a first plot only because positions are logged
every ~6 s and survey lines every ~2 s; placed by time between logged
positions, 395 of 429 frames land on the track. From 19:32 to 19:38 the
reader kept losing the HUD (debug HUD not on screen, CamPos row unread,
`-687,287` read as `87,254`), down to ~10 frames a minute from ~30: most
likely bright water or sky behind the HUD text, as before.

So over a lake the HUD gives no depth and no surface. The water level has
to come from the ship's own height while skimming (~1,232 m here). Survey
mode was right to drop the 297: they fail its identity. A frame where the
HUD's ground is above the ship (elev - alt = height) is a sign of water --
or of anything else cut below the planet's ground -- but where that ground
dips below the water it reads as normal, so it cannot mark the whole lake.
The ice sea near New Babbage (exactly -40.0 m, passing the normal identity)
behaves differently: a sea level, not a carved lake.

**Lake survey (built 2026-10-17).** A trace of kind `lake` (SURVEY > survey
a lake). Its first fix sets `level`: the camera's height over the reference
sphere less `water_below_cam_m` (2 m), as the pilot starts skimming the
water; a height over the sphere, so the surface curves with the body. The
water is the fan of triangles from the first point to each step of the
route (`traces.fan`): "everything between the start and the route flown".
Filling the outline instead would go wrong twice over: PIL fills polygons
even-odd (a pentagram's middle comes out empty), so a route crossing itself
or flown round one and a half times leaves holes. The fan's limit: the start
must see the shore across water, or a headland it cannot see past is
covered. On the height map all lakes are painted into one mask (overlaps
merge), drawn as an RGBA image over relief and contours with the LAT/LON
grid raised back over it; the shore is the mask's edge, 2 px, found by
erosion padded with the edge's own pixels (PIL's FIND_EDGES drew a shore
along the map's edge where a lake ran off it). Cells inside a lake are left
out of contours, relief and the colour range; the contour cache key holds
the lakes' point count. Replayed with the sweep above (169 logged
positions): the lake comes out as flown, a drag step 25 ms with it, 21 ms
without. Not yet surveyed in game with the new mode. That replay was then
added to `traces.json` as `lake-001` (169 logged positions, level 1,229.9 m)
so the lake shows until it is surveyed with the new mode.

**First lake survey in game (`lake-002`, 2026-10-18).** 85 points, level
1,232.0 m (lake-001's replay: 1,229.9 m); about 98% of lake-001's water is
inside it. Skimmed at ~5 m/s, at most 51 m/s. One misread got through: z
129,051 read as 129,668, 617 m off the north shore in 4 s (154 m/s) but only
78 m higher, under both TRACE_MAX_CLIMB and the 3,000 m/s limit. It drew a
~29,000 m2 wedge of false water and was removed by hand (backup in bak/).
Since then the speed limit is per kind, `TRACE_MAX_SPEED`: railways keep
3,000 m/s (railway-001 was flown at ~350, up to 650), lakes 100 m/s. A
replay of lake-002 drops only that point. A sea, too big to skim at under
100 m/s, will need its own kind with its own limit.

**Sea survey (built 2026-10-18).** A trace of kind `sea` (SURVEY > survey a
sea), for water too big to skim at under 100 m/s. Asked, the user chose a
lake in all but speed: any sea, not one place; the level from the first fix
(not the HUD's heights, nor a fixed value); the water still the fan from the
first point, accepting that over a big sea a headland the start cannot see
past is covered; and `TRACE_MAX_SPEED` 1,000 m/s, with no other guard. So a
misread like lake-002's (617 m in 4 s) gets through on a sea; only one over
1,000 m/s or `TRACE_MAX_CLIMB` is dropped. `traces.WATER` ("lake", "sea")
marks the kinds with a level and a fan; the overlay says SEA / END SEA, and
the height map draws seas as lakes under one switch, "Lakes and seas".
`STEP_M` (25 m) is left alone: flown fast, points are spaced by how often
the HUD is read (a fix every ~2-6 s, so ~600 m+ at 300 m/s), not by it.
First flown 2026-10-18 (`sea-001`, microTech, 13 minutes) and lost: the
first fix had y read with a minus sign (-429,205.7 for 429,205.7), 858 km
off at the right height, so it passed the climb check. Every fix after it
was judged against it and dropped as a position jump (858 km needs 858 s
at 1,000 m/s; the flight took 800 s), and END left one point: "too short".
Every point after the first is checked against the one before it, but the
first was checked against nothing. Since then `trace_fix` holds the first
fix (`State.trace_held`) until the next one agrees with it (`trace_jump`:
climb and speed), and replaces it with that one if not; a lake's level is
set only then, so the overlay keeps saying "skim the water" until it is.
Replaying sea-001's 110 logged positions (one every ~6 s) keeps 93 of
them, level 407.7 m, a sea about 31 x 29 km.

**Waterway survey (built 2026-10-18).** A trace of kind `waterway` (SURVEY >
survey a waterway) for rivers and canals: start at one end, fly along the
middle, END TRACE at the other. No level and no fan: a line, painted into
the lakes' water mask with PIL `line(joint="curve")`, so it joins a lake it
runs into and gets the same shore edge. Width chosen with the user: real
metres, to scale, never under 2 px. Each waterway stores `width_m`, copied
from `waterway_width_m` (default 30 m) when it starts, so the default can be
tuned and one waterway corrected in `traces.json` without moving the
others. At 30 m it is 5 px at 6.25 m a pixel, nearly all shore colour,
and 2 px from 15 m a pixel out. `TRACE_MAX_SPEED` 200 m/s. Heights under a
waterway are not left out of the relief as a lake's are: at 30 m it is
narrower than a 100 m cell. The height map switch is now "Lakes, seas,
rivers". Not yet flown in game.

**Jumping to the height map (2026-10-17).** SHOW ON HEIGHT MAP in the
Waypoints tab, and a right-click menu on star map pins (Show on height map,
Show details), call `HeightMapTab.show_waypoint`: it opens the waypoint's
body, turns its type on and centres on it as Go to does, keeping the zoom.
Show details is `waypoint_text` (as Copy) plus the save time, in a window of
its own. The height map's legend text was cut off at the bottom by then
(two more switches, two more key lines) and was shortened to fit.

## Main window text and screen scale (2026-10-15)

The Windows scale of a second screen never reached the text. `mss` makes the
process per-monitor DPI aware when it loads (thread awareness 0 before, 2
after), so Windows stops enlarging the window, and Tk sizes point fonts once
at start-up from the main screen (`tk scaling` 1.667, 120 dpi, 125 %). An
iPad as a 150 % screen (144 dpi) got 125 % text, at any scale set for it.

The main window now has its own named fonts sized in pixels
(`points x dpi / 72`) from `GetDpiForWindow`, checked once a second and when
the window moves; on a change it resizes them, the table rows and the
column widths. Tk redraws every widget and canvas text using a named font
when it changes. Only the main window: the overlay shares the same Tk, and
one global `tk scaling` cannot serve two screens. Canvas drawings (pins,
lines) stay in pixels.

## System map default view (2026-10-15)

Zoom 1 used to put the farthest body 0.42 of the window from the star,
whatever the tilt, which wasted most of a 60-degree-tilted view. It is now
the closest zoom at which every waypoint shown, and every body that has one,
fits with its name (`starmap.fit_view`), worked out for the default angle so
turning the view does not change it. The star stays in the middle, as it is
what the map turns about. Stanton opened 1.6 times closer, Pyro 1.1.

## Sharing waypoints and reports (2026-10-17)

Both tables take several rows (Ctrl/Shift-click); right-click gives **Copy**
and **Export**, and an **IMPORT** button reads a file someone sent. Export
writes the selected records in the format of their own file
(`waypoints.json` is a list, `bodies.json` a dict by place), so an export
can be imported or dropped in as is.

Copy is meant for Discord. A waypoint is a few readable lines (name, type,
system and body, lat/lon, height, note), not coordinates: the user chose
readable over re-importable. A report goes in a ``` code block, because its
lines are column-aligned and Discord's proportional font breaks them.

What an import checks, and why:

- **Fields.** Everything a record is placed or drawn by is required, but not
  every record has every field: `radius` only in the planet frame, `elev` and
  `agl` only where the HUD agreed, `frame` missing on the oldest waypoints
  (all planet frame). Reports need different fields per kind (rotating,
  static or moving body; fixed or moving place), taken from what
  `spin.make_record` / `make_space_record` write and `report_lines` reads.
  Bad records are skipped and listed; the rest still import.
- **The date.** `saved` / `analysed` become the import time, and the note
  starts `Imported on : <date>`. A report's `t_ref` (the sun's phase anchor)
  is left alone: it is a measurement, not a date.
- **Waypoint clashes.** The same name, or within 1,000 m of one of yours.
  Distances only compare like with like: planet-frame waypoints on the same
  body (each body's coordinates are its own), system-frame ones in the same
  system. Yes on a name clash asks for a new name, as names are keys.
- **Report clashes.** A body has one report, so the same body offers only
  No / Replace. Places in space clash within 20 km, the app's own
  same-place distance (`SPACE_MATCH_M`): anything closer would compete with
  yours when you ANALYSE or open the report there. A place that turns with a
  body is compared by its position over that body, as `nearest_space` does;
  its system position is only where it was during the analysis.
- **Order.** Each accepted record is added before the next is checked, so
  records in one file are also checked against each other. A Replace is
  checked before anything is removed, so a refused one loses nothing. The
  file is saved once at the end.

Replacing a body report replaces what the sun times and the star map's
placing of that body come from.

## SUB and QT on waypoints (2026-10-18)

Two fields on every waypoint, backfilled on load like `system` and `type`:

- `sub`: what kind of place it is, one of `subtypes.SUBTYPES`, or `""` for
  not said (shown as `none`). It picks the icon on the star map pin and the
  height map spot. The list and the icons live together in `subtypes.py`;
  adding a kind is a name and an icon there, nothing else. An icon is shapes
  in a -1..1 box, drawn on a dark badge, so it reads over the globe and over
  the height map's colours alike. A `sub` with no icon (an imported one from
  a newer list, say) falls back to the plain pin.
- `qt`: whether you can quantum to it. Defaults by frame: `true` in deep
  space, where what gets marked is a station or a jump point, `false` on a
  body, where it is mostly your own spot. Applied to the 55 existing
  waypoints the same way, so the user sets `true` on cities and outposts by
  hand.

Routing (`qt_hop`): a QT-no target on a body is reached by jumping to the
nearest, over the ground, of the QT-yes waypoints on that body and its six
orbital markers. The OMs are always there, so there is always an answer, and
with no QT-yes waypoints on the body this is exactly the old `VIA OM-x`. The
VIA line now also gives the bearing from the hop to the target; from a polar
OM there is no compass bearing, so none is given. A QT-no target in deep
space hops via the nearest QT-yes deep-space waypoint in the same system,
direct; with none, no VIA line. A target in orbit still gets no VIA line.

The overlay's live DIST/BRG/turn and arrow still point at the target, not
the hop: the jump is flown with the game's own marker, and from where it
drops you the live guidance is the overland leg. The height map draws that
leg as a dashed magenta great circle from the hop (labelled `QT <name>`) to
the target; on its gnomonic projection a great circle is a straight line.

The save itself stays one click with no dialog, so the game keeps focus.
RENAME on the panel became EDIT: name, a SUB menu and a QT box, so both can
be set in game right after saving. `edit_waypoint` checks the SUB before the
rename, so a refused edit changes nothing.

The Waypoints tab has a name search (`search_waypoints`): part of the name,
any case, filtering as you type, on top of the system/body/type filters. It
matches the name only, as asked; not SUB or note. Picking a pin on a map
clears it along with the other filters if it would hide that waypoint.

## Flown legs in the relief (microTech, 2026-10-18)

The expanding square's legs show in the height map's colours and shading.
In a 12 x 12 km view (910 cells, most with one reading), each cell against
a sloping fit to its neighbours within 400 m: typically 11 m off, 5% more
than 54 m, and a cell's error matches its nearest neighbour's (correlation
0.51), so stretches of a leg read high or low together. The fill adds to
it: `height_grid` is inverse-distance weighted and passes through every
cell, so the ground comes out flattest on the track (median slope 0.19)
and steeper between legs (0.28-0.33). Tried in a scratch script, numpy
only: a Gaussian average (200 and 300 m) and a sloping (local plane) fit
(200-400 m). They soften the legs without removing them, blur real
detail, and the plane fit blows single bad cells up into dark pits.
Dropping cells over 30 m off their neighbours took 144 of 901, real ridge
included, leaving holes. The fill was left as it is. More readings per
cell are the fix: a second square, offset half a line spacing.

The closest height map zoom is now 6.25 m a pixel (grid 0.05 deg, ~870 m,
on microTech and Hurston). The survey pattern is drawn 3 px wide, 4 on the
current leg, dashes 10/6, for a small screen.

## OCR engine: tesserocr instead of pytesseract (2026-10-25)

pytesseract starts a `tesseract.exe` per read, which reloads the language data
each time. tesserocr runs the engine inside Python and keeps one engine per
thread. It releases the GIL while reading: the Tk thread's worst pause during
three full reads was 18 ms.

Measured on 20 frames of the real HUD near Crusader (788x403 crop, scale 2.0,
cut 28), alternating engines frame by frame:

| conditions | pytesseract | tesserocr |
|---|---|---|
| game in its menus | ~905 ms | ~780 ms |
| logged in, in flight | ~1100-1400 ms | ~900-1150 ms |

Both gave the same fix on 79 of 80 reads; the odd one out was pytesseract
reading Crusader's radius as 7,450,910.

Tried and not worth it:
- `OMP_THREAD_LIMIT` 1, 2 or 4: no difference beyond run-to-run noise.
- The fast English model: the UB Mannheim install already ships it (same file
  as `tessdata_fast`, md5 `d1be414f...`).
- Word lists off (`load_system_dawg`, `load_freq_dawg`): about 2%, noise.
- Scale 1.0: about 45% faster but 0-1 of 20 frames right -- digits swap
  (5/8, 0/9) into fixes that still look plausible. Scale 1.5 needs
  `contrast_radius` 4.5 (in proportion) to stay 20/20, and saves about 10%;
  left at 2.0 until it has been checked over more backgrounds.

## Reading only the wanted rows (2026-10-25)

Most of a read is recognising the ~31-35 HUD rows, of which the parsers use
about ten. `RowReader` learns the row layout from a full read, then on later
frames finds the lines without recognising them (75 ms) and, if they sit where
they did, recognises only the wanted rows. `read_rows_only: false` turns it off.

- Each read is checked against the fields (`hud_fields`) the full read it
  learned from yielded. Losing the fix or the radius redoes the frame in full.
  Losing anything else is kept -- full reads drop Zone-row fields now and then
  too, and the State settles them over frames -- but three such reads in a row
  relearn the rows. Without a check, the first layout, learned from a frame
  that read "Current player tocation", dropped the location for 24 frames.
  Redoing every short read in full cost ~2 s per frame and happened on 3 of 20
  live frames, which is what made the check this lenient.
- Rows may drift half a row height between layouts. They were seen moving up
  to 6 px of 22 as letters below the line came and went; a quarter height
  failed on that. A row added above moves the rest by 26 px.
- Rows are picked with the parsers' own misread-tolerant patterns, and every
  HUD marker row is kept (the synthetic test HUD has no CamDir row).
- Bands are cropped in PIL, not set with `SetRectangle`: a rectangle starting
  at x=0 and spanning the full width makes Tesseract 5.5.3 read the bottom of
  the image, whatever its top.
- A full read relearns the rows every 60 partial reads.

On the 20 saved frames run twice, with the Vulture frame (a different layout)
dropped in: 38 of 41 reads partial, 0.56x the time of full reads (630 against
1228 ms per read, game running). Fix, heights, system, location and HUD-visible
matched the full read on every read. The system position and the Root row
read *more* consistently: 717.5 every time, matching the SolarSystem row,
where the full read wavers between that and 747.5 or misses it.

Live at ArcCorp (20 frames, then replayed with the final rules): 19 of 20
partial, 0.55x the time. CamPos identical on every frame. Where the two
disagreed, the row read had the altitude right 7 times to the full read's 1;
it read the radius as 800000.9 three times (the State keeps the roundest) and
the location once with a stray "{LZ ..." tail (settled over frames).

## Still open

- `HDG` is course over ground, not facing — wrong whenever hovering, strafing
  or reversing. Still mislabelled on the overlay.
- `to_latlon` divides by zero on `(0,0,0)` — currently unreachable, one gate
  change away.
- `BODY_RADII` covers Stanton, Pyro and Nyx I; the rest of Nyx is absent.
  Not harmless: without an entry the radius is voted on, and on Nyx I a
  misread 16,066 let fixes 625 km from the centre through (2026-10-16). Nyx I
  prints 1,000,000 and the height identity holds against it to the millimetre
  (3,052.095 m both ways), so it is in the table now. Add the other Nyx bodies
  the same way as they are visited.
- The panel rebuilds its waypoint list once a second to refresh distances. The
  selection is restored by name, but a `Listbox` is the wrong widget for rows
  that change under the user.
- `render()` still says "readout not found - run calibrate" when the real cause
  is usually the HUD being off. The panel's own status line says the right
  thing; this one does not.
- Naming a Lagrange point as `HUR-L1` needs each body's centre in system
  coordinates. ANALYSE now measures it (see "Body analysis"), so this only
  waits on enough bodies having been analysed.
- Hurston, ArcCorp and Clio still carry their 8-9 day old spin
  anchors; each is re-checked the first time the ship is near it with both
  position rows readable. Whether a planet's rotation phase differs between
  servers has not been ruled out -- the re-check covers it either way.
- How the HUD names Pyro's moons has not been seen. `fuzzy_body` matches a
  moon by its own name; a moon printed only as `pyro5a` would be taken for
  its planet.
