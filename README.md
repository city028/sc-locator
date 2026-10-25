# SC Locator

A small overlay for **Star Citizen** that tells you exactly where you are, lets
you save spots as waypoints, and guides you back to them with distance, bearing
and a turn hint.

It works by reading the game's own debug text off the screen (screen capture +
OCR). It does **not** read game memory, change game files or send input to the
game.

- Windows only
- One `.exe`, nothing to install (the OCR engine is packed inside)
- Your waypoints are stored in a plain JSON file next to the `.exe`

---

## Contents

- [Quick start](#quick-start)
- [What it shows](#what-it-shows)
- [Features](#features)
  - [Live location](#live-location)
  - [Status pin](#status-pin)
  - [Waypoints](#waypoints)
  - [Navigating to a waypoint](#navigating-to-a-waypoint)
  - [Orbital markers](#orbital-markers)
  - [Deep-space waypoints](#deep-space-waypoints)
  - [Body analysis: spin, sun, sunset](#body-analysis-spin-sun-sunset)
  - [Places in space and station info](#places-in-space-and-station-info)
  - [Main window: star map, waypoints, reports](#main-window-star-map-waypoints-reports)
  - [Sharing waypoints and reports](#sharing-waypoints-and-reports)
  - [Survey mode: your own height map](#survey-mode-your-own-height-map)
  - [Automatic backups](#automatic-backups)
- [The panel, button by button](#the-panel-button-by-button)
- [Command line](#command-line)
- [Settings](#settings-locator_configjson)
- [Files it creates](#files-it-creates)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Building from source](#building-from-source)
- [Credits and licences](#credits-and-licences)

---

## Quick start

1. Download `sc-locator.exe` from the
   [Releases](../../releases) page and put it in its own folder
   (it saves its files next to itself).
2. In Star Citizen, set the display mode to **Borderless Window**. Nothing can
   draw over exclusive fullscreen.
3. In game, open the console with `~` and type:

   ```
   r_DisplayInfo 2
   ```

   This turns on the debug text in the top-right corner. It resets every time
   the game restarts, so do it each session.
4. Double-click `sc-locator.exe`. A small coloured dot (the **pin**) appears in
   the bottom-right corner of the screen. A console window also opens; leave it
   open (it shows errors if something goes wrong).
5. When the pin turns **green** you have a position fix.

To click the overlay while in game, hold **`F`** (the game only shows a mouse
cursor while `F` is held), or alt-tab.

> **Windows SmartScreen** may say "Windows protected your PC" because the
> `.exe` is not code-signed. Click **More info → Run anyway**. Some antivirus
> tools flag packed Python programs by mistake; the full source is in this
> repository if you would rather build it yourself.
>
> The first start takes a few seconds while the `.exe` unpacks itself.

---

## What it shows

Right-click the pin to open the panel. The top part is where you are:

```
Current location: stanton, euterpe
Status: fix live
FOB Abandoned Stanton4c FOB2
LAT   -0.835
LON +119.609
H   457 m ground / 7.8 m up
```

Below that, when a target is set, is where you are going:

```
TARGET microtech-003
VIA  OM-3  then 12.4 km over ground BRG 091
DIST 8.91 km surface / 8.93 km direct
BRG  087 E   HDG 064   turn RIGHT 23
ALT  -1.2 km vs you
```

Next to the target block is a compass arrow pointing the way to turn.

Below that is the list of every saved waypoint, grouped by `system / body`,
as a table: `Location`, `Distance`, `HDG`, `LON`, `LAT`. Distance and bearing
are filled in for the ones on the body you are on; LON/LAT for every waypoint
on a body.

---

## Features

### Live location

Read from the game's debug HUD about once a second:

| line | meaning |
|---|---|
| `Current location` | star system and body (planet or moon) you are at |
| place name | the game's own location name (outpost, city, station) |
| `LAT` / `LON` | latitude and longitude on the body, in degrees |
| `H` | height of the ground below you, and how high above that ground you are |

Above 100 km over a body it says **In Space near &lt;body&gt;** instead,
because at that range the body is only the nearest bearing, not where you are.

OCR makes mistakes, so the program checks every reading before it believes it:
body names must repeat for a few frames, the radius comes from a built-in table
of known bodies, and heights are only shown when the HUD's numbers agree with
each other. A bad frame is dropped, not shown.

### Status pin

When the panel is closed, only the pin is shown. Its colour tells you the state:

| colour | meaning | what to do |
|---|---|---|
| grey | game not running | start the game |
| amber | game running, but no debug HUD | press `~`, type `r_DisplayInfo 2` |
| green | position fix is live | — |
| amber | fix going stale (no good reading for a few seconds) | look away from bright light behind the HUD text |
| blue | HUD is up but no body in range (between planets) | normal in deep space |
| red | capture error | run with `--log` and check the log |

- **Left-click the pin** to save a waypoint where you are. It works in flight.
  The result ("saved hurston-004" or the reason it refused) shows beside the
  pin for a few seconds.
- **Right-click the pin** to open the panel. Right-click again to close it.

### Waypoints

A waypoint is a saved position on a body (or in deep space).

- **Save** with a left-click on the pin, or **SAVE** in the panel. It is
  named automatically, e.g. `microtech-003`. Rename it later in the panel.
- Each waypoint stores the body, system, position, the ground's elevation and
  how high above the ground you were, and the time saved.
- A save is **refused** if the latest reading contradicts itself (for example a
  misread digit in the position). Just click again a second later. This stops
  bad readings from becoming waypoints that look fine but point to the wrong
  place.
- Stations in orbit (Port Tressler, Everus Harbor, Grim HEX) can be saved too;
  they turn with their planet, so they are saved in the planet's frame like a
  spot on the ground.
- The list shows **all** waypoints, grouped by `system / body`, with the body
  you are on first.
- Every waypoint has a **type**: **POI** (point of interest: one you saved) or
  **Survey** (the marker a survey leaves where it started, see below). The
  star map shows POI waypoints and the height map survey ones unless you
  tick the other type; the Waypoints tab shows both.
- Every waypoint has a **SUB**: what kind of place it is. It sets the
  waypoint's icon on the star map and the height map. The built-in kinds
  are Space Station, Spaceport, City, Village, Building, Tower, Turret,
  Landing Pad, Wreck, Ruin, Summit, Jump Point, Town Hall, Radio Dish, Cargo
  Elevator, Shop, Cave, Minable, Settlement, Forward Operating Base (FOB),
  Planetary Alignment Facility (PAF), Orbital Laser Platform (OLP), PAF
  Bunker, Ground Race Track, Crash Site and Derelict Ship, each with its own
  icon. Add, rename and delete them under **SUB categories** in the Settings
  tab: a SUB you add takes any of the icons (or the plain pin), a rename or
  delete carries over to the waypoints with it (a delete asks first, and
  leaves them with no SUB), and **Restore built-ins** adds back any built-in
  one you deleted. The list is kept in `subtypes.json`; a SUB on an imported
  waypoint that you do not have is added to it. A waypoint with no SUB
  (`none`) keeps the plain pin. It is meant for POI waypoints, but survey
  ones can have it too.
- Every waypoint has a **QT** yes/no: whether you can quantum travel to it.
  New and older waypoints start as **yes** in deep space (stations, jump
  points) and **no** on a planet or moon; set it by hand on cities and
  outposts you can jump to. It changes the route, see below.
- Set SUB and QT with **EDIT** in the panel (just after saving, in game) or in
  the main window's Waypoints tab.

### Navigating to a waypoint

Select a waypoint and press **TARGET**. The target block then shows:

| line | meaning |
|---|---|
| `VIA ...` | for a target with QT no: where to quantum to, how far it is over the ground from there, and the bearing to fly. It is the nearest of the QT yes waypoints on that body and its six orbital markers. No bearing is given from a polar OM. |
| `QT straight to it` | the target has QT yes: jump to it directly. |
| `DIST` | distance over the surface, and straight-line distance |
| `BRG` | compass bearing to the target (000 = north) |
| `HDG` / `turn` | your direction of travel and how far to turn LEFT or RIGHT. Appears once you have moved 20 m. |
| `ALT` | how much higher or lower the target is than you |

The compass arrow beside it shows the same turn: green on course, amber off it, red well off.

The `VIA` line is shown even without a position fix, so you can plan the
jump from orbit. Distance and bearing only work while you are on the same body
as the target. Once you have jumped, `DIST` and `BRG` guide you over the
ground to the target. The height map draws that overland leg as a dashed
magenta line from the `QT` point to the target.

A deep-space target with QT no shows `VIA` the nearest QT yes deep-space
waypoint in the same system and the straight-line distance from it.
A target in orbit has no `VIA` line: you fly to it directly.

If left and right feel reversed, set `"mirror": true` in the settings.

### Orbital markers

Each planet and moon has six orbital markers at fixed points:

| marker | where |
|---|---|
| OM-1 / OM-2 | north pole / south pole |
| OM-5 / OM-3 / OM-6 / OM-4 | equator at longitude 0, +90, 180, -90 |

The program works out which one is closest to any waypoint from its
coordinates, so you know which OM to jump to.

### Deep-space waypoints

Far from any body, the program switches to the **system-wide** position the
HUD also prints, so you can mark a spot between planets (a parked ship, a
wreck, a jump point). A deep-space waypoint has no latitude, longitude or
bearing; the target block shows the straight-line distance (`DIST 61.91 Mm
direct`). Distances use the game's units: m, km, Mm (megametres), Gm
(gigametres).

To save one, hold still for a moment: out there nothing else can confirm the
reading, so the position must be steady first.

### Body analysis: spin, sun, sunset

Land (or park on a pad), **hold still, look at a dark area**, and press
**ANALYSE**. The program watches how your system-wide position moves as the
body turns, and fits a circle to it. It takes from 30 seconds to about 2
minutes and stops by itself. Do not press STOP early; that ends the run with no
result.

The result is stored per body in `bodies.json` and shown with **REPORT**:

```
hurston  (stanton)  2026-10-05 15:14
Radius   1,000.0 km   diameter 2,000.0 km
Spin     2h 29m per turn  (0.04019 deg/s)
         +/-0.9% from 2m 32s, 6.2 deg
Axis     0.0 deg off the system z
Star     12.85 Gm, declination -0.0 deg
Here     lat +47.3  lon +17.0
         daylight 1h 14m of 2h 29m
Sun      +23 deg, up
         sets 34m, rises 1h 49m, noon 12m
```

- **Spin** — how long one day on that body lasts
- **Sun** — the sun's height above the horizon where you are now, and the time
  until sunset, sunrise and noon, live
- **Check** — appears if the result looks doubtful

Each body only needs to be analysed once. This repository ships with
`bodies.json` already holding **Hurston, Crusader, ArcCorp, microTech and
Clio**, so the sun figures work there straight away.

After a run, **KEEP** closes the report (the result is already saved) and
**DISCARD** throws the new result away and puts back the old one.

Results can be:

| result | meaning |
|---|---|
| rotating | normal: the spin was measured |
| static | nothing moved in two minutes: the body does not turn |
| moving | the motion was not a simple spin (bad readings, or the body also moves) |
| inconclusive | not enough good readings; nothing is saved |

Sunset is when the **centre** of the sun crosses the horizon; the top edge
disappears a little later.

### Places in space and station info

Press **ANALYSE** while parked or docked in space (more than 100 km above a
body, or out of any body's range). It samples your system position for about a
minute and says whether that place is **fixed** or **moving** in the system.

Use the **label** drop-down in the report to say what it is: planet, moon,
orbital station, Lagrange station, jump point, mining base, PIO, other.

Use the **station** drop-down to link a station's details from the
[UEX Corp](https://uexcorp.space) community database: faction, armistice zone,
landing pad sizes and services (refinery, repair, refuel, clinic, shops, trade,
habs, cargo). The station list is downloaded once a week and cached in
`stations.json`; this is the only thing the program fetches from the internet.

### Main window: star map, waypoints, reports

Next to the overlay a normal window opens, with a taskbar entry. Closing it
quits the program.

- **HUD overlay** switch: hide or show the in-game pin and panel.
- **Star map**: pick Stanton, Pyro or Nyx. The star is at the centre; planets
  and moons are placed where ANALYSE measured them, deep-space waypoints where
  they were saved. It opens zoomed in as far as it can with every waypoint
  shown, and every body that has one, in view. Drag to rotate, mouse wheel to
  zoom; **default view** (top right) puts the view back as it started. The
  **POI waypoints** and **Survey waypoints** switches pick which types are
  shown; only POI at first. Click a planet or moon
  to open it: its waypoints are shown on the globe, stations at their real
  height above it. Bodies with waypoints that have not been analysed yet are
  listed on the left and open the same way.
- **Around a planet**: the opened planet also shows its analysed moons and the
  places in space near it (stations, bases), in their true direction, with the
  real distance in the label. Distances beyond the globe are pulled in so
  everything fits; the globe itself is to scale and turned to how it stands
  right now, so surface waypoints sit right against the moons. Moons not yet
  analysed are listed on the left. A deep-space waypoint is shown around the
  nearest body within 1,000 Mm; to choose by hand, set **Shown around** in
  its **Edit...** window (e.g. a Lagrange station that sits far from its
  planet).
  Clicking a pin opens it in the Waypoints tab; right-click it, or a planet
  or moon, for its menu (see [Right-click menus](#right-click-menus)).
- **View from an orbital marker**: with a planet or moon open, the **OM-1** to
  **OM-6** buttons at the top right look down on it from above that marker, at
  the zoom you have set (OM-1 north pole, OM-2 south pole, OM-3/OM-4 equator at
  longitude +90/-90, OM-5/OM-6 at 0/180). The view stays over the marker as
  the planet turns, until you drag it.
- **Waypoints**: every waypoint in a table. Right-click a row for its menu:
  show it on the maps, **Rename...**, **Delete...** (asks first), and
  **Edit...** for a window with all of it: name, **SUB**, **QT**, note, and
  for deep space **Shown around**; **OK** saves them together, **X** or
  Escape leaves it as it was.
  Names need not be unique: any number of waypoints can be called Cave.
  The app tells them apart by a hidden `id` each one carries in
  `waypoints.json`, given to older waypoints the first time it loads.
  Filter by system, body and type (all, POI or Survey), and type in
  **Search** to narrow the list to names holding that text (any case) as you
  type. Copy and export them from the same menu, import them in Settings;
  see [Sharing](#sharing-waypoints-and-reports).
- **Reports**: every analysis result, with your own notes saved alongside it
  in `bodies.json`. A new ANALYSE run of the same place keeps the notes.
  Copy, export and import them the same way.
- **Settings**: **Import waypoints...** for a file someone shared, and the
  **SUB categories** (see above).

Sizes on the system map are not to scale (a planet would be smaller than a
pixel); positions are.

The window's text follows the Windows scale (Settings > Display > Scale) of
the screen it is on, so on a small second screen raise that screen's scale
to make the text bigger. Drag the window to another screen and its text
resizes within a second. The overlay keeps the size it starts with.

### Right-click menus

Each kind of thing has one menu, the same wherever you right-click it. An
entry that does not apply is left out, and so is the one for the tab you
are already on.

| Right-click | Where | Menu |
|---|---|---|
| a **waypoint** (POI, survey, deep space) | its pin on the star map or height map, its row in Waypoints | **Show details**, **Show on star map**, **Show on height map** (on the ground of a planet or moon: not in deep space, nor a station in orbit over 100 km up), **Show in Waypoints**, **Rename...**, **Edit...** (name, SUB, QT, note, and Shown around in deep space), **Delete...** |
| a **planet or moon** | its globe on the star map (in its own view, anywhere on it), a body in the list on the left, the ground of the height map, its row in Reports | **Show details**, **Open on star map**, **Show on height map** (if surveyed or traced), **Show report** (if analysed), **Show waypoints** |
| a **building** | the height map | **Show details**, **Rename...** |

**Show details** opens a small window by the pointer: for a waypoint what
Copy gives plus the nearest orbital marker, how far it is from you (on the
same body, or in the same system for deep space) and when it was saved; for
a planet or moon its analysis report, or what is known if it has none, with
its waypoints, surveyed cells and traces; for a building its size, area,
corners, wall direction and the walk it came from. **CLOSE** or Escape
closes it. In Waypoints and Reports, Copy and Export... follow the menu.

### Sharing waypoints and reports

In the **Waypoints** and **Reports** tabs, Ctrl-click or Shift-click to
select several rows, then right-click one of them:

- **Copy** puts them on the clipboard, ready to paste into Discord. A
  waypoint becomes a few readable lines (name, type, system and body,
  latitude/longitude, height, note); a report is pasted in a code block so its
  columns stay lined up, with your notes below it.
- **Export...** saves them to a file you choose, in the same format as
  `waypoints.json` or `bodies.json`. Send that file to a friend.

**Import waypoints...** (in the Settings tab) and **IMPORT** (top right in
the Reports tab) read a file someone sent you. Each record is checked first:

- Records missing a field they need, or with a field that makes no sense,
  are skipped. The rest are imported, and a summary at the end lists what was
  added, replaced, left out, and skipped with the reason.
- Imported records get the import date and time, and their note starts with
  `Imported on : <date>`, followed by the sender's own note.
- A **waypoint** within 1,000 m of one of yours on the same body (in the
  same system for deep space) shows both in full and asks: **Yes** adds it
  as well, **No** leaves it out, **Replace** puts it in place of the nearest
  of yours. Sharing a name with one of yours is no clash: names repeat.
- A **report** for a body you already have a report for offers only **No**
  (keep yours) or **Replace** (take theirs): one report is kept per body. A
  place in space within 20 km of one of yours (the distance at which the app
  treats two places as the same) asks Yes / No / Replace.
- Tick **apply to all remaining conflicts** to use the same answer for the
  rest of that file.

Replacing a body's report changes the sun times and where the star map
places that body, as they come from it.

### Survey mode: your own height map

Press **SURVEY** in the panel and fly or walk: every frame where the HUD's
ground height reads cleanly records the height of the ground under you into a
grid of cells about 100 m across (70-100 m, depending on where on the body).
Press **END SURVEY** to stop; the map is saved then, and every 2 minutes while
surveying. While it runs, the panel shows a survey block: the cells
surveyed on this body, the height below which it records, and a reminder of
how far apart samples land at 20, 30, 100 and 200 m/s.

The first spot a survey records on a body gets a waypoint, `survey-001`,
`survey-002` and so on, with the note "start of a survey", so every surveyed
area can be found again from **Go to** in the Height map tab. Rename or delete
it like any other waypoint. A survey that starts within 5 km of a survey
waypoint, renamed or not, adds no new one. These are **Survey** waypoints: the
height map shows them, the star map only when you tick **Survey waypoints**.

- Works in flight too: the HUD reports the ground under you, not your own
  height. Nothing is recorded more than 5 km over the reference sphere
  (`survey_max_height_m`): higher up the ground height mostly cannot be read
  (on microTech, not at all between 7 and 15 km), so survey low. Stations
  and orbits never touch the map. A sample whose position jumped faster than
  3 km/s since the last checked one (a misread digit or sign) is dropped too.
- A cell measured more than once keeps the average.
- For a map with no gaps, keep your samples under about 70 m apart. A frame
  comes every 1.5-2 seconds and some are dropped, so measured at Harper's
  Point 30 m/s still gave ~90 m and left small gaps. Just under 20 m/s gave
  ~35 m and an almost unbroken line; fly that, with flight lines 70 m apart.
- **Star map**, a planet open: the globe is drawn as a mesh. Tiles you have
  surveyed are coloured by height and raised (**relief x** at the top right
  sets by how much; real relief is invisible at globe size); the rest is grey.
  Each tile covers tens of km, so the globe shows where you have been.
- **Height map** tab: the ground around you at full detail, coloured by
  height from green through tan to white. Grey is ground nobody measured. It
  follows you while **follow me** is ticked; drag to move, wheel to zoom (0.4 m
  to 800 m a pixel). Switches on the left turn each layer on or off:
  - **Filled relief**: the ground between your flight lines filled in and
    shaded as if lit from the upper left. It only reaches 400 m from a
    measured cell, and it is a guess between lines, not measured ground.
  - **Survey samples**: the cells you measured, as coloured squares.
  - **Height lines**: contour lines, every fifth thicker and labelled with its
    height in metres.
  - **Survey pattern**: the square course, dashed (see below).
  - **POI waypoints** / **Survey waypoints**: your waypoints on that body of
    each type, each with the ground height where it was saved. Both are on
    at first.
  - **POI heights**: the ground height after each POI's name; untick it
    to show POI names only. Survey waypoints keep theirs.

  A **Go to** list centres the map on a waypoint of the types shown.

#### Flying a pattern

Pressing **SURVEY** asks which pattern to fly: free fly, or an expanding
square with lines 500 m or 1000 m apart. The square starts at the waypoint you
have targeted and winds out clockwise; every leg is due north, east, south or
west, so it can be flown on the ship's compass alone. The panel shows the two
lines to fly by, a heading to steer back onto the line, the speed to fly the
leg at, and a turn warning before each corner. It never ends by itself: end
the survey when the area is big enough. The square is saved at every corner,
and the next **SURVEY** offers to resume it at the leg you stopped on or to
skip ahead past the legs already flown.

#### Tracing a railway

**SURVEY** > **trace a railway** records a line instead of heights: fly (or
ride the train) along a track and every fix adds a point to it, `railway-001`,
`railway-002` and so on. Height does not matter, only where you go; fly right
over the track at about 30 m/s for a point every ~60 m. The panel shows the
length so far and why a fix added nothing, if it did not. Press **END TRACE**
at the end of the track; it is saved then and every 30 s while tracing. A
trace of one point is dropped.

The height map draws railways with the topographic map symbol for a standard
gauge railroad, multiple track: two black lines with ticks across them
(switch **Railways**). A body you have only traced on shows up in the
height map's body list too.

#### Surveying a lake

**SURVEY** > **survey a lake** maps a lake from the route you fly round it:

1. Get as low over the water as you can and pick **survey a lake**. The first
   fix sets the lake's level: your camera's height, less 2 m
   (`water_below_cam_m`). The HUD cannot give it: over a lake its ground
   height is neither the bed nor the surface.
2. Climb to a safe height, fly to the shore and follow the waterline all
   the way round.
3. Press **END LAKE**. Everything between where you started and the route
   you flew is water.

The height map draws it flat light blue with a blue shore line and its
level, as on a paper map (switch **Lakes**); heights measured inside a lake
are left out of its height lines and shading. Start somewhere you can see
the whole shore you will follow across the water: a headland the start
cannot see past is counted as water. Lakes that overlap merge, so a big one
can be done in parts, `lake-001`, `lake-002` and so on.

#### Surveying a waterway

**SURVEY** > **survey a waterway** maps a river or canal: fly to one end,
pick it, fly along the middle of the water and press **END TRACE** at the
other end. Keep under 200 m/s: a fix that moved faster is taken for a
misread and dropped. The height map draws it as water, to scale,
`waterway_width_m` wide (30 m unless you change it) and never thinner than
2 px; one that runs into a lake joins it. Each waterway keeps the width it
was started with, as `width_m` in `traces.json`, so one can be corrected
there.

#### Surveying a building

**SURVEY** > **survey a building** records a walk round a building, a point
every metre, as `building-001`, `building-002` and so on. Walk in **first
person** (in third person the HUD gives the camera's position, behind you),
close along the wall, all the way round and into every corner, then press
**END BUILDING**. Fixes that moved faster than 8 m/s since the last point
(judged over at most 4 s, so standing still does not loosen it) are dropped
as misreads. If five fixes in a row are dropped the overlay says **LOST
TRACK**: press **CANCEL** and walk it again. Once you are back where you
started the overlay says **BACK AT THE START**: press **END BUILDING**
then. A walk that goes on round past its start is cut where it got back
there, so the extra does no harm, but it is kept in `traces.json`.

After **END BUILDING** a **name** field opens: type what the building is,
or pick a name given before from the **▼** list, and press **OK** or Enter.
Names need not be unique (there is a Mining Control at every mining area);
`building-001` stays its own. **X** or Escape leaves it unnamed. The name is
kept as `label` in `traces.json`. On the height map it is printed on the
building where it fits inside the walls; where it does not, it shows next
to the pointer while the pointer is over the building. Right-click a
building on the height map and pick **Rename...** to change its name: type
one or pick one given before, **OK** or Enter; **CLEAR** removes it; **X**
or Escape leaves it as it was. Right-clicking a waypoint's pin there renames
the waypoint the same way (a waypoint always keeps a name, so no CLEAR).

The height map draws it squared off, tan with a dark outline as on a paper
map (switch **Buildings**; zoom right in, down to 0.4 m a pixel, to see the
detail): only right angles, turned to the building's walls, with steps
under 2 m smoothed out. The outline is pulled in `building_wall_offset_m`
(0.5 m unless you change it) from where you walked. Each building keeps the
offset it was started with, as `offset_m` in `traces.json`, so one can be
corrected there. The walk itself is kept as walked, so it can be squared
again if the method changes. Expect it right to about a metre.

### Automatic backups

Every time `waypoints.json`, `bodies.json`, `heightmap.json`, `traces.json` or
`locator_config.json` is rewritten, the previous version is copied to the `bak/` folder. The last 30
versions of each file are kept. If something goes wrong, copy one back.

---

## The panel, button by button

| button | what it does |
|---|---|
| **SAVE** | save a waypoint where you are (same as left-clicking the pin) |
| **TARGET** / **UNTARGET** | navigate to the selected waypoint, or stop |
| **EDIT** | edit the selected waypoint: its name, its **sub** (what kind of place it is) and **QT** (can be jumped to). Press **OK** or Enter; **X** or Escape cancels. |
| **DELETE** | delete the selected waypoint |
| **ANALYSE** / **STOP** | start a body or place analysis |
| **REPORT** / **HIDE** | show or hide the analysis for where you are |
| **SURVEY** / **END SURVEY** | record the ground's height under you into the height map, or stop and save |
| **END TRACE** | stop tracing a railway (started from the **SURVEY** menu) and save it |
| **END LAKE** | stop surveying a lake (started from the **SURVEY** menu) and save it |
| **END BUILDING** | stop surveying a building (started from the **SURVEY** menu) and save it |
| **CANCEL** | shown under the survey block while tracing: stop and throw the trace away |
| **CLOSE** | collapse the panel back to the pin |
| **QUIT** | exit the program |

- Drag the readout text with the left mouse button to move the panel.
- The panel only takes keyboard focus while you rename; the rest of the time
  your clicks go back to the game.
- Release `F` before typing a name, or the name fills with f's.

---

## Command line

Double-clicking the `.exe` is the same as `sc-locator.exe run`. From a command
prompt in the `.exe`'s folder you can also use:

| command | what it does |
|---|---|
| `sc-locator.exe run` | start the overlay |
| `sc-locator.exe run --console` | text readout in the console instead of the overlay |
| `sc-locator.exe run --log session.log` | write a diagnostic log (useful for bug reports) |
| `sc-locator.exe run --allow-multiple` | start even if another copy is running (normally refused, as two overlays draw on top of each other) |
| `sc-locator.exe list` | print every waypoint: system, body, nearest OM, lat/lon, height, time saved |
| `sc-locator.exe rename OLD NEW` | rename a waypoint (refused, listing where each is, when several share the name) |
| `sc-locator.exe delete NAME` | delete a waypoint (refused the same way when several share the name) |
| `sc-locator.exe convert X Y Z [--radius R]` | turn planet coordinates (metres) into lat/lon and height |
| `sc-locator.exe calibrate --find --delay 5 --save` | find the HUD text on your screen and save the capture area (see Troubleshooting) |

`calibrate` options: `--find` searches the whole screen for the HUD text,
`--delay N` waits N seconds so you can switch to the game, `--region L T R B`
sets the area by hand (fractions of the screen, 0 to 1), `--cut N` sets the
text contrast, `--save` writes the result to the settings. It also saves
`calib_raw.png` and `calib_processed.png` so you can see what the OCR sees.

---

## Settings (`locator_config.json`)

Created next to the `.exe` on first run. Edit it with any text editor while the
program is closed. The ones you are most likely to change:

| setting | default | meaning |
|---|---|---|
| `monitor` | `1` | which monitor the game is on (1 = primary) |
| `region` | `[0.60, 0.00, 1.00, 0.28]` | screen area read for the HUD text: left, top, right, bottom as fractions of the screen |
| `overlay_anchor` | `"br"` | corner the pin sits in: `br`, `bl`, `tr`, `tl` |
| `overlay_margin` | `[20, 20]` | distance in pixels from that corner |
| `alpha` | `0.85` | overlay opacity (0 to 1) |
| `mirror` | `false` | swap left/right in the turn hint |
| `interval` | `1.0` | seconds between screen reads |
| `idle_interval` | `5.0` | seconds between checks for the game while it is not running |
| `stale_after` | `3.0` | seconds without a good reading before the fix shows as stale |
| `space_line_m` | `100000` | height (m) above which it says "In Space near …" |
| `survey_max_height_m` | `5000` | height (m) over the reference sphere above which survey records nothing |
| `water_below_cam_m` | `2` | how far (m) below your camera the water is when a lake survey starts |
| `waterway_width_m` | `30` | how wide (m) a new waterway is drawn on the height map |
| `building_wall_offset_m` | `0.5` | how far (m) outside the walls you walk round a building; its outline is pulled in this much |
| `min_move` | `20.0` | metres you must move before your heading updates |
| `game_process` | `["starcitizen.exe"]` | process names that count as the game running |
| `read_rows_only` | `true` | read only the HUD rows the locator uses once their layout is known (about twice as fast); `false` reads every row every time |
| `tesseract_cmd` | `null` | path to your own `tesseract.exe`; its `tessdata` folder is used instead of the packed one |

The rest (`scale`, `contrast_*`, `far_line_m`, `default_radius`) tune the OCR
and are best left alone.

Keep the overlay out of the `region` area, or it reads its own text.

---

## Files it creates

All next to the `.exe`:

| file | what |
|---|---|
| `waypoints.json` | your waypoints |
| `bodies.json` | analysis results per body and place |
| `locator_config.json` | settings |
| `stations.json` | cached station list from UEX |
| `heightmap.json` | ground heights from survey mode, per body |
| `survey.json` | the last survey square, to resume it |
| `traces.json` | railways you traced, water and buildings you surveyed |
| `subtypes.json` | your SUB categories and the icon each uses |
| `bak/` | automatic backups of the files above |

To move to a new PC, copy the whole folder. To share waypoints or reports with
a friend, use **Export...** and **IMPORT** (see
[Sharing](#sharing-waypoints-and-reports)) rather than sending the whole file:
an import checks each record and asks before replacing anything they
already have.

---

## Troubleshooting

**The pin stays amber ("no HUD").** Type `r_DisplayInfo 2` in the game
console. It resets every game restart.

**The pin stays grey.** The game process was not found. If your game exe has a
different name, add it to `game_process`.

**The pin flickers between green and amber, or positions jump.** Bright light
behind the HUD text (sunlit ground, a cockpit lamp) makes it hard to read.
Turn your view toward something darker. This matters most during ANALYSE.

**Never gets a fix at all.** The HUD text may be outside the capture area
(different resolution, ultrawide, HUD on another monitor). Run this, then
switch to the game within 5 seconds:

```
sc-locator.exe calibrate --find --delay 5 --save
```

Then look at `calib_processed.png`: the `CamPos` line should be crisp, with
the right-hand digits not cut off. If the game is not on your primary monitor,
set `monitor` first.

**"HUD heights disagree with the position - not saved".** One of the numbers
was misread for that frame; press save again a second later. If it happens on
every frame where the ground is below sea level (frozen seas), update to
v1.0.1 or later.

**Nothing appears over the game.** The game must be in Borderless Window.

**"sc_locator is already running".** Another copy is open, possibly hidden.
Close it (or end `sc-locator.exe` in Task Manager).

**Keyboard shortcuts.** There are none that work in game: Star Citizen's
anti-cheat stops other programs from seeing key presses while the game has
focus. Use the pin with `F` held.

**Reporting a problem.** Run `sc-locator.exe run --log session.log` from a
command prompt, reproduce it, and attach `session.log`.

---

## Known limitations

- `HDG` is your direction of **travel**, not the way the ship is facing. It is
  wrong while hovering, strafing or reversing.
- The built-in radius table covers **Stanton, Pyro and Nyx I**. Other Nyx
  bodies use the radius the HUD prints, which OCR reads less reliably.
- Distance and bearing to a waypoint only work while you are on the same body.
- The analysis assumes every body spins the same way as the ones measured so
  far. The report's `Check` line warns if a result disagrees.
- On foot in very dark places the fix can drop out. Not yet tested properly.
- Survey mode records whatever the HUD calls the ground. Whether that includes
  buildings and outpost roofs is not yet tested; if it does, they show as
  spikes.
- It depends on the debug HUD's layout. A game patch that changes it can break
  reading until the program is updated.

---

## Building from source

Needs Windows, Python 3 (tested on 3.14) and Tesseract. The OCR runs inside
Python through [tesserocr](https://github.com/sirfz/tesserocr); the Tesseract
install supplies only its English language data.

```
winget install UB-Mannheim.TesseractOCR
pip install -r requirements.txt
python sc_locator.py run
```

To build the `.exe` (the language data is copied in from the Tesseract install
folder; set `TESSERACT_DIR` if it is not in `C:\Program Files\Tesseract-OCR`):

```
pip install pyinstaller
pyinstaller sc_locator.spec
```

The result is `dist\sc-locator.exe`.

| file | what |
|---|---|
| `sc_locator.py` | the program: screen capture, OCR, parsing, overlay, waypoints |
| `spin_analysis.py` | maths for the body analysis (circle fit, spin, sun position) |
| `stations.py` | UEX station list download and formatting |
| `sc_locator.spec` | PyInstaller build recipe |
| `bodies.json` | measured analyses for the Stanton bodies |
| `NOTES.md` | detailed development notes: how the HUD behaves, what OCR gets wrong, and why things work the way they do |
| `OCR-PERFORMANCE.md` | the switch to tesserocr and reading only the needed HUD rows, with before/after measurements |

---

## Credits and licences

- SC Locator is released under the [MIT licence](LICENSE).
- The `.exe` contains [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
  (Apache License 2.0) through [tesserocr](https://github.com/sirfz/tesserocr)
  (MIT), with the English language data from the
  [UB Mannheim Windows build](https://github.com/UB-Mannheim/tesseract/wiki).
  Tesseract's licence is packed in the `.exe` as `tesseract/LICENSE`.
- Station data comes from the [UEX Corp](https://uexcorp.space) community API.
- This is a fan-made tool, not affiliated with or endorsed by Cloud Imperium
  Games. Star Citizen® is a trademark of Cloud Imperium Rights LLC. It only
  reads pixels from your screen, but use it at your own risk.
