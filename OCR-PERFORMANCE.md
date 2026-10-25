# OCR performance: tesserocr and reading only the needed rows

*2026-10-25*

## Summary

Reading the debug HUD now takes about **half as long** in game: roughly
**600-660 ms per read instead of 1,100-1,400 ms**, with the same position
(CamPos) on every frame compared. Two changes did it:

1. **Tesseract runs inside the program** (through `tesserocr`) instead of
   starting `tesseract.exe` for every read. About 20% faster on its own.
2. **Only the rows the locator uses are read.** The HUD prints some 31-35
   rows; the program parses about ten. Once it knows where they are, it reads
   only those. This saved most of the rest.

Side effects: a read now fits inside the 1-second capture interval, the
`.exe` shrank from **107 MB to 57 MB**, and the thousands of `tesseract.exe`
processes an idle session used to start are gone. Reading only the needed
rows can be turned off with `"read_rows_only": false` in
`locator_config.json`.

An overlay session in game (about 5 minutes at ArcCorp L2) ran without errors,
and its warning rates were within the range of earlier runs.

## What changed

### 1. Tesseract inside the program (tesserocr)

Before, `pytesseract` wrote each image to a temporary file and started a new
`tesseract.exe`, which loaded the English language data again and wrote its
text back to a file. Every read paid for that start-up.

Now `tesserocr` keeps one Tesseract engine per thread, loaded once
(`open_ocr` in `sc_locator.py`). Each read hands it the image in memory.

- It releases Python's interpreter lock while it reads, so the overlay stays
  responsive: the worst pause measured on the main thread during three full
  reads was 18 ms.
- The `tesserocr` wheel brings the Tesseract engine (5.5.3) but not the
  language data. That still comes from a Tesseract install (the UB Mannheim
  installer), found the same way as before. `tesseract_cmd` in the config now
  points the program at that install's `tessdata` folder.
- The calibration's HUD finder (`--find`) needs word boxes; it now gets them
  from Tesseract's TSV output (`ocr_boxes`), the same columns `pytesseract`
  produced.

### 2. Reading only the needed rows (`RowReader`)

Recognising text is nearly all of a read's cost. Finding where the text lines
are, without recognising them, takes about 75 ms. So:

1. The first frame is read in full. If it yields anything, the program notes
   where every line is, and which of them it parses: the Zone rows, the player
   location, Planet, CamPos, Radius, and the rows that show the HUD is up
   (CamDir, ServerTime, ShardId).
2. On later frames it finds the lines first. If they sit where they did (to
   within half a row height), only the wanted rows are cut out of the image
   and read.
3. Otherwise -- a row added or removed, as when boarding a ship -- the frame is
   read in full and the new layout learned.

Safeguards:

- A partial read that loses the position or the radius that the learning read
  had is redone in full on the same frame.
- A partial read that loses something else (a Zone row misread, say) is kept,
  because full reads lose those now and then too and the program already
  settles them over several frames. But three such reads in a row mean a row
  is being missed, not misread, and the layout is learned again.
- Every 60th read is a full one, so the layout cannot go stale.
- Rows are picked with the same misread-tolerant patterns the parsers use.

### 3. Build and packaging

- `sc_locator.spec` no longer copies `tesseract.exe` and its DLLs; the
  engine comes in with `tesserocr`. Only `eng.traineddata` and Tesseract's
  licence are packed. The `.exe` went from 107 MB to 57 MB.
- `requirements.txt`: `pytesseract` replaced by `tesserocr`. Tesseract itself
  is still installed for its language data.

### Files

| file | change |
|---|---|
| `sc_locator.py` | `find_tessdata`, `open_ocr`, `ocr_text`, `ocr_boxes`; `RowReader` and its helpers; capture loop, calibration and HUD finder moved over; `read_rows_only` setting |
| `sc_locator.spec` | packs only the language data and licence |
| `requirements.txt` | `tesserocr` instead of `pytesseract` |
| `README.md` | install and build notes, `read_rows_only`, credits |
| `NOTES.md` | the measurements in full, and why the rules are what they are |

## Measurements

All in-game timings are medians per read, on the 788x403 HUD crop at the
default 2x enlargement, with the game running on the same machine.

### Engine: pytesseract against tesserocr

| conditions | pytesseract | tesserocr |
|---|---|---|
| live, in flight near Crusader (20 frames) | 1,414 ms | 1,124 ms |
| saved frames, game in its menus | ~905 ms | ~780 ms |
| saved frames, logged in, in flight | 1,293-1,409 ms | 1,051-1,151 ms |

