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
VIA  OM-3   then 12.4 km over ground
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

### Navigating to a waypoint

Select a waypoint and press **TARGET**. The target block then shows:

| line | meaning |
|---|---|
| `VIA OM-x` | the nearest orbital marker to the target, and how far it is over the ground from there. Pick this OM when you quantum in. |
| `DIST` | distance over the surface, and straight-line distance |
| `BRG` | compass bearing to the target (000 = north) |
| `HDG` / `turn` | your direction of travel and how far to turn LEFT or RIGHT. Appears once you have moved 20 m. |
| `ALT` | how much higher or lower the target is than you |

The compass arrow beside it shows the same turn: green on course, amber off it, red well off.

The `VIA` line is shown even without a position fix, so you can plan the OM
jump from orbit. Distance and bearing only work while you are on the same body
as the target.

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
  they were saved. Drag to rotate, mouse wheel to zoom; **default view**
  (top right) puts the view back as it started. Click a planet or moon
  to open it: its waypoints are shown on the globe, stations at their real
  height above it. Bodies with waypoints that have not been analysed yet are
  listed on the left and open the same way.
- **Around a planet**: the opened planet also shows its analysed moons and the
  places in space near it (stations, bases), in their true direction, with the
  real distance in the label. Distances beyond the globe are pulled in so
  everything fits; the globe itself is to scale and turned to how it stands
  right now, so surface waypoints sit right against the moons. Moons not yet
  analysed are listed on the left. A deep-space waypoint is shown around the
  nearest body within 1,000 Mm; to choose by hand, use **Shown around** in the
  Waypoints tab (e.g. a Lagrange station that sits far from its planet).
  Clicking a pin opens it in the Waypoints tab.
- **View from an orbital marker**: with a planet or moon open, the **OM-1** to
  **OM-6** buttons at the top right look down on it from above that marker, at
  the zoom you have set (OM-1 north pole, OM-2 south pole, OM-3/OM-4 equator at
  longitude +90/-90, OM-5/OM-6 at 0/180). The view stays over the marker as
  the planet turns, until you drag it.
- **Waypoints**: every waypoint in a table; rename, delete, and add a note.
- **Reports**: every analysis result, with your own notes saved alongside it
  in `bodies.json`. A new ANALYSE run of the same place keeps the notes.

Sizes on the system map are not to scale (a planet would be smaller than a
pixel); positions are.

### Automatic backups

Every time `waypoints.json`, `bodies.json` or `locator_config.json` is
rewritten, the previous version is copied to the `bak/` folder. The last 30
versions of each file are kept. If something goes wrong, copy one back.

---

## The panel, button by button

| button | what it does |
|---|---|
| **SAVE** | save a waypoint where you are (same as left-clicking the pin) |
| **TARGET** / **UNTARGET** | navigate to the selected waypoint, or stop |
| **RENAME** | rename the selected waypoint; type the name and press **OK** or Enter; **X** or Escape cancels. |
| **DELETE** | delete the selected waypoint |
| **ANALYSE** / **STOP** | start a body or place analysis |
| **REPORT** / **HIDE** | show or hide the analysis for where you are |
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
| `sc-locator.exe rename OLD NEW` | rename a waypoint |
| `sc-locator.exe delete NAME` | delete a waypoint |
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
| `min_move` | `20.0` | metres you must move before your heading updates |
| `game_process` | `["starcitizen.exe"]` | process names that count as the game running |
| `tesseract_cmd` | `null` | path to your own `tesseract.exe`, to use instead of the packed one |

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
| `bak/` | automatic backups of the files above |

To move to a new PC, copy the whole folder. To share waypoints with a friend,
send them `waypoints.json` (it replaces theirs, so they should merge by hand
if they have their own).

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
- The built-in radius table covers **Stanton and Pyro**. Other systems (Nyx)
  use the radius the HUD prints, which OCR reads less reliably.
- Distance and bearing to a waypoint only work while you are on the same body.
- The analysis assumes every body spins the same way as the ones measured so
  far. The report's `Check` line warns if a result disagrees.
- On foot in very dark places the fix can drop out. Not yet tested properly.
- It depends on the debug HUD's layout. A game patch that changes it can break
  reading until the program is updated.

---

## Building from source

Needs Windows, Python 3 (tested on 3.14) and Tesseract.

```
winget install UB-Mannheim.TesseractOCR
pip install -r requirements.txt
python sc_locator.py run
```

To build the `.exe` (Tesseract is copied in from its install folder; set
`TESSERACT_DIR` if it is not in `C:\Program Files\Tesseract-OCR`):

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

---

## Credits and licences

- SC Locator is released under the [MIT licence](LICENSE).
- The `.exe` contains [Tesseract OCR](https://github.com/tesseract-ocr/tesseract)
  (Apache License 2.0), from the
  [UB Mannheim Windows build](https://github.com/UB-Mannheim/tesseract/wiki).
  Its licence is packed in the `.exe` as `tesseract/LICENSE`.
- Station data comes from the [UEX Corp](https://uexcorp.space) community API.
- This is a fan-made tool, not affiliated with or endorsed by Cloud Imperium
  Games. Star Citizen® is a trademark of Cloud Imperium Rights LLC. It only
  reads pixels from your screen, but use it at your own risk.