Both engines gave the same position on 79 of 80 reads. The odd one out was
pytesseract reading Crusader's radius as 7,450,910 m (it is 7,450,010).

An early test on the star-map screenshots in `examples/` showed tesserocr 6x
faster (74 against 474 ms). Those screenshots have no debug HUD, so there was
little text to read; that figure only measures start-up cost.

### Reading only the needed rows

| test | partial reads | time against full reads | per read |
|---|---|---|---|
| 20 saved frames twice, plus a frame with a different layout | 38 of 41 | 0.56x | 630 against 1,228 ms |
| live at ArcCorp L2, first version | 15 of 20 | 0.72x | 664 against 1,301 ms |
| the same live frames, final version | 19 of 20 | 0.55x | 603 against 1,154 ms |
| the Crusader frames, final version | 19 of 20 | 0.54x | 574 against 1,109 ms |

Fields compared against a full read of the same frame:

- **Position (CamPos):** identical on every frame.
- **Altitude:** where the two differed, the row read was right 7 times, the
  full read once.
- **System position and Root row:** read more often and more consistently by
  the row read (717.5 every time, matching the SolarSystem row; the full read
  wavered to 747.5 or missed it).
- **Radius:** read as 800,000.9 instead of 800,000.0 on 3 frames at ArcCorp.
  The program already keeps the roundest radius reading.
- **Location:** once read with a stray `{LZ` fragment; settled over frames.

### Overall

| | before | now |
|---|---|---|
| one HUD read, in game | 1,100-1,400 ms | 600-660 ms |
| `.exe` size | 107 MB | 57 MB |
| processes started per read | 1 | 0 |

### Overlay session in game

`python sc_locator.py run`, 09:08-09:12 at ArcCorp L2: first fix after 5 s,
a waypoint saved, a space analysis finished with 52 samples in 60 s, and about
0.8 frames a second while parked. No errors. Warning rates per minute of
capturing, against the earlier runs in `session.log`:

| event | this run | earlier runs |
|---|---|---|
| contrast cut switched | 2.7 | 0-6.2 |
| misread system position rejected | 3.7 | 0-3.9 |
| "reader is stuck" (parked, a false alarm) | 0.4 | 0-0.5 |
| fix lost, back on the next frame | 1.0 | 0-1.9 |
| planet name misread | 0.8 | 0-3.7 |

The run was short and at a place not logged before, so this is a sanity check
rather than proof.

## Tried and not used

| idea | result |
|---|---|
| limit Tesseract's threads (`OMP_THREAD_LIMIT` 1, 2, 4) | no difference beyond run-to-run noise |
| Tesseract's "fast" English model | already installed: the same file, byte for byte |
| turn off Tesseract's word lists | about 2% faster: noise |
| enlarge the crop 1.0x instead of 2x | ~45% faster, but 0-1 of 20 frames right: digits swap into readings that still look valid |
| enlarge 1.5x (contrast radius 4.5) | ~10% faster and 20 of 20 right, but only tested at one place; left at 2x |

## Problems found along the way

- **Tesseract's `SetRectangle`:** a rectangle that starts at x = 0 and spans
  the full image width makes Tesseract 5.5.3 read the bottom of the image,
  whatever the rectangle's top. Each band is cropped from the image instead.
- **A misread row name:** the first layout was learned from a frame that read
  "Current player tocation", so the location row was left out and the
  location was lost for 24 frames. Rows are now picked with the parsers' own
  misread-tolerant patterns, and the field check above catches the rest.
- **Row jitter:** rows move up to 6 px (of 22) between frames as letters below
  the line come and go. The first tolerance (a quarter row) was too tight; it
  is now half a row. A row added above moves the rest by 26 px.
- **Strict field check:** redoing every partial read that lost any field cost
  about 2 s per frame and happened on 3 of 20 live frames, for fields full
  reads also lose. Only the position and radius now force a full read.
- **Installing tesserocr:** the Terminal panel in the Claude app sees its own
  copy of the user's Python packages, so tesserocr installed from another
  shell was not found there. Installing it from that terminal fixed it.

## Still to check

- A longer overlay session on a planet surface, especially over bright ground,
  to compare warning rates with earlier runs there.
- Whether 1.5x enlargement holds up over more backgrounds.
