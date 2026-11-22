"""The main window: HUD switch, star map, waypoint editor and report viewer.

A normal window with a taskbar entry, next to the overlay and sharing its Tk
root and its State. It never imports sc_locator: the running module is handed
in as `scl`, because a second import of that file would carry its own file
paths and its own copy of the data, and could save over the real files.

The handlers here stay thin: the edits are sc_locator's helpers, the map
geometry is starmap's.
"""
import ctypes
import ctypes.wintypes
import json
import math
import os
import shutil
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import webbrowser
from tkinter import filedialog, ttk

import numpy as np
from PIL import (Image, ImageColor, ImageDraw, ImageEnhance, ImageFont,
                 ImageTk)

import databackup
import discordapi
import discordshare
import heightmap
import mapprint
import maps
import starmap
import subtypes
import survey
import traces
import updater

BG, PANEL, FG, DIM, ACCENT = "#05080c", "#0d141c", "#D8E2EE", "#8FA0B4", "#7CFFB2"
SUN = ("#B86A12", "#FFB347", "#FFE7B0")
PLANET = ("#1D4E73", "#3E8CC4", "#A8D8FF")
MOON = ("#4A5560", "#8A97A4", "#D0D8E0")
PIN, ORBIT, GRID = "#FF9F43", "#1B3348", "#2D5A80"
GRID_LINE = "#2E343B"       # LAT/LON on the height map: darker than its grey
CONTOUR, CONTOUR_INDEX = "#C8925A", "#E0A868"   # brown, as on a paper map
# Survey legs: flown, now, next. Blue, not ACCENT: you fly along the leg
# you are on, and in your own green it hid your + (2026-10-15).
COURSE = ("#B8C6D4", "#5CC8FF", "#2F7FA8")
# Railways: black lines, the paper's colour between them, as on a paper map.
RAIL, RAIL_GAP = "#000000", "#FAF7F2"
# The overland leg from a QT hop to its target: dashed magenta, apart from
# everything else on the map.
ROUTE = "#FF5CD6"
UPDATE_NOTICE = "#FFB000"
# Lakes: light blue water and a darker blue shore line, as on a paper map.
WATER, SHORE = (169, 212, 239), (42, 122, 184)
# Buildings: tan with a dark outline, as on a large-scale paper map.
BUILDING, BUILDING_EDGE = "#F2C49B", "#3A2A1E"

# The colours a waypoint's name can be given on each map (Settings >
# Waypoints), by the name the config keeps; white is the window's own.
LABEL_COLOURS = {"white": FG, "black": "#000000", "red": "#E53935",
                 "yellow": "#FFEB3B", "blue": "#1E88E5", "green": "#43A047"}

SUN_PX, PLANET_PX, MOON_PX = 14, 9, 5
SYSTEM_PITCH = math.radians(60)     # the system map's default view: tilted
# Seconds without a body fix before follow me counts you as in deep space:
# long enough that a few unread frames do not flip the star map's view.
SPACE_AFTER = 10.0


BASE_DPI = 120     # the screen the window was laid out on: 125 %
ROW_PX = 20        # a table row there

# The window's fonts, one of each (family, points, bold), sized in pixels for
# the screen it is on. Tk sizes point fonts once, for the main screen, so the
# Windows scale of any other screen would never reach the text.
_fonts = {}
_dpi = [BASE_DPI]
_columns = []      # (table, column, width as laid out): widths follow too


def font_px(points, dpi):
    """Tk size of a `points` font on a `dpi` screen: negative is pixels."""
    return -round(points * dpi / 72)


def _font(family, points, bold=False):
    k = (family, points, bold)
    if k not in _fonts:
        _fonts[k] = tkfont.Font(family=family, size=font_px(points, _dpi[0]),
                                weight="bold" if bold else "normal")
    return _fonts[k]


def mono(points=10, bold=False):
    return _font("Consolas", points, bold)


def ui_font():
    """For buttons, tabs and lists that had Tk's default font."""
    return _font(tkfont.nametofont("TkDefaultFont").actual("family"), 9)


def px(n):
    """n pixels of the window as laid out, on the screen it is on now."""
    return round(n * _dpi[0] / BASE_DPI)


def set_column(tree, name, width, **kw):
    """A table column `width` px wide as laid out, kept to scale."""
    _columns.append((tree, name, width))
    tree.column(name, width=px(width), **kw)


# Which column each sortable list is sorted by:
# tree -> (column, descending, key of a cell's text).
_sorted_by = {}


def sortable(tree, key):
    """Sort a list by clicking a column's heading, A to Z by `key` of each
    cell's text (scl.sort_key), then Z to A on a second click; the heading
    shows which way. Kept when the list is filled again: its refresh calls
    sort_rows."""
    titles = {c: tree.heading(c, "text") for c in tree["columns"]}

    def click(col):
        was, down, _ = _sorted_by.get(tree, (None, False, key))
        down = was == col and not down
        _sorted_by[tree] = (col, down, key)
        for c, title in titles.items():
            arrow = "" if c != col else " \u25bc" if down else " \u25b2"
            tree.heading(c, text=title + arrow)
        sort_rows(tree)

    for c in titles:
        tree.heading(c, command=lambda c=c: click(c))


def sort_rows(tree):
    """Put a sortable list's rows in the order its heading was clicked
    for; as they were filled when none was. Rows the same stay in that
    order."""
    if tree not in _sorted_by:
        return
    col, down, key = _sorted_by[tree]
    rows = sorted(tree.get_children(),
                  key=lambda i: key(tree.set(i, col)), reverse=down)
    for n, iid in enumerate(rows):
        tree.move(iid, "", n)


def window_dpi(win):
    """DPI of the screen `win` is on: 96 at 100 %, 144 at 150 %."""
    if os.name != "nt":
        return BASE_DPI
    u = ctypes.windll.user32
    u.GetAncestor.restype = ctypes.c_void_p
    u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.GetDpiForWindow.argtypes = [ctypes.c_void_p]
    return u.GetDpiForWindow(u.GetAncestor(win.winfo_id(), 2)) or BASE_DPI


def fit_to_screen(win, style):
    """Resize every font, and the table rows, if the window has moved to a
    screen with another Windows scale (or the scale was changed)."""
    dpi = window_dpi(win)
    if dpi == _dpi[0]:
        return
    _dpi[0] = dpi
    for (_, points, _), f in _fonts.items():
        f.configure(size=font_px(points, dpi))
    style.configure("Treeview", rowheight=px(ROW_PX))
    for tree, name, width in _columns:
        tree.column(name, width=px(width))
    pill_theme(style)


TYPE_LABELS = {"poi": "POI", "survey": "Survey"}   # see WAYPOINT_TYPES


def type_switches(parent, on, command, boxes=None):
    """A tick box per waypoint type, ticked for the types in `on`.
    Returns {type: BooleanVar}; the tick boxes go in `boxes` if given."""
    out = {}
    for t, label in TYPE_LABELS.items():
        out[t] = tk.BooleanVar(value=t in on)
        box = ttk.Checkbutton(parent, text=f"{label} waypoints",
                              variable=out[t], command=command)
        box.pack(anchor="w")
        if boxes is not None:
            boxes[t] = box
    return out


def of_types(wps, switches):
    """The waypoints whose type is ticked."""
    return [w for w in wps
            if switches.get(w.get("type", "poi"), switches["poi"]).get()]


def list_lines(tree, parent=""):
    """How many lines a tree shows: each item, and what is under the open
    ones."""
    n = 0
    for iid in tree.get_children(parent):
        n += 1
        if tree.item(iid, "open"):
            n += list_lines(tree, iid)
    return n


def choice_groups(items):
    """SURVEY's choices (scl.survey_menu_items) in groups, each under a
    heading, for the Survey box: [(heading, [(label, key, fn)])]. The
    patterns come first (Height map), each sub-menu is a group of its own,
    and anything after the sub-menus is a Resume."""
    groups, rest = [], "Height map"
    for item in items:
        if item[0] == "separator":
            continue
        if item[0] == "cascade":
            groups.append((item[1][:1].upper() + item[1][1:],
                           [entry[1:] for entry in item[2]]))
            rest = "Resume"
            continue
        if not groups or groups[-1][0] != rest:
            groups.append((rest, []))
        groups[-1][1].append(item[1:])
    return groups


POI_LIST_PX = 330      # the star map's Waypoints list, as laid out
MAPS_LIST_PX = 260     # the Maps tab's list of maps, as laid out


def fit_text(text, room, measure):
    """`text` as it fits in `room` px (`measure` gives a text's width):
    whole if it fits, else cut and ended with "..."."""
    if measure(text) <= room:
        return text
    while text and measure(text + "...") > room:
        text = text[:-1]
    return text.rstrip() + "..."


def waypoint_tree(groups, system, ids, fmt):
    """The star map's Waypoints list from waypoint_groups: [(heading,
    [(id, name, distance text)])]. Only `system`'s groups when it is known,
    headed by the body; every group when it is None, headed system / body.
    Only the waypoints in `ids` (the search); no empty groups."""
    out = []
    for sys_name, body, rows in groups:
        if system is not None and sys_name != system:
            continue
        kept = [(wid, name, "-" if dist is None else fmt(dist))
                for wid, name, dist, *_ in rows if wid in ids]
        if kept:
            out.append((body if system is not None
                        else f"{sys_name} / {body}", kept))
    return out


class Section:
    """A framed block of the star map's left menu that folds to its title:
    click the title to hide what is in it, again to bring it back. Its
    contents go in .body; `opened` False starts it folded. `remember`, if
    given, is called with open or not each time its title is clicked."""

    def __init__(self, parent, title, expand=False, opened=True,
                 remember=None):
        self.expand, self.open, self.remember = expand, True, remember
        self.frame = RoundFrame(parent, on_click=self.clicked, arrow=True,
                                padx=8, pady=6)
        # Always packed: a frame whose last packed child goes keeps its size.
        tk.Frame(self.frame, height=0, bg=BG).pack()
        self.body = tk.Frame(self.frame, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.title = title
        self.fold(opened)

    def clicked(self):
        self.fold(not self.open)
        if self.remember:
            self.remember(self.open)

    def pack(self, **kw):
        grow = self.open and self.expand
        self.frame.pack(anchor="w", fill="both" if grow else "x", expand=grow,
                        **kw)

    def set_title(self, title):
        self.title = title.strip()
        self.frame.set_title(self.title, self.open)

    def fold(self, open_):
        """Open the section (True) or fold it to its title (False)."""
        self.open = open_
        if open_:
            self.body.pack(fill="both", expand=True)
        else:
            self.body.pack_forget()
        self.frame.configure(pady=6 if open_ else 0)
        if self.frame.winfo_manager():
            grow = open_ and self.expand
            self.frame.pack_configure(fill="both" if grow else "x",
                                      expand=grow)
        self.set_title(self.title)


def kept_section(state, scl, parent, title, key=None, make=Section, **kw):
    """A Section (or `make`) of a tab's menu, open or folded as it was last
    left by hand (`key`, else its title, in the menu_sections setting);
    folded until then."""
    key = key or title
    return make(
        parent, title, opened=state.cfg["menu_sections"].get(key, False),
        remember=lambda o: scl.save_setting(
            state, "menu_sections", dict(state.cfg["menu_sections"],
                                         **{key: o})),
        **kw)


def draw_icon(c, sub, x, y, size, tags=(), icon=None):
    """A waypoint's SUB icon on canvas `c`: a dark badge `size` px in radius
    centred on (x, y), the glyph in the pin's colour inside; `icon` draws
    that ICONS key instead (a preview). Returns the ids."""
    ids = [c.create_oval(x - size, y - size, x + size, y + size, fill=BG,
                         outline=PIN, width=1.5, tags=tags)]
    width = max(1.0, size * 0.14)
    for kind, paint, xy in subtypes.icon_items(sub, x, y, size * 0.68, icon):
        colour = PIN if paint == "fg" else BG
        if kind == "poly":
            ids.append(c.create_polygon(*xy, fill=colour, outline="",
                                        tags=tags))
        elif kind == "rect":
            ids.append(c.create_rectangle(*xy, fill=colour, outline="",
                                          tags=tags))
        elif kind == "oval":
            ids.append(c.create_oval(*xy, fill=colour, outline="", tags=tags))
        elif kind == "ring":
            ids.append(c.create_oval(*xy, outline=colour, width=width,
                                     tags=tags))
        else:
            ids.append(c.create_line(*xy, fill=colour, width=width,
                                     capstyle="round", tags=tags))
    return ids


def snapshot(state):
    """Copies of the waypoints and analyses, so drawing never holds the lock."""
    with state.lock:
        return ([dict(w) for w in state.waypoints],
                {k: dict(v) for k, v in state.bodies.items()})


def moved_on(held, here):
    """True once you have left the (system, body) you were at when a map was
    moved by hand: a new system, or a different body. A lost fix is not a
    move, so a few unread frames do not pull the map back to you."""
    return here[0] != held[0] or (here[1] not in (None, "unknown")
                                  and here[1] != held[1])


PICTURE_TYPES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
NO_PICTURE = "(none)"


def background_names(dirs):
    """The pictures in `dirs` (missing ones skipped), each name once, sorted."""
    names = {p.name for d in dirs if d.is_dir() for p in d.iterdir()
             if p.suffix.lower() in PICTURE_TYPES and p.is_file()}
    return sorted(names, key=str.lower)


def background_file(name, dirs, default):
    """The picture `name` from the first of `dirs` that has it, else the
    default; None for NO_PICTURE or when neither is found (plain colour)."""
    if name == NO_PICTURE:
        return None
    for n in (name, default):
        for d in dirs:
            if n and (d / n).is_file():
                return d / n
    return None


def cover_box(size, w, h):
    """A picture of `size` scaled to fill w x h without stretching: the
    scaled size, and the centred w x h box to crop from it."""
    s = max(w / size[0], h / size[1])
    sw, sh = max(w, round(size[0] * s)), max(h, round(size[1] * s))
    x, y = (sw - w) // 2, (sh - h) // 2
    return (sw, sh), (x, y, x + w, y + h)


# Every background picture is toned down, so it does not compete with the
# maps: this much of its brightness and of its colour is kept.
BACKGROUND_BRIGHTNESS, BACKGROUND_COLOUR = 0.40, 0.5


def tone_down(img):
    """`img` (RGB) dimmed and greyed by the amounts above. A pixel landing on
    BG exactly is nudged off it: BG is the see-through colour, and on the
    star map a drag starting there would fall through the window."""
    img = ImageEnhance.Color(img).enhance(BACKGROUND_COLOUR)
    a = np.array(ImageEnhance.Brightness(img).enhance(BACKGROUND_BRIGHTNESS))
    a[(a == ImageColor.getrgb(BG)).all(axis=2), 2] += 1
    return Image.fromarray(a)


class Wallpaper:
    """The picture behind the whole main window.

    Tk widgets cannot be see-through, so the window's BG colour is made
    see-through (Windows colour key) and a borderless window holding the
    picture sits straight behind it. Windows also lets clicks through those
    pixels, so a canvas that is dragged on its empty space (the star map)
    draws its own crop of the same picture first: under()."""

    def __init__(self, win):
        self.win = win
        self.src = self.scaled = self.photo = None
        self.geo = self.pending = None
        self.framed = self.refit = None     # see refit_frame
        self.crops = {}             # canvas -> (crop key, its PhotoImage)
        self.redraw = []            # called when the picture changes
        self.pic = tk.Toplevel(win)
        self.pic.withdraw()
        self.pic.overrideredirect(True)
        self.label = tk.Label(self.pic, bd=0, bg=BG)
        self.label.pack(fill="both", expand=True)
        # A click on a see-through spot lands here: the window comes back.
        self.label.bind("<Button-1>", lambda e: (
            win.lift(), win.focus_force(), self.stack()))
        win.bind("<Configure>", lambda e: e.widget is win and self.place(),
                 add="+")
        win.bind("<FocusIn>", lambda e: self.stack(), add="+")
        win.bind("<Unmap>", lambda e: e.widget is win and self.pic.withdraw(),
                 add="+")
        win.bind("<Map>", lambda e: e.widget is win and self.shown(), add="+")

    def show(self, path):
        """Use the picture at `path`; None (or one that will not open): the
        plain colour."""
        try:
            self.src = (tone_down(Image.open(path).convert("RGB")) if path
                        else None)
        except OSError:
            self.src = None
        self.scaled = self.photo = self.geo = None
        self.crops.clear()
        self.win.attributes("-transparentcolor", BG if self.src else "")
        if self.src is None:
            self.pic.withdraw()
            self.label.config(image="")
            self.changed()
        else:
            self.shown()

    def shown(self):
        if self.src is not None:
            self.pic.deiconify()
            self.geo = None
            self.win.after(30, self.place)

    def place(self):
        """The picture window exactly over the main window's inside."""
        win = self.win
        if self.src is None or not win.winfo_ismapped():
            return
        w, h = win.winfo_width(), win.winfo_height()
        geo = f"{w}x{h}+{win.winfo_rootx()}+{win.winfo_rooty()}"
        if geo != self.geo:
            self.geo = geo
            self.pic.geometry(geo)
            # Moved now: a stacking call first would leave it where it was.
            self.pic.update_idletasks()
            self.stack()
        if self.scaled is None or self.scaled.size != (w, h):
            if self.pending:            # rescaled once a resize settles
                win.after_cancel(self.pending)
            self.pending = win.after(150, self.rescale)
        if self.framed is None:
            self.framed = (w, h)
        elif (w, h) != self.framed:
            if self.refit:
                win.after_cancel(self.refit)
            self.refit = win.after(300, self.refit_frame)

    def refit_frame(self):
        """Windows stops a see-through (layered) window's title bar taking
        clicks once it has been maximised or snapped and back: they fall
        through to what is behind, so it can neither be dragged nor brought
        forward (2026-11-15). Taking the frame off for an instant and putting
        it back repairs that; done once a resize has settled."""
        self.refit = None
        win = self.win
        if self.src is None or os.name != "nt":
            return
        u = ctypes.windll.user32
        if u.GetAsyncKeyState(0x01) & 0x8000:      # an edge still held
            self.refit = win.after(300, self.refit_frame)
            return
        size = (win.winfo_width(), win.winfo_height())
        if size == self.framed:     # back to where it was repaired
            return
        self.framed = size
        u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_long]
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [
            ctypes.c_int] * 4 + [ctypes.c_uint]
        hwnd = int(win.wm_frame(), 16)
        style = u.GetWindowLongW(hwnd, -16)        # GWL_STYLE
        # WS_POPUP without caption, borders or buttons; then as it was.
        for s in ((style & ~0x00CF0000) | 0x80000000, style):
            u.SetWindowLongW(hwnd, -16, s)
            # NOSIZE NOMOVE NOZORDER NOACTIVATE FRAMECHANGED
            u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0037)

    def stack(self):
        """The picture straight behind the window, neither moved nor
        activated (a lift would fire <Configure> again, and again)."""
        if self.src is None or os.name != "nt":
            return
        u = ctypes.windll.user32
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [
            ctypes.c_int] * 4 + [ctypes.c_uint]
        u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        win, pic = (int(t.wm_frame(), 16) for t in (self.win, self.pic))
        # Neither stays on top of other programs. Placed behind an on-top
        # window, the picture turns on-top too, and the pair then covered
        # everything (2026-11-15, set by something outside the program).
        for hwnd in (win, pic):
            if u.GetWindowLongW(hwnd, -20) & 0x8:       # WS_EX_TOPMOST
                u.SetWindowPos(hwnd, -2, 0, 0, 0, 0,    # HWND_NOTOPMOST
                               0x0001 | 0x0002 | 0x0010)
        u.SetWindowPos(pic, win, 0, 0, 0, 0,
                       0x0001 | 0x0002 | 0x0010)  # NOSIZE NOMOVE NOACTIVATE

    def rescale(self):
        self.pending = None
        w, h = self.win.winfo_width(), self.win.winfo_height()
        if self.src is None or w < 2 or h < 2:
            return
        size, box = cover_box(self.src.size, w, h)
        self.scaled = self.src.resize(size, Image.LANCZOS,
                                      reducing_gap=3.0).crop(box)
        self.photo = ImageTk.PhotoImage(self.scaled)
        self.label.config(image=self.photo)
        self.changed()

    def changed(self):
        self.crops.clear()
        for redraw in self.redraw:
            redraw()

    def under(self, canvas):
        """Draw the piece of the picture behind `canvas` as its lowest item;
        nothing while there is no picture."""
        if self.scaled is None:
            return
        x = canvas.winfo_rootx() - self.win.winfo_rootx()
        y = canvas.winfo_rooty() - self.win.winfo_rooty()
        key = (x, y, canvas.winfo_width(), canvas.winfo_height())
        if self.crops.get(canvas, (None,))[0] != key:
            self.crops[canvas] = (key, ImageTk.PhotoImage(self.scaled.crop(
                (x, y, x + key[2], y + key[3]))))
        canvas.tag_lower(canvas.create_image(
            0, 0, image=self.crops[canvas][1], anchor="nw"))


class FullScreen:
    """Full screen on the monitor the window is on: its frame taken off and
    the window sized to that monitor; put back as it was on leaving. Not
    Tk's -fullscreen, which always fills the main monitor (Tk 8.6.15) and
    leaves the window on top of every other program."""

    def __init__(self, win):
        self.win, self.on, self.saved = win, False, None

    def set(self, on):
        if on == self.on:
            return
        self.on = on
        if os.name != "nt":
            self.win.attributes("-fullscreen", on)
            return
        u = ctypes.windll.user32
        u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_long]
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [
            ctypes.c_int] * 4 + [ctypes.c_uint]
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        hwnd = int(self.win.wm_frame(), 16)
        if on:
            r = ctypes.wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            style = u.GetWindowLongW(hwnd, -16)        # GWL_STYLE
            self.saved = style, (r.left, r.top, r.right - r.left,
                                 r.bottom - r.top)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            u.GetMonitorInfoW(ctypes.c_void_p(u.MonitorFromWindow(hwnd, 2)),
                              ctypes.byref(info))      # MONITOR_DEFAULTTONEAREST
            m = info.rcMonitor
            # WS_POPUP: no caption, borders or buttons.
            style = (style & ~0x00CF0000) | 0x80000000
            box = (m.left, m.top, m.right - m.left, m.bottom - m.top)
        else:
            style, box = self.saved
        u.SetWindowLongW(hwnd, -16, style)
        u.SetWindowPos(hwnd, None, *box, 0x0024)      # NOZORDER FRAMECHANGED


class MonitorInfo(ctypes.Structure):
    """Win32 MONITORINFO: a monitor's whole area and its work area."""
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.wintypes.RECT),
                ("rcWork", ctypes.wintypes.RECT), ("dwFlags", ctypes.c_ulong)]


# The top bar's pills: light-blue outline and text, filled while on.
PILL_LINE, PILL_TEXT, PILL_HOVER = "#7CC8FF", "#A0DAFF", "#C8EBFF"
PILL_DARK = "#0A1420"   # text on a filled pill: not BG, which is see-through


def pill_image(w, h, on, hover, scale=3):
    """A w x h RGBA pill (round ends): outlined, filled while `on`, a dark
    see-through inside otherwise. Drawn `scale` times as big and shrunk, so
    its edges are smooth."""
    line = ImageColor.getrgb(PILL_HOVER if hover else PILL_LINE) + (255,)
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    box = (scale, scale, (w - 1) * scale - 1, (h - 1) * scale - 1)
    ImageDraw.Draw(big).rounded_rectangle(
        box, (box[3] - box[1]) // 2, fill=line if on else (5, 8, 12, 110),
        outline=line, width=2 * scale)
    return big.resize((w, h), Image.LANCZOS)


def slash_image(w, h, colour, scale=3):
    """A w x h RGBA slash from bottom left to top right, in `colour` with a
    dark edge either side, to lay over an icon: "this, closed". Drawn
    `scale` times as big and shrunk, so it is smooth."""
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    k = round(h * 0.27) * scale            # half its length along each axis
    cx, cy = w * scale // 2, h * scale // 2
    ends = (cx - k, cy + k, cx + k, cy - k)
    # The edge in PILL_DARK, not BG: BG pixels are see-through.
    d.line(ends, fill=ImageColor.getrgb(PILL_DARK) + (255,), width=5 * scale)
    d.line(ends, fill=ImageColor.getrgb(colour) + (255,), width=2 * scale)
    return big.resize((w, h), Image.LANCZOS)


def icon(code, fallback):
    """A glyph of Windows' icon font (Segoe Fluent Icons on Windows 11,
    Segoe MDL2 Assets on 10: the same codes) and that font; `fallback` in
    the window's own font where neither is installed."""
    families = set(tkfont.families())
    for family in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if family in families:
            return code, _font(family, 10)
    return fallback, ui_font()


class Pill:
    """A pill-shaped button. A canvas, as Tk's buttons are rectangles: its
    piece of the window's picture first (Wallpaper.under), then the pill.
    Pills given the same `fits` are all as wide as the widest of those
    labels; a `circle` one is a circle as high as they are, for an icon
    (`font`). A `hint` shows by the pointer while it is over the pill;
    `on_hover(True / False)` is called as the pointer comes and goes.
    With `slash` set, a slash is drawn over its text or icon. Hidden, it
    keeps its place, so the pills after it do not move."""

    def __init__(self, parent, text, command, wallpaper, fits=(), font=None,
                 circle=False, hint=None, on_hover=None):
        self.text, self.fits, self.wallpaper = text, fits, wallpaper
        self.font, self.circle, self.hint = font, circle, hint
        self.on_hover = on_hover
        self.on = self.hover = self.hidden = self.slash = False
        self.image = self.slash_photo = None    # Tk drops what nothing holds
        self.tip = self.tip_after = None
        self.canvas = c = tk.Canvas(parent, bg=BG, highlightthickness=0,
                                    width=1, height=1, cursor="hand2")
        c.bind("<Button-1>", lambda e: self.hidden or (self.unhint(),
                                                       command()))
        c.bind("<Enter>", lambda e: self.set(hover=True))
        c.bind("<Leave>", lambda e: self.set(hover=False))
        c.bind("<Configure>", lambda e: self.draw())
        # Shown again (menu after collapse): its place may have moved.
        c.bind("<Map>", lambda e: self.draw())
        wallpaper.redraw.append(self.draw)
        self.fit()

    def set(self, text=None, on=None, hover=None, hidden=None, slash=None):
        if text is not None:
            self.text = text
        if slash is not None:
            self.slash = slash
        if on is not None:
            self.on = on
        if hidden is not None:
            self.hidden = hidden
            self.canvas.config(cursor="" if hidden else "hand2")
        if hover is not None:
            self.hover = hover
            if hover and self.hint and not self.hidden:
                self.tip_after = self.canvas.after(500, self.show_hint)
            else:
                self.unhint()
            if self.on_hover:
                self.on_hover(hover and not self.hidden)
        self.draw()

    def show_hint(self):
        self.tip_after = None
        self.tip = tk.Toplevel(self.canvas)
        self.tip.overrideredirect(True)
        tk.Label(self.tip, text=self.hint, font=ui_font(), bg=PANEL,
                 fg=PILL_TEXT, padx=6, pady=2, highlightthickness=1,
                 highlightbackground=PILL_LINE).pack()
        place_near(self.tip)

    def unhint(self):
        if self.tip_after:
            self.canvas.after_cancel(self.tip_after)
            self.tip_after = None
        if self.tip:
            self.tip.destroy()
            self.tip = None

    def fit(self):
        """Sized for the font in use: it follows the Windows scale."""
        f = ui_font()
        h = f.metrics("linespace") + px(12)
        size = ((h if self.circle else
                 max(f.measure(t) for t in self.fits) + px(36)), h)
        if size != (int(self.canvas["width"]), int(self.canvas["height"])):
            self.canvas.config(width=size[0], height=size[1])

    def draw(self):
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 8 or h < 8:
            return
        c.delete("all")
        self.wallpaper.under(c)
        if self.hidden:
            return
        self.image = ImageTk.PhotoImage(pill_image(w, h, self.on, self.hover))
        c.create_image(0, 0, image=self.image, anchor="nw")
        colour = (PILL_DARK if self.on
                  else PILL_HOVER if self.hover else PILL_TEXT)
        c.create_text(w / 2, h / 2, text=self.text,
                      font=self.font or ui_font(), fill=colour)
        if self.slash:
            self.slash_photo = ImageTk.PhotoImage(slash_image(w, h, colour))
            c.create_image(0, 0, image=self.slash_photo, anchor="nw")


# An open menu pill's box: dark, but not BG, which is see-through.
BOX = "#070B10"


def box_image(w, h, r, scale=3):
    """A w x h RGBA box with corners of radius r: BOX inside, the pills'
    outline round it. Drawn `scale` times as big and shrunk, so smooth."""
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        (scale, scale, (w - 1) * scale - 1, (h - 1) * scale - 1), r * scale,
        fill=ImageColor.getrgb(BOX) + (255,),
        outline=ImageColor.getrgb(PILL_LINE) + (255,), width=2 * scale)
    return big.resize((w, h), Image.LANCZOS)


def on_box(w):
    """`w` and everything in it on BOX instead of BG: in a box, BG would
    show the window's picture through it rather than the box."""
    if isinstance(w, ttk.Widget):
        cls = w.winfo_class()
        if cls == "TScrollbar":
            w.configure(style=f"Box.{str(w.cget('orient')).title()}"
                              ".TScrollbar")
        elif cls in BOX_STYLED:
            w.configure(style="Box." + cls)
    elif str(w.cget("bg")).lower() == BG.lower():
        w.configure(bg=BOX)
    for child in w.winfo_children():
        on_box(child)


# The look picked on 2026-11-22 (style A), on every control of the window:
# light-blue pill buttons, rounded fields, round ticks, thin scroll bars.
PILL_FILL = "#0A1622"   # a pill button's inside: not BG, which is see-through
FIELD, FIELD_LINE = "#0E1B28", "#2E5677"    # a field's inside and edge
PICK = "#1F3A52"        # what is selected in a list or menu
BOX_STYLED = ("TButton", "TCheckbutton", "TRadiobutton", "TMenubutton",
              "TCombobox", "TEntry", "Treeview")
_theme = {"n": 0, "images": []}


def rounded(w, h, r, fill=None, outline=None, width=2, scale=3):
    """A w x h RGBA box with corners of radius r, `fill` inside (see-through
    when None) and an `outline` round it. Drawn `scale` times as big and
    shrunk, so smooth."""
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    rgba = (lambda c: ImageColor.getrgb(c) + (255,) if c else None)
    ImageDraw.Draw(big).rounded_rectangle(
        (scale, scale, (w - 1) * scale - 1, (h - 1) * scale - 1), r * scale,
        fill=rgba(fill), outline=rgba(outline),
        width=width * scale if outline else 0)
    return big.resize((w, h), Image.LANCZOS)


def chevron(w, h, colour, up=False, scale=3):
    """A w x h RGBA chevron, pointing down (or up), in the middle."""
    big = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
    cx, cy, k = w * scale / 2, h * scale / 2, h * scale * 0.09
    dy = -k if up else k
    ImageDraw.Draw(big).line(
        ((cx - 2 * k, cy - dy), (cx, cy + dy), (cx + 2 * k, cy - dy)),
        fill=ImageColor.getrgb(colour) + (255,), width=round(1.6 * scale),
        joint="curve")
    return big.resize((w, h), Image.LANCZOS)


def tick_image(d, gap, on, colour, scale=3):
    """A round tick d px across, a dot in it when `on`, with `gap` px of
    nothing to its right (before its label)."""
    big = Image.new("RGBA", ((d + gap) * scale, d * scale), (0, 0, 0, 0))
    draw, c = ImageDraw.Draw(big), ImageColor.getrgb(colour) + (255,)
    draw.ellipse((scale, scale, (d - 1) * scale, (d - 1) * scale),
                 outline=c, width=2 * scale)
    if on:
        k = d * scale * 0.27
        draw.ellipse((d * scale / 2 - k, d * scale / 2 - k,
                      d * scale / 2 + k, d * scale / 2 + k), fill=c)
    return big.resize(((d + gap), d), Image.LANCZOS)


def pill_theme(style):
    """Style A on every ttk control, sized for the font in use: called
    again when the Windows scale changes, with new pictures (elements
    cannot be redefined, so each call names its own)."""
    h = ui_font().metrics("linespace") + px(10)
    r = h // 2
    _theme["n"] += 1
    n, keep = _theme["n"], []
    _theme["images"] = keep         # Tk drops what nothing holds

    def element(name, default, *states, **kw):
        def photo(img):
            keep.append(ImageTk.PhotoImage(img))
            return keep[-1]
        name = f"A{n}{name}"
        style.element_create(name, "image", photo(default),
                             *[(s, photo(i)) for s, i in states], **kw)
        return name

    def pill(fill, line):
        return rounded(2 * h, h, r, fill, line)

    button = element(".button", pill(PILL_FILL, PILL_LINE),
                     ("disabled", pill(PILL_FILL, FIELD_LINE)),
                     ("pressed", pill(PILL_LINE, PILL_LINE)),
                     ("active", pill(PILL_FILL, PILL_HOVER)),
                     border=r, padding=(px(14), 0), sticky="nsew")
    style.layout("TButton", [(button, {"sticky": "nswe", "children": [
        ("Button.label", {"sticky": "nswe"})]})])
    style.configure("TButton", foreground=PILL_TEXT, anchor="center",
                    padding=0)
    style.map("TButton", foreground=[("disabled", FIELD_LINE),
                                     ("pressed", PILL_DARK),
                                     ("active", PILL_HOVER)])

    # Text fields and drop-downs: a rounded field, a chevron on the right.
    field = element(".field", pill(FIELD, FIELD_LINE),
                    ("focus", pill(FIELD, PILL_LINE)),
                    border=r, padding=(px(12), 0), sticky="nsew")
    arrow = element(".chevron", chevron(px(22), h, PILL_TEXT),
                    sticky="")
    style.layout("TEntry", [(field, {"sticky": "nswe", "children": [
        ("Entry.textarea", {"sticky": "nswe"})]})])
    style.layout("TCombobox", [(field, {"sticky": "nswe", "children": [
        (arrow, {"side": "right", "sticky": ""}),
        ("Combobox.textarea", {"sticky": "nswe"})]})])
    style.layout("TMenubutton", [(field, {"sticky": "nswe", "children": [
        (arrow, {"side": "right", "sticky": ""}),
        ("Menubutton.label", {"sticky": "w"})]})])
    for cls in ("TEntry", "TCombobox", "TMenubutton"):
        style.configure(cls, foreground=FG, insertcolor=FG, padding=0,
                        selectbackground=PICK, selectforeground=FG)
    style.map("TCombobox", selectbackground=[("readonly", FIELD)],
              foreground=[("disabled", FIELD_LINE)])

    # Ticks: a ring, with a dot in it while on.
    d = round(ui_font().metrics("linespace") * 0.85)
    tick = element(".tick", tick_image(d, px(8), False, PILL_LINE),
                   ("disabled", tick_image(d, px(8), False, FIELD_LINE)),
                   ("selected", tick_image(d, px(8), True, PILL_LINE)),
                   sticky="")
    for kind in ("Checkbutton", "Radiobutton"):
        style.layout(f"T{kind}", [(f"{kind}.padding", {
            "sticky": "nswe", "children": [
                (tick, {"side": "left", "sticky": ""}),
                (f"{kind}.label", {"side": "left", "sticky": "nswe"})]})])
        style.configure(f"T{kind}", foreground=FG)

    # Scroll bars: a thin rounded thumb, no arrows, the trough unseen.
    t = px(7)
    for orient, size in (("Vertical", (t, 3 * t)), ("Horizontal", (3 * t, t))):
        thumb = element(f".{orient}thumb",
                        rounded(*size, t // 2, FIELD_LINE),
                        ("active", rounded(*size, t // 2, PILL_LINE)),
                        border=t // 2, sticky="nswe")
        style.layout(f"{orient}.TScrollbar", [(
            f"{orient}.Scrollbar.trough", {"sticky": "nswe", "children": [
                (thumb, {"expand": "1", "sticky": "nswe"})]})])
        for prefix, under in (("", BG), ("Box.", BOX)):
            style.configure(f"{prefix}{orient}.TScrollbar", troughcolor=under,
                            bordercolor=under, lightcolor=under,
                            darkcolor=under, background=under)

    # Tabs (Settings' sections): pills, filled for the open one.
    # Filled inside, as the buttons are: a see-through BG inside lets clicks
    # through the window to the picture behind, so only the outline and the
    # letters took them (2026-11-22).
    m = px(6)                       # the gap after each
    tab = element(".tab", rounded(2 * h + m, h, r, PILL_FILL, PILL_LINE),
                  ("selected", rounded(2 * h + m, h, r, PILL_LINE, PILL_LINE)),
                  ("active", rounded(2 * h + m, h, r, PILL_FILL, PILL_HOVER)),
                  border=(r, r, r + m, r), padding=(px(14), 0, px(14) + m, 0),
                  sticky="nsew")
    style.layout("TNotebook.Tab", [(tab, {"sticky": "nswe", "children": [
        ("Notebook.label", {"sticky": ""})]})])
    style.configure("TNotebook.Tab", foreground=PILL_TEXT, background=BG)
    style.map("TNotebook.Tab", foreground=[("selected", PILL_DARK),
                                           ("active", PILL_HOVER)],
              background=[("selected", BG)], expand=[])

    # The corners of all these show what they stand on: BG on the window's
    # picture, BOX in the star map's menu boxes (on_box).
    for cls in ("TButton", "TCheckbutton", "TRadiobutton", "TMenubutton",
                "TCombobox", "TEntry"):
        for prefix, under in (("", BG), ("Box.", BOX)):
            style.configure(prefix + cls, background=under)
            style.map(prefix + cls, background=[("active", under),
                                                ("pressed", under)])


def round_frame_image(w, h, ph, title, open_, arrow):
    """A RoundFrame's picture, w x h: the title in a pill ph high, filled
    and with an outline round all of it while open (or not folding); only
    the outlined pill while folded. `arrow`: the fold arrow at the right."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if open_:
        img.alpha_composite(rounded(w, h, ph // 2, None, PILL_LINE))
        img.alpha_composite(pill_image(w, ph, True, False))
    else:   # filled: a see-through inside would let clicks through
        img.alpha_composite(rounded(w, ph, ph // 2, PILL_FILL, PILL_LINE))
    colour = PILL_DARK if open_ else PILL_TEXT
    size = -font_px(9, _dpi[0])
    try:
        font = ImageFont.truetype("segoeui.ttf", size)
    except OSError:
        font = ImageFont.load_default(size)
    ImageDraw.Draw(img).text((px(16), ph / 2), title, font=font,
                             fill=colour, anchor="lm")
    if arrow:
        a = chevron(ph, ph, colour, up=open_)
        img.alpha_composite(a, (w - ph - px(4), 0))
    return img


class RoundFrame(tk.LabelFrame):
    """A box as the star map's menu draws one: its title in a filled pill
    at the top, a rounded outline round what is in it. Takes a LabelFrame's
    options (its look ones are left out). Drawn as one picture behind its
    contents; the inside is BG, so the window's picture shows through. With
    `on_click`, a click on the title calls it; `arrow` adds a fold arrow."""

    def __init__(self, parent, text="", on_click=None, arrow=False, **kw):
        for k in ("font", "fg", "bg", "bd", "relief", "labelwidget"):
            kw.pop(k, None)
        super().__init__(parent, bg=BG, bd=0, **kw)
        self.title, self.open, self.arrow = text.strip(), True, arrow
        self.drawn = self.photo = self.head_photo = None
        # Holds the title's room; it shows its own piece of the picture,
        # as it covers that piece.
        self.head = tk.Frame(self, bg=BG, width=1, height=1)
        self.head_pic = tk.Label(self.head, bd=0, bg=BG)
        self.head_pic.place(x=0, y=0)
        self.configure(labelwidget=self.head)
        self.back = tk.Label(self, bd=0, bg=BG)
        self.back.place(x=0, y=0, relwidth=1, relheight=1,
                        bordermode="outside")
        self.back.lower()
        self.bind("<Configure>", lambda e: self.draw(), add="+")
        self.head.bind("<Configure>", lambda e: self.draw())
        if on_click:
            self.head_pic.config(cursor="hand2")
            self.head_pic.bind("<Button-1>", lambda e: on_click())
            on_title = (lambda e: e.y < self.pill_h())  # the rest of it
            self.back.bind("<Button-1>", lambda e: on_title(e) and on_click())
            self.back.bind("<Motion>", lambda e: self.back.config(
                cursor="hand2" if on_title(e) else ""))

    @staticmethod
    def pill_h():
        return ui_font().metrics("linespace") + px(12)

    def set_title(self, title, open_=True):
        self.title, self.open = title.strip(), open_
        self.draw()

    def draw(self):
        if not self.head.winfo_exists():    # the window is closing
            return
        ph = self.pill_h()
        # As wide as the title and its arrow need, so a folded one has room.
        hw = ui_font().measure(self.title) + px(16) + (ph if self.arrow
                                                        else px(16))
        if (int(self.head["height"]), int(self.head["width"])) != (ph, hw):
            self.head.config(height=ph, width=hw)
        w, h = self.winfo_width(), self.winfo_height()
        x, y = self.head.winfo_x(), self.head.winfo_y()
        key = (w, h, ph, self.title, self.open, x, y)
        if w < ph or h < ph or key == self.drawn:
            return
        self.drawn = key
        img = round_frame_image(w, h, ph, self.title, self.open, self.arrow)
        self.photo = ImageTk.PhotoImage(img)
        self.back.config(image=self.photo)
        self.head_photo = ImageTk.PhotoImage(img.crop((x, y, x + hw,
                                                       y + ph)))
        self.head_pic.config(image=self.head_photo)


def stack_boxes(heights, top, gap):
    """The top of each of the left menu's pills, one under the other, for
    the height of each (with its box, when open)."""
    out = []
    for h in heights:
        out.append(top)
        top += h + gap
    return out


class MenuPill:
    """A section of the star map's left menu: a pill with its title and an
    arrow, opening into a box with its contents (.body) under the title.
    Used as a Section is (fold, set_title, open); show(False) takes it out
    of the menu (Target, while there is none). Laid out by PillMenu."""

    def __init__(self, menu, title, opened=True, remember=None):
        self.menu, self.title, self.open = menu, title.strip(), opened
        self.remember, self.shown = remember, True
        self.body = tk.Frame(menu.canvas, bg=BOX)
        self.window = menu.canvas.create_window(
            0, 0, window=self.body, anchor="nw", state="hidden",
            tags=("menu",))
        # Its contents grew or shrank: the pills under it move.
        self.body.bind("<Configure>", lambda e: menu.layout())
        menu.items.append(self)

    def clicked(self):
        self.fold(not self.open)
        if self.remember:
            self.remember(self.open)

    def fold(self, open_):
        """Open the box (True) or fold it to its pill (False)."""
        self.open = open_
        self.menu.layout()

    def set_title(self, title):
        self.title = title.strip()
        self.menu.layout()

    def show(self, shown):
        self.shown = shown
        self.menu.layout()


class PillMenu:
    """The star map's left menu: MenuPills one under the other at the top
    left of its canvas, over the map, all as wide as the widest box. Drawn
    on the canvas (tag "menu"), so the map keeps its size and zoom whatever
    is open, and shows between and around the pills."""

    def __init__(self, canvas):
        self.canvas, self.items = canvas, []
        self.shown = True       # the window's menu / collapse button
        self.laid = None        # what it was last laid out from
        self.images = {}        # the pictures in use: Tk drops what nothing holds
        # MenuPill -> (its pill image item, its texts, the pill's size)
        self.pills = {}
        self.arrows = icon("", "▼"), icon("", "▲")

    def layout(self):
        """Draw the pills and open boxes again, if anything they are drawn
        from has changed."""
        c, f = self.canvas, ui_font()
        h = f.metrics("linespace") + px(12)
        pad, x = px(10), px(10)
        width = max(s.body.winfo_reqwidth() for s in self.items) + 2 * pad
        rows = [s for s in self.items if s.shown and self.shown]
        key = (h, width, tuple((s.title, s.open, s.body.winfo_reqheight()
                                if s.open else 0) for s in rows))
        if key == self.laid:
            return
        self.laid = key
        c.delete("menuart")
        self.pills.clear()
        used, self.images = self.images, {}

        def picture(k, make):
            """The picture for `k`: one drawn already, else drawn now."""
            if k not in self.images:
                self.images[k] = used.get(k) or ImageTk.PhotoImage(make())
            return self.images[k]

        heights = [h + (2 * pad + s.body.winfo_reqheight() if s.open else 0)
                   for s in rows]
        for s in self.items:
            if s not in rows or not s.open:
                c.itemconfigure(s.window, state="hidden")
        for s, top, tall in zip(rows, stack_boxes(heights, px(6), px(8)),
                                heights):
            tag = f"pill{self.items.index(s)}"
            if s.open:
                box = picture(("box", width, tall, h // 2),
                              lambda: box_image(width, tall, h // 2))
                c.create_image(x, top, image=box, anchor="nw",
                               tags=("menu", "menuart"))
                c.coords(s.window, x + pad, top + h + pad)
                c.itemconfigure(s.window, width=width - 2 * pad,
                                state="normal")
            tags = ("menu", "menuart", tag)
            pill = c.create_image(x, top, anchor="nw", tags=tags, image=picture(
                ("pill", width, h, s.open, False),
                lambda: pill_image(width, h, s.open, False)))
            colour = PILL_DARK if s.open else PILL_TEXT
            glyph, glyph_font = self.arrows[s.open]
            texts = (c.create_text(x + px(16), top + h / 2, text=s.title,
                                   font=f, fill=colour, anchor="w",
                                   tags=tags),
                     c.create_text(x + width - px(16), top + h / 2,
                                   text=glyph, font=glyph_font, fill=colour,
                                   anchor="e", tags=tags))
            self.pills[s] = (pill, texts, (width, h))
            c.tag_bind(tag, "<Button-1>", lambda e, s=s: s.clicked())
            c.tag_bind(tag, "<Enter>", lambda e, s=s: self.hovered(s, True))
            c.tag_bind(tag, "<Leave>", lambda e, s=s: self.hovered(s, False))

    def hovered(self, s, on):
        """The pointer came onto (on) or left pill `s`: lit while on it."""
        self.canvas.config(cursor="hand2" if on else "")
        if s not in self.pills:
            return
        pill, texts, (w, h) = self.pills[s]
        k = ("pill", w, h, s.open, on)
        if k not in self.images:
            self.images[k] = ImageTk.PhotoImage(pill_image(w, h, s.open, on))
        self.canvas.itemconfigure(pill, image=self.images[k])
        colour = (PILL_DARK if s.open else PILL_HOVER if on else PILL_TEXT)
        for t in texts:
            self.canvas.itemconfigure(t, fill=colour)

    def show(self, on):
        """The window's menu (True) or collapse (False) button."""
        self.shown = on
        self.layout()


def open_main(root, state, scl, set_hud):
    win = tk.Toplevel(root)
    win.title("SC Locator")
    win.geometry("1180x760")
    win.configure(bg=BG)
    # Closing the main window ends the program: with the HUD switched off it
    # would otherwise be left running with nothing on screen.
    win.protocol("WM_DELETE_WINDOW", root.destroy)

    style = ttk.Style(win)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL,
                    font=ui_font())
    # Drop-down text and its list are widget options, not style ones.
    win.option_add("*TCombobox.font", ui_font())
    win.option_add("*TCombobox*Listbox.font", ui_font())
    # No box round any tab's page: it sits straight on the picture. clam
    # draws the box whatever its width, so it is drawn in BG, see-through.
    style.configure("TNotebook", background=BG, bordercolor=BG,
                    lightcolor=BG, darkcolor=BG)
    style.configure("Treeview", background=PANEL, foreground=FG,
                    fieldbackground=PANEL, font=mono(), rowheight=ROW_PX,
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
    style.map("Treeview", background=[("selected", PICK)],
              foreground=[("selected", PILL_HOVER)])
    style.configure("Treeview.Heading", background=BG, foreground=PILL_TEXT,
                    relief="flat", bordercolor=BG, lightcolor=BG, darkcolor=BG)
    style.map("Treeview.Heading", background=[("active", BG)],
              foreground=[("active", PILL_HOVER)])
    # In the star map's menu boxes (on_box).
    style.configure("Box.Treeview", background=BOX, fieldbackground=BOX,
                    bordercolor=BOX, lightcolor=BOX, darkcolor=BOX)
    # The drop-downs' lists.
    win.option_add("*TCombobox*Listbox.background", PANEL)
    win.option_add("*TCombobox*Listbox.foreground", FG)
    win.option_add("*TCombobox*Listbox.selectBackground", PICK)
    win.option_add("*TCombobox*Listbox.selectForeground", PILL_HOVER)
    style.configure("ComboboxPopdownFrame", background=PANEL,
                    bordercolor=FIELD_LINE, relief="solid", borderwidth=1)
    pill_theme(style)
    fit_to_screen(win, style)

    wallpaper = Wallpaper(win)
    bar = tk.Frame(win, bg=BG)
    bar.pack(fill="x", padx=8, pady=(6, 0))
    titles = ("Star map", "Height map", "Maps", "Waypoints", "Reports",
              "Surveys", "Settings")
    # Every tab pill as wide as the widest of their labels.
    fits = titles
    # The one button at the top left, three lines: collapse leaves only the
    # open tab's map or list; menu brings the tabs and side panels back
    # (show_menus). Slashed while the menu is open; its hint says which a
    # click does.
    lines = icon("\ue700", "\u2630")
    menu_btn = Pill(bar, lines[0], lambda: show_menus(
        state.cfg["menu_collapsed"]), wallpaper, font=lines[1], circle=True,
        hint="collapse")
    menu_btn.set(slash=True)        # open: slashed lines close it
    menu_btn.canvas.pack(side="left")
    # The star map back to its system's default view, at the bar's right
    # end: shown on the star map only (tab_changed).
    glyph, glyph_font = icon("\ue72c", "\u21bb")         # refresh
    home = Pill(bar, glyph, lambda: star.show(star.system, None), wallpaper,
                font=glyph_font, circle=True, hint="default view")
    home.canvas.pack(side="right", padx=(6, 0))
    # Full screen, left of it: no title bar or borders. Left again by this
    # pill (its icon then says back to a window), Esc or F11.
    full = FullScreen(win)
    to_full, to_window = icon("\ue740", "\u26f6"), icon("\ue73f", "\u2750")
    full_btn = Pill(bar, to_full[0], lambda: set_full(not full.on),
                    wallpaper, font=to_full[1], circle=True,
                    hint="full screen")
    full_btn.canvas.pack(side="right")

    def set_full(on):
        full.set(on)
        full_btn.hint = "window" if on else "full screen"
        full_btn.set(text=(to_window if on else to_full)[0])

    win.bind("<F11>", lambda e: set_full(not full.on))
    win.bind("<Escape>", lambda e: full.on and set_full(False))
    # The tabs, to the right of it: the notebook's own are never shown.
    tab_row = tk.Frame(bar, bg=BG)
    tab_row.pack(side="left", padx=(12, 0))
    # One switch for both maps: each keeps you in view while it is on. Set
    # in Settings > Other, with the HUD's.
    follow = tk.BooleanVar(value=state.cfg["follow_me"])
    updates = Updates(root, win, scl)
    # A line to click rather than a pop-up: this may be over the game.
    notice = tk.Label(bar, text="", font=mono(), fg=UPDATE_NOTICE, bg=BG,
                      cursor="hand2")
    notice.pack(side="left", padx=(12, 0))
    notice.bind("<Button-1>", lambda e: updates.offer())
    # Status messages at the far right.
    status = tk.Label(bar, text="", font=mono(), fg=DIM, bg=BG, anchor="e")
    status.pack(side="right", fill="x", expand=True)

    # The bottom strip, star map only (tab_changed): a ? pill at its right
    # end, and the star map's how-to line beside it while the pointer is on
    # the pill.
    foot = tk.Frame(win, bg=BG)
    foot_pack = dict(side="bottom", fill="x", padx=8, pady=(0, 6))
    foot.pack(**foot_pack)
    help_line = tk.Label(foot, text="", font=ui_font(), fg=PILL_TEXT, bg=BG)
    help_pill = Pill(foot, "?", lambda: None, wallpaper, circle=True,
                     on_hover=lambda on: help_line.config(
                         text=star.help if on else ""))
    help_pill.canvas.pack(side="right")
    help_line.pack(side="right", padx=(0, 10))

    # Its own style, so hiding its tabs leaves Settings' sections alone.
    style.layout("Main.TNotebook.Tab", [])
    nb = ttk.Notebook(win, style="Main.TNotebook")
    nb.pack(fill="both", expand=True, padx=8, pady=8)
    nav = Navigator(nb, state, scl)
    nav.wallpaper = wallpaper
    editor = WaypointEditor(nb, state, scl, nav)
    star = StarMap(nb, state, scl, nav, follow)
    height = HeightMapTab(nb, state, scl, follow, nav)
    reports = ReportViewer(nb, state, scl, nav)
    surveys = SurveysTab(nb, state, scl, nav)
    maps_tab = MapsTab(nb, state, scl, nav)
    nav.star, nav.height, nav.editor, nav.reports = star, height, editor, reports
    nav.surveys, nav.maps, nav.updates = surveys, maps_tab, updates
    settings = SettingsTab(nb, state, scl, nav, editor, set_hud, follow)
    if not state.cfg["hud_on"]:
        set_hud(False)
    nav.settings = settings         # Remove from Discord goes through it
    # The graphics first, then the lists and text. A pill each in the top
    # row, filled for the open tab; one picked elsewhere (Show on star map)
    # fills its pill too.
    tabs = [star, height, maps_tab, editor, reports, surveys, settings]
    tab_pills = []
    for i, (tab, title) in enumerate(zip(tabs, titles)):
        nb.add(tab.frame, text=title)
        pill = Pill(tab_row, title, lambda i=i: nb.select(i), wallpaper, fits)
        pill.canvas.pack(side="left", padx=(0, 6))
        tab_pills.append(pill)
    tab_pills[0].set(on=True)

    # The tab Settings was opened from: collapse goes back to it, as
    # Settings with no tabs to leave it by is a dead end (2026-11-22).
    came_from = [tabs.index(star)]

    def tab_changed(e):
        if nb.index("current") != tabs.index(settings):
            came_from[0] = nb.index("current")
        for i, pill in enumerate(tab_pills):
            if pill.on != (i == nb.index("current")):
                pill.set(on=not pill.on)
        on_star = nb.index("current") == tabs.index(star)
        home.set(hidden=not on_star)
        if on_star and not foot.winfo_manager():
            foot.pack(**foot_pack, before=nb)
        elif not on_star:
            foot.pack_forget()

    nb.bind("<<NotebookTabChanged>>", tab_changed)
    pills = [menu_btn, full_btn, home, help_pill] + tab_pills

    hidden = []         # (panel, its pack info, the sibling it was before)

    def show_menus(on):
        """menu (True) or collapse (False): the tabs and every tab's side
        panels (its .panels), or only the open tab's map or list. Kept for
        the next start."""
        if on and not tab_row.winfo_manager():
            tab_row.pack(side="left", padx=(12, 0), after=menu_btn.canvas)
        elif not on:
            tab_row.pack_forget()
        if on:
            for w, info, before in reversed(hidden):
                w.pack(**info, **({"before": before} if before else {}))
            hidden.clear()
        elif not hidden:
            for tab in tabs:
                for w in getattr(tab, "panels", ()):
                    if w.winfo_manager():
                        sibs = w.master.pack_slaves()
                        i = sibs.index(w)
                        hidden.append((w, w.pack_info(), sibs[i + 1]
                                       if i + 1 < len(sibs) else None))
            for w, _, _ in hidden:
                w.pack_forget()
        star.pill_menu.show(on)     # over its map: the map stays as it is
        if not on and nb.index("current") == tabs.index(settings):
            nb.select(came_from[0])
        menu_btn.hint = "collapse" if on else "menu"
        menu_btn.set(slash=on)
        if state.cfg["menu_collapsed"] != (not on):
            scl.save_setting(state, "menu_collapsed", not on)

    if state.cfg["menu_collapsed"]:
        show_menus(False)

    # Dragged to another screen: refit when it lands, not a second later.
    win.bind("<Configure>", lambda e: e.widget is win
             and fit_to_screen(win, style), add="+")  # Wallpaper's stays

    def tick():
        fit_to_screen(win, style)   # the Windows scale changed under it
        for pill in pills:
            pill.fit()
        for tab in tabs:
            tab.refresh()
        updates.refresh()
        notice.config(text=updates.banner())
        with state.lock:
            fresh = time.time() - state.note_time < 4
            status.config(text=state.note if fresh else "")
        win.after(1000, tick)

    wallpaper.redraw.append(star.draw)
    wallpaper.show(background_file(state.cfg["background"],
                                   scl.BACKGROUND_DIRS, scl.DEFAULT_BACKGROUND))
    tick()
    updates.start()
    if state.startup_notice:
        win.after(800, show_message, "SC Locator", state.startup_notice, win)
    return win


def blend(colours, t):
    """Colour at t in 0..1 along dark -> mid -> light."""
    a, b = (colours[0], colours[1]) if t < 0.5 else (colours[1], colours[2])
    t = t * 2 if t < 0.5 else t * 2 - 1
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(u + (v - u) * t):02x}" for u, v in zip(ca, cb))


def place_near(win, at=None):
    """Put a window just below and right of the screen point `at` (where it
    was asked for; the mouse pointer if None), kept inside the main window
    so it cannot run off the edge of whichever screen that is on. Every
    pop-up goes through here: one that is not placed opens at the top left
    of the screen, far from the click that opened it."""
    win.update_idletasks()
    if at is None:
        at = win.winfo_pointerxy()
    top = win.master.winfo_toplevel()
    # The window's own title bar and border are not in its requested size;
    # taken as the main window's, which has the same ones.
    side = top.winfo_rootx() - top.winfo_x()
    title = top.winfo_rooty() - top.winfo_y()
    right = (top.winfo_rootx() + top.winfo_width() - win.winfo_reqwidth()
             - 2 * side)
    bottom = (top.winfo_rooty() + top.winfo_height() - win.winfo_reqheight()
              - title - side)
    win.geometry(f"+{max(top.winfo_rootx(), min(at[0] + 12, right))}"
                 f"+{max(top.winfo_rooty(), min(at[1] + 12, bottom))}")


# The see-through colour of a pop-up's corners: used nowhere else.
POPUP_KEY = "#010203"


def popup_image(w, h, ph, title, closer=True):
    """A pop-up's picture, w x h: a box with corners of radius ph / 2, its
    title in a filled pill ph high at the top with an x at its right end
    (`closer`); POPUP_KEY round the corners. No title (None): no pill."""
    img = Image.new("RGBA", (w, h), ImageColor.getrgb(POPUP_KEY) + (255,))
    img.alpha_composite(rounded(w, h, ph // 2, BG, PILL_LINE))
    if title is None:
        return img
    img.alpha_composite(pill_image(w, ph, True, False))
    draw = ImageDraw.Draw(img)
    size = -font_px(9, _dpi[0])
    try:
        font = ImageFont.truetype("segoeui.ttf", size)
    except OSError:
        font = ImageFont.load_default(size)
    draw.text((px(16), ph / 2), title, font=font, fill=PILL_DARK, anchor="lm")
    if not closer:
        return img
    cx, cy, k = w - ph / 2 - px(4), ph / 2, ph * 0.16      # the x
    for a, b in (((cx - k, cy - k), (cx + k, cy + k)),
                 ((cx - k, cy + k), (cx + k, cy - k))):
        draw.line((a, b), fill=PILL_DARK, width=max(2, px(2)))
    return img


def owned_by(win, owner):
    """Make Windows keep `win` above `owner`, as it does a dialog over its
    window. Tk's transient does not do it for a frameless window: a click
    on the main window put it over its pop-up, which still held every click,
    so the program looked frozen (2026-11-22)."""
    if os.name != "nt":
        return
    u = ctypes.windll.user32
    u.SetWindowLongPtrW.restype = ctypes.c_void_p
    u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_void_p]
    win.update_idletasks()          # its Windows window exists from here
    u.SetWindowLongPtrW(int(win.wm_frame(), 16), -8,     # GWLP_HWNDPARENT
                        int(owner.wm_frame(), 16))


def popup(parent, title):
    """A pop-up window drawn as the menus' boxes are: no Windows frame, round
    corners, `title` in the pill at the top. Dragged by that pill; its x
    closes it, as the frame's X did. Starts withdrawn, so it can be placed
    (place_near) before it is seen. Returns (window, the frame its contents
    go in)."""
    win = tk.Toplevel(parent, bg=POPUP_KEY)
    win.withdraw()
    win.title(title)
    win.overrideredirect(True)
    win.attributes("-transparentcolor", POPUP_KEY)
    win.transient(parent.winfo_toplevel())
    owned_by(win, parent.winfo_toplevel())
    ph = ui_font().metrics("linespace") + px(12)
    inset = px(7)           # keeps the contents clear of the round corners
    back = tk.Label(win, bd=0, bg=POPUP_KEY)
    back.place(x=0, y=0, relwidth=1, relheight=1)
    blank = ImageTk.PhotoImage(Image.new("RGBA", (1, ph)))
    head = tk.Label(win, bd=0, bg=POPUP_KEY, image=blank, width=1,
                    anchor="nw", cursor="fleur")
    head.pack(fill="x")
    pane = tk.Frame(win, bg=BG)
    pane.pack(fill="both", expand=True, padx=inset, pady=(px(4), inset))
    held = {"pics": [blank], "size": None, "grab": None}

    def draw(e):
        w, h = win.winfo_width(), win.winfo_height()
        if e.widget is not win or (w, h) == held["size"] or w < 2 * ph:
            return
        held["size"] = (w, h)
        img = popup_image(w, h, ph, title)
        held["pics"] = [ImageTk.PhotoImage(img),
                        ImageTk.PhotoImage(img.crop((0, 0, w, ph)))]
        back.config(image=held["pics"][0])
        head.config(image=held["pics"][1])

    def press(e):
        if e.x >= win.winfo_width() - ph:       # the x
            win.destroy()
        else:
            held["grab"] = (e.x_root - win.winfo_x(), e.y_root - win.winfo_y())

    def drag(e):
        if held["grab"]:
            gx, gy = held["grab"]
            win.geometry(f"+{e.x_root - gx}+{e.y_root - gy}")

    def shown(e):
        """A frameless window is not given the keyboard on its own: take
        it, and hand it back to what the pop-up had put it on."""
        if e.widget is win:
            last = win.focus_lastfor()
            win.focus_force()
            if last is not None and last is not win:
                last.focus_set()

    win.bind("<Configure>", draw, add="+")
    win.bind("<Map>", shown, add="+")
    head.bind("<ButtonPress-1>", press)
    head.bind("<B1-Motion>", drag)
    head.bind("<ButtonRelease-1>", lambda e: held.update(grab=None))
    return win, pane


class PopMenu:
    """A right-click menu in the window's style (picked 2026-11-22): a
    pop-up box at the pointer, `title` (what was clicked) in its pill, a
    line per choice lit under the pointer, a thin line between groups. Takes
    the tk.Menu calls this window makes (add_command, add_separator, delete,
    tk_popup), so what fills a menu stays as it was; its look options are
    left out. Closes on a pick, Escape or a click anywhere else."""

    def __init__(self, parent, postcommand=None, **_look):
        self.parent, self.postcommand = parent, postcommand
        self.items, self.title, self.win = [], None, None

    def add_command(self, label, command=None, state="normal"):
        self.items.append((label, command, state != "disabled"))

    def add_separator(self):
        self.items.append(None)

    def delete(self, *_):
        """Everything out, the title too: the next fill starts afresh."""
        self.items, self.title = [], None

    def close(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.destroy()
        self.win = None

    def tk_popup(self, x, y):
        if self.postcommand:
            self.postcommand()
        self.close()
        f = ui_font()
        row, gap, pad = f.metrics("linespace") + px(8), px(10), px(8)
        ph = f.metrics("linespace") + px(12)
        texts = [it[0] for it in self.items if it]
        w = max([f.measure(t) for t in texts] + [0]) + 2 * px(14) + 2 * pad
        title = None
        if self.title:
            w = max(w, min(f.measure(self.title) + px(40), px(320)))
            title = fit_text(self.title, w - px(28), f.measure)
        top = ph + px(6) if title else pad
        h = top + sum(row if it else gap for it in self.items) + pad
        win = self.win = tk.Toplevel(self.parent, bg=POPUP_KEY)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-transparentcolor", POPUP_KEY)
        win.attributes("-topmost", True)
        c = tk.Canvas(win, width=w, height=h, bg=POPUP_KEY,
                      highlightthickness=0)
        c.pack()
        c.pics = [ImageTk.PhotoImage(popup_image(w, h, ph, title, False)),
                  ImageTk.PhotoImage(rounded(w - 2 * pad, row, row // 2,
                                             PICK))]
        c.create_image(0, 0, image=c.pics[0], anchor="nw")
        bar = c.create_image(pad, 0, image=c.pics[1], anchor="nw",
                             state="hidden")
        lines, y_at = [], top               # (top, bottom, command) per line
        for it in self.items:
            if it is None:
                c.create_line(pad, y_at + gap / 2, w - pad, y_at + gap / 2,
                              fill=FIELD_LINE)
                y_at += gap
                continue
            label, command, on = it
            t = c.create_text(pad + px(14), y_at + row / 2, text=label,
                              font=f, fill=FG if on else FIELD_LINE,
                              anchor="w")
            if on:
                lines.append((y_at, y_at + row, command, t))
            y_at += row

        def line_at(e):
            return next((ln for ln in lines if ln[0] <= e.y < ln[1]), None)

        def hover(e):
            ln = line_at(e)
            c.itemconfigure(bar, state="normal" if ln else "hidden")
            for _, _, _, t in lines:
                c.itemconfigure(t, fill=PILL_HOVER if ln and ln[3] == t
                                else FG)
            if ln:
                c.coords(bar, pad, ln[0])
                c.tag_lower(bar, ln[3])
            c.config(cursor="hand2" if ln else "")

        def click(e):
            ln = line_at(e)
            if ln:
                self.close()
                if ln[2]:
                    ln[2]()

        def lost(_e):
            """Focus gone from it (a click elsewhere): close, once settled."""
            def check():
                if self.win is win and not str(win.focus_get() or "") \
                        .startswith(str(win)):
                    self.close()
            win.after(50, check)

        c.bind("<Motion>", hover)
        c.bind("<Leave>", lambda e: c.itemconfigure(bar, state="hidden"))
        c.bind("<ButtonRelease-1>", click)
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<FocusOut>", lost)
        place_near(win, (x - 12, y - 12))       # its corner at the pointer
        win.deiconify()
        win.focus_force()


def details_window(parent, title, text, at=None):
    """Show details: `text` in a window of its own by the pointer (`at`),
    the same for a waypoint, a body or a building. CLOSE or Escape."""
    win, pane = popup(parent, title)
    lines = text.splitlines() or [""]
    box = text_box(pane, min(30, len(lines) + 1), readonly=True)
    box.config(width=max(40, min(90, max(len(ln) for ln in lines) + 2)),
               wrap="none")
    set_text(box, text)
    box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
    ttk.Button(pane, text="CLOSE", command=win.destroy).pack(pady=(0, 10))
    win.bind("<Escape>", lambda e: win.destroy())
    place_near(win, at)
    win.deiconify()
    win.focus_set()
    return win


def ask_buttons(title, text, parent, buttons=("OK",)):
    """A message in a window by the pointer, with a button per label in
    `buttons`; waits, and returns the label clicked. Enter is the first
    button; closing it or Escape returns None. In place of tkinter's
    messagebox, which Windows centres on the main window and draws in its
    own light colours."""
    win, pane = popup(parent, title)
    answer = {"got": None}
    lines = text.splitlines() or [""]
    box = text_box(pane, min(30, len(lines)), readonly=True)
    box.config(width=max(40, min(90, max(len(ln) for ln in lines) + 2)),
               wrap="word")
    set_text(box, text)
    box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
    row = tk.Frame(pane, bg=BG)
    row.pack(anchor="e", padx=10, pady=(0, 10))

    def choose(label):
        answer["got"] = label
        win.destroy()

    for label in buttons:
        ttk.Button(row, text=label, command=lambda b=label: choose(b)).pack(
            side="left", padx=(6, 0))
    win.bind("<Return>", lambda e: choose(buttons[0]))
    win.bind("<Escape>", lambda e: win.destroy())
    place_near(win)
    win.deiconify()
    win.focus_set()
    win.grab_set()
    win.wait_window()
    return answer["got"]


def ask_yes_no(title, text, parent):
    """True for Yes; No, Escape or closing it is False."""
    return ask_buttons(title, text, parent, ("Yes", "No")) == "Yes"


def show_message(title, text, parent):
    """A message to read, with OK."""
    ask_buttons(title, text, parent)


class Updates:
    """Whether a newer version is out, and putting it in place. GitHub is
    asked and the installer fetched on threads of their own; what they find
    is left here for the main window's tick, as only the main thread may
    touch a window."""

    def __init__(self, root, win, scl):
        self.root, self.win, self.scl = root, win, scl
        self.info = None        # updater.check's answer, once there is one
        self.checking = False
        self.fraction = None    # 0..1 while the installer comes in
        self.ready = None       # the installer, fetched and verified
        self.failed = None      # why it could not be fetched or started

    def start(self):
        """When the program opens: clear what the last update left, and
        look for the next. Only an installed program has one coming."""
        if self.scl.FROZEN:
            shutil.rmtree(self.scl.HERE / "update", ignore_errors=True)
            self.check()

    def check(self):
        if self.checking or self.fraction is not None:
            return
        self.checking = True
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self):
        self.info = updater.check()
        self.checking = False

    @property
    def available(self):
        return bool(self.info and self.info["available"])

    def banner(self):
        """The line beside the main window's switches; empty for none."""
        if self.fraction is not None:
            return f"Downloading update: {self.fraction:.0%}"
        if self.available:
            return f"Version {self.info['latest']} is out: click to update"
        return ""

    def offer(self):
        """What the update brings, and whether to install it now."""
        if not self.available or self.fraction is not None:
            return
        info = self.info
        text = (f"SC Locator {info['latest']} ({info['published']}) is out. "
                f"You have {updater.VERSION}.\n\n{info['notes'].strip()}\n\n")
        if not updater.can_install():
            text += ("This copy did not come from the installer, so it "
                     "cannot update itself.")
            if ask_buttons("Update available", text, self.win,
                           ("Release page", "Later")) == "Release page":
                webbrowser.open(updater.RELEASE_PAGE)
            return
        text += ("Install now closes SC Locator, installs the new version "
                 "and starts it. Your data is kept.")
        if ask_buttons("Update available", text, self.win,
                       ("Install now", "Later")) == "Install now":
            self.fraction = 0.0
            threading.Thread(target=self._download, args=(info,),
                             daemon=True).start()

    def _download(self, info):
        try:
            self.ready = updater.download(
                info, self.scl.HERE / "update" / updater.ASSET_NAME,
                lambda f: setattr(self, "fraction", f))
        except Exception as exc:
            self.failed = str(exc) or type(exc).__name__

    def refresh(self):
        """Act on what the download thread left: start the installer and
        close, by the normal way out so that everything is saved."""
        if self.ready:
            setup, self.ready = self.ready, None
            try:
                updater.start_install(setup)
            except OSError as exc:
                self.failed = str(exc)
            else:
                self.root.after_idle(self.root.destroy)
        if self.failed:
            reason, self.failed, self.fraction = self.failed, None, None
            # Not from inside the tick: this waits for its OK.
            self.win.after(1, show_message, "Update failed",
                           f"{reason}\n\nNothing was changed. The new version "
                           f"is also at {updater.RELEASE_PAGE}", self.win)


class Navigator:
    """The way between the tabs, and the right-click menus they share.

    One menu per kind of thing -- a waypoint, a body (planet or moon), a
    building -- the same wherever it is right-clicked. An entry that does
    not apply is left out rather than greyed out, and so is the one for the
    tab the menu is opened from (`here`: "star", "height", "list" or
    "reports"). open_main sets the tabs once they exist.
    """

    def __init__(self, nb, state, scl):
        self.nb, self.state, self.scl = nb, state, scl
        self.star = self.height = self.editor = self.reports = None
        self.surveys = self.maps = self.settings = None

    # --- going to a tab -------------------------------------------------

    def to_star(self, wid):
        self.nb.select(self.star.frame)
        self.star.show_waypoint(wid)

    def to_height(self, wid):
        self.nb.select(self.height.frame)
        self.height.show_waypoint(wid)

    def to_list(self, wid):
        self.nb.select(self.editor.frame)
        self.editor.select(wid)

    def open_pin(self, wid):
        """A pin clicked on the star map: on the height map if it has ground
        under it, else in the Waypoints tab."""
        w = self.scl.find_waypoint(snapshot(self.state)[0], wid)
        if w is not None and self.scl.has_ground(w, self.state.cfg):
            self.to_height(wid)
        else:
            self.to_list(wid)

    def body_system(self, body):
        with self.state.lock:
            rec = self.state.bodies.get(body) or {}
        return self.scl.BODY_SYSTEM.get(body) or rec.get("system")

    def body_to_star(self, body):
        self.nb.select(self.star.frame)
        self.star.show(self.body_system(body), body)

    def back_to_star(self, body):
        """From the height map to `body`'s view on the star map, left as it
        was if the star map is still showing that body."""
        self.nb.select(self.star.frame)
        if self.star.body != body:
            self.star.show(self.body_system(body), body)

    def body_to_height(self, body):
        self.nb.select(self.height.frame)
        self.height.body_var.set(body)
        self.height.open(body)

    def map_to_height(self, m):
        """Reposition map `m` on the height map, then back to it in the
        Maps tab."""
        self.nb.select(self.height.frame)

        def back():
            self.nb.select(self.maps.frame)
            self.maps.select(m["id"])

        self.height.frame_map(m, back)

    def trace_to_height(self, name):
        self.nb.select(self.height.frame)
        self.height.show_trace(name)

    def body_to_report(self, body):
        self.nb.select(self.reports.frame)
        self.reports.select(body)

    def body_to_list(self, body):
        self.nb.select(self.editor.frame)
        self.editor.show_body(self.body_system(body), body)

    def refresh_tabs(self):
        """After a rename: every tab shows it now, not at the next tick."""
        for tab in (self.star, self.height, self.editor):
            tab.refresh()

    # --- Show details ---------------------------------------------------

    def here(self):
        """Where you are, for a waypoint's distance from you."""
        st = self.state
        with st.lock:
            return {"system": st.system, "planet": st.planet,
                    "latlon": st.latlon if st.pos else None,
                    "radius": st.radius, "sys_pos": st.sys_pos}

    def waypoint_details(self, wid, at=None):
        wps, bodies = snapshot(self.state)
        w = self.scl.find_waypoint(wps, wid)
        if w is None:
            return
        system = w.get("system")
        placed = (starmap.placed_bodies(bodies, system)
                  if system in starmap.SYSTEMS else {})
        text = self.scl.waypoint_details_text(
            w, starmap.attached_body(w, placed), self.here())
        details_window(self.nb, w["name"], text, at)

    def body_details(self, body, at=None):
        st = self.state
        wps, bodies = snapshot(st)
        rec = bodies.get(body)
        report = self.reports.lines_for(body) if rec else None
        with st.lock:
            cells = heightmap.cell_count(st.heightmap, body)
            trs = list(st.traces)
            radius = ((rec or {}).get("radius_m")
                      or self.scl.BODY_RADII.get(body))
        text = self.scl.body_details_text(body, self.body_system(body), report,
                                          wps, cells, trs, radius)
        details_window(self.nb, body.upper(), text, at)

    def building_details(self, name, at=None):
        st = self.state
        with st.lock:
            t = next((dict(t) for t in st.traces if t["name"] == name), None)
            if t is None:
                return
            rec = st.heightmap.get(t["planet"])
            radius = ((rec or {}).get("radius")
                      or self.scl.BODY_RADII.get(t["planet"], 1e6))
            # One walked before offset_m was kept takes today's setting.
            offset = t.setdefault("offset_m", st.cfg["building_wall_offset_m"])
        frame = heightmap.local_frame(t["points"][0])
        xys = [heightmap.to_map(q, frame, radius) for q in t["points"]]
        text = self.scl.building_details_text(
            t, traces.building_facts(xys, offset, t.get("shape", "squared")))
        details_window(self.nb, t.get("label") or name, text, at)

    # --- renaming -------------------------------------------------------

    def rename_waypoint(self, wid, at=None):
        """A waypoint's new name; it can be one in use (many are a Cave)."""
        st, scl = self.state, self.scl
        with st.lock:
            w = scl.find_waypoint(st.waypoints, wid)
            if w is None:
                return
            old = w["name"]
            given = sorted({q["name"] for q in st.waypoints}, key=str.lower)
        new = ask_name(self.nb, "Waypoint name", f"{old} ({w['planet']})",
                       old, given, at, can_clear=False)
        if new is None or new == old:
            return
        with st.lock:
            try:
                scl.rename_waypoint(st.waypoints, wid, new)
            except ValueError as exc:
                st.flash(str(exc))
                return
            scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            st.flash(f"renamed to {new}")
        self.refresh_tabs()

    def edit_waypoint(self, wid, at=None):
        """Every field of a waypoint in one window (see ask_waypoint)."""
        st, scl = self.state, self.scl
        wps, bodies = snapshot(st)
        w = scl.find_waypoint(wps, wid)
        if w is None:
            return
        names = sorted({q["name"] for q in wps}, key=str.lower)
        around = None
        if w.get("frame") == "system":
            around = sorted(starmap.placed_bodies(bodies, w.get("system")))
        got = ask_waypoint(self.nb, w, names, around, scl.NO_SUB, at)
        if got is None:
            return
        with st.lock:
            try:
                scl.update_waypoint(st.waypoints, wid, **got)
            except ValueError as exc:
                st.flash(str(exc))
                return
            scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            st.flash(f"saved {got['name'].strip()}")
        self.refresh_tabs()
        if got.get("private") or got.get("share") is False:
            offer_unpost(self, [("waypoint", wid)], "share", False, self.nb)

    def delete_waypoint(self, wid):
        """Delete a waypoint, once asked; a target that was it is cleared."""
        st, scl = self.state, self.scl
        wps, _ = snapshot(st)
        w = scl.find_waypoint(wps, wid)
        if w is None or not ask_yes_no(
                "Delete waypoint", f"Delete '{w['name']}' ({w['planet']})?",
                parent=self.nb):
            return
        with st.lock:
            try:
                scl.delete_waypoint(st.waypoints, wid)
            except ValueError as exc:
                st.flash(str(exc))
                return
            if st.target == wid:
                st.target = None
            scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            st.flash(f"deleted {w['name']}")
        self.refresh_tabs()

    def rename_trace(self, name, kind, label, at=None):
        """Name a building or a line (a kind that is named, traces.KINDS),
        from the names given before to that kind."""
        with self.state.lock:
            given = self.scl.trace_labels(self.state.traces, kind)
        new = ask_name(self.nb, traces.KINDS[kind]["menu"].capitalize()
                       + " name", name, label or "", given, at)
        if new is not None and new != (label or ""):
            self.scl.label_trace(self.state, name, new)
            self.height.draw()

    # --- the menus ------------------------------------------------------
    # Each fills `menu` (already emptied) and returns whether it added any.

    def waypoint_menu(self, menu, wid, here, at):
        wps, _ = snapshot(self.state)
        w = self.scl.find_waypoint(wps, wid)
        if w is None:
            return False
        menu.title = menu.title or w["name"]
        add = menu.add_command
        add(label="Show details", command=lambda: self.waypoint_details(wid,
                                                                        at))
        # In its own body's view: turned to the middle, where the wheel
        # zooms straight in on it.
        if (here == "star" and w.get("frame", "planet") == "planet"
                and self.star.body == w.get("planet")):
            add(label="Centre", command=lambda: self.star.centre_on(wid))
        if here != "star" and w.get("system") in starmap.SYSTEMS:
            add(label="Show on star map", command=lambda: self.to_star(wid))
        if here != "height" and self.scl.has_ground(w, self.state.cfg):
            add(label="Show on height map", command=lambda: self.to_height(wid))
        if here != "list":
            add(label="Show in Waypoints", command=lambda: self.to_list(wid))
        add(label="Rename...", command=lambda: self.rename_waypoint(wid, at))
        add(label="Edit...", command=lambda: self.edit_waypoint(wid, at))
        add(label="Delete...", command=lambda: self.delete_waypoint(wid))
        return True

    def poi_list_menu(self, menu, wid, at):
        """A POI's menu in the star map's list: where else to see it."""
        wps, bodies = snapshot(self.state)
        w = self.scl.find_waypoint(wps, wid)
        if w is None:
            return False
        report = self.scl.waypoint_report(w, bodies, wps)
        menu.title = menu.title or w["name"]
        add = menu.add_command
        add(label="Show on star map", command=lambda: self.to_star(wid))
        add(label="Show info", command=lambda: self.waypoint_details(wid, at))
        add(label="Show in waypoints", command=lambda: self.to_list(wid))
        if report is not None:
            add(label="Show in reports",
                command=lambda: self.body_to_report(report))
        return True

    def body_menu(self, menu, body, here, at, opened=False):
        """A planet's or moon's menu. `opened`: the star map is already
        showing this body, so it has no Open on star map."""
        st = self.state
        with st.lock:
            analysed = body in st.bodies
            mapped = (body in st.heightmap
                      or any(t["planet"] == body for t in st.traces))
            marked = any(w.get("planet") == body for w in st.waypoints)
        menu.title = menu.title or body
        add = menu.add_command
        add(label="Show details", command=lambda: self.body_details(body, at))
        if (self.body_system(body) in starmap.SYSTEMS
                and not (here == "star" and opened)):
            add(label="Open on star map",
                command=lambda: self.body_to_star(body))
        if mapped and here != "height":
            add(label="Show on height map",
                command=lambda: self.body_to_height(body))
        if analysed and here != "reports":
            add(label="Show report", command=lambda: self.body_to_report(body))
        if marked and here != "list":
            add(label="Show waypoints", command=lambda: self.body_to_list(body))
        return True

    def building_menu(self, menu, name, label, at):
        menu.title = menu.title or label or name
        add = menu.add_command
        add(label="Show details", command=lambda: self.building_details(name,
                                                                        at))
        add(label="Rename...", command=lambda: self.rename_trace(
            name, "building", label, at))
        # Squared suits most; one with arms and diagonals is drawn as walked.
        with self.state.lock:
            walked = any(t["name"] == name and t.get("shape") == "walked"
                         for t in self.state.traces)
        add(label="Draw squared" if walked else "Draw as walked",
            command=lambda: self.shape_building(
                name, "squared" if walked else "walked"))
        return True

    def shape_building(self, name, shape):
        self.scl.shape_building(self.state, name, shape)
        self.height.draw()


def scrolled(parent):
    """A frame filling `parent` that scrolls up and down, by its bar or the
    mouse wheel, when what is in it is taller than the window; the bar
    shows only then. Returns the frame to put things in."""
    canvas = tk.Canvas(parent, bg=BG, highlightthickness=0)
    bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=BG)
    item = canvas.create_window(0, 0, window=inner, anchor="nw")
    canvas.configure(yscrollcommand=bar.set)
    canvas.pack(side="left", fill="both", expand=True)

    def fit(_e=None):
        canvas.itemconfigure(item, width=canvas.winfo_width())
        canvas.configure(scrollregion=(0, 0, canvas.winfo_width(),
                                       inner.winfo_reqheight()))
        if inner.winfo_reqheight() > canvas.winfo_height():
            if not bar.winfo_ismapped():
                bar.pack(side="right", fill="y", before=canvas)
        elif bar.winfo_ismapped():
            bar.pack_forget()
            canvas.yview_moveto(0)

    def wheel(e):
        if bar.winfo_ismapped():
            canvas.yview_scroll(-e.delta // 120, "units")

    inner.bind("<Configure>", fit)
    canvas.bind("<Configure>", fit)
    # The wheel scrolls it only while the pointer is over it.
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
    return inner


def text_box(parent, height, readonly=False):
    t = tk.Text(parent, height=height, font=mono(), bg=PANEL, fg=FG,
                insertbackground=FG, relief="flat", wrap="word")
    if readonly:
        t.config(state="disabled")
    return t


def set_text(widget, text):
    readonly = widget.cget("state") == "disabled"
    widget.config(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", text)
    if readonly:
        widget.config(state="disabled")


# ----------------------------------------------------------------------------
# Star map
# ----------------------------------------------------------------------------


class StarMap:
    """One system at a time; click a body to zoom in on it and its waypoints."""

    def __init__(self, parent, state, scl, nav, follow):
        self.state, self.scl, self.nav = state, scl, nav
        self.follow = follow
        # canvas item -> waypoint id. Not a tag: one per waypoint, kept with
        # the items it covers.
        self.pins = {}
        self.subs = {}          # waypoint id -> its SUB, for its icon
        self.names = {}         # waypoint id -> its name, for its label
        self.marked = None      # id of the waypoint ringed by SHOW ON MAP
        self.backdrops, self.ramp = {}, None
        self.help = ""          # the how-to line the ? pill shows
        self.frame = tk.Frame(parent, bg=BG)
        self.system, self.body = starmap.SYSTEMS[0], None
        self.up = None          # the body "back" opens; None: the system view
        self.yaw, self.pitch, self.zoom = 0.0, SYSTEM_PITCH, 1.0
        self.press, self.dragged, self.seen = None, False, None
        self.ticks = 0
        self.om = None          # (lat, lon) of the marker the view sits above
        self.turn = (0.0, 0.0)  # (yaw, pitch) dragged away from you, following
        # Your (system, body) when the view was moved by hand: follow me waits
        # until you leave it, so the view is not pulled straight back.
        self.held = None
        # (fix time, system point) last shown for you, the fix time last
        # judged, and how many fixes in a row were refused as misreads.
        self.track, self.track_t, self.refused = None, None, 0
        self.mesh_key, self.mesh_tiles = None, ([], 0.0, 0.0)

        # The map fills the tab whatever its menu does: the menu is drawn
        # over it (PillMenu), so opening a section never moves or zooms it.
        self.canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Button-3>", self.context_menu)
        # Each block of the menu is a pill that opens into a box. The HUD's
        # functions are here too, for a second screen without the HUD.
        self.pill_menu = PillMenu(self.canvas)
        sec = self.section("Location")
        self.location = tk.Label(sec.body, font=mono(9), fg=FG, bg=BG,
                                 justify="left", anchor="w")
        self.location.pack(anchor="w", fill="x")
        # The report on this place under where you are, as on the HUD.
        self.report_btn = ttk.Button(sec.body, text="REPORT", width=8,
                                     command=self.report_clicked)
        self.report_btn.pack(anchor="w", pady=(6, 0))
        self.report = tk.Label(sec.body, font=mono(9), fg=FG, bg=PANEL,
                               justify="left", anchor="w", padx=4, pady=4)
        self.location_sec = sec
        if state.cfg["menu_sections"].get("Report"):    # as it was left
            self.toggle_report(True, open_section=False)
        sec = self.section("System")
        self.system_var = tk.StringVar(value=self.system)
        pick = ttk.Combobox(sec.body, textvariable=self.system_var,
                            state="readonly", values=starmap.SYSTEMS, width=14)
        pick.pack(anchor="w", pady=(0, 8))
        pick.bind("<<ComboboxSelected>>",
                  lambda e: self.show(self.system_var.get(), None))
        # Survey markers would crowd the map: shown only when asked for, and
        # their switch only in a body's view, the system view has none.
        self.type_boxes = {}
        self.types = type_switches(sec.body, ("poi",), self.draw,
                                   self.type_boxes)
        self.type_boxes["survey"].pack_forget()
        self.back = ttk.Button(sec.body, text="< back to system",
                               command=lambda: self.show(self.system, self.up))
        # Packed by show, in a body's view: the system view has no way back.

        # The way to the target, the HUD's: only while there is one.
        self.target_sec = self.section("Target")
        self.target_sec.shown = False
        self.target_text = tk.Label(self.target_sec.body, font=mono(9), fg=FG,
                                    bg=BG, justify="left", anchor="w")
        self.target_text.pack(anchor="w", fill="x")
        self.compass = tk.Canvas(self.target_sec.body, width=self.scl.ARROW_PX,
                                 height=self.scl.ARROW_PX, bg=BG,
                                 highlightthickness=0)
        self.compass.pack(pady=(4, 0))

        # SURVEY as on the HUD: its menu, then END and the directions while
        # one runs. Folded until one starts.
        self.survey_sec = self.section("Survey")
        row = tk.Frame(self.survey_sec.body, bg=BG)
        row.pack(fill="x")
        # Wide enough for END BUILDING, so the label can swap.
        self.survey_btn = ttk.Button(row, text="SURVEY", width=14,
                                     command=self.on_survey)
        self.survey_btn.pack(side="left")
        self.cancel_btn = ttk.Button(          # packed while tracing
            row, text="CANCEL",
            command=lambda: self.run("cancel", self.scl.cancel_trace))
        # SURVEY's choices, in this box rather than a Windows menu
        # (2026-11-22): open while SURVEY is on, built afresh each time.
        self.survey_choices = tk.Frame(self.survey_sec.body, bg=BOX)
        self.survey_rows = tk.Frame(self.survey_sec.body, bg=BG)
        self.survey_lbls = [tk.Label(self.survey_rows, font=mono(9), fg=FG,
                                     bg=BG, justify="left", anchor="w")
                            for _ in range(3)]
        for lbl in self.survey_lbls:
            lbl.pack(anchor="w", fill="x")
        self.surveying = False

        # ANALYSE as on the HUD; the report is in the Location section.
        # Folded until one starts.
        self.analysis_sec = self.section("Analysis")
        body = self.analysis_sec.body
        self.analysis_text = tk.Label(body, font=mono(9), fg=FG, bg=BG,
                                      justify="left", anchor="w")
        self.analysis_text.pack(anchor="w", fill="x")
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        self.analyse_btn = ttk.Button(row, text="ANALYSE", width=9,
                                      command=self.on_analyse)
        self.analyse_btn.pack(side="left")
        self.label_row = row = tk.Frame(body, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        tk.Label(row, text="label  ", font=mono(9), fg=DIM, bg=BG).pack(
            side="left")
        self.label_var = tk.StringVar(value=self.scl.NO_LABEL)
        pick = ttk.Combobox(row, textvariable=self.label_var, width=18,
                            state="readonly", values=self.scl.PLACE_LABELS)
        pick.pack(side="left")
        pick.bind("<<ComboboxSelected>>", lambda e: self.run(
            "label", lambda st: self.scl.label_place(st, self.label_var.get())))
        # Only for a place in space: a station is where the ship is parked.
        self.station_row = tk.Frame(body, bg=BG)
        tk.Label(self.station_row, text="station", font=mono(9), fg=DIM,
                 bg=BG).pack(side="left")
        self.station_btn = ttk.Menubutton(self.station_row, width=18)
        self.station_btn.pack(side="left")
        self.station_menu = PopMenu(self.station_btn, tearoff=False,
                                    font=mono(), bg=PANEL, fg=FG,
                                    activebackground=PICK,
                                    postcommand=self.fill_station_menu)
        # Its own menu, not the Windows one a Menubutton would post.
        self.station_btn.bind("<ButtonPress-1>", lambda e: (
            setattr(self.station_menu, "title", "Station"),
            self.station_menu.tk_popup(
                e.widget.winfo_rootx(),
                e.widget.winfo_rooty() + e.widget.winfo_height()),
            "break")[-1])
        # After a run that wrote a result: keep it or put the old one back.
        self.review_row = tk.Frame(body, bg=BG)
        ttk.Button(self.review_row, text="KEEP",
                   command=self.keep_analysis).pack(side="left")
        ttk.Button(self.review_row, text="DISCARD",
                   command=lambda: self.run("discard",
                                            self.scl.discard_analysis)).pack(
            side="left", padx=(6, 0))
        self.analysing = self.reviewing = False

        # The waypoints of the system you are in (all when none is known),
        # whichever view is open; SAVE and TARGET as on the HUD.
        self.poi_seen, self.poi_closed = None, set()
        self.targeted = None    # the target the Target section was shown for
        self.wp_sec = self.section("Waypoints")
        box = self.wp_sec.body
        tk.Label(box, text="Search", font=mono(), fg=DIM, bg=BG).pack(anchor="w")
        self.poi_search = tk.StringVar()
        ttk.Entry(box, textvariable=self.poi_search, font=mono(),
                  width=10).pack(anchor="w", fill="x", pady=(0, 6))
        self.poi_search.trace_add("write", lambda *_: self.fill_waypoints())
        buttons = tk.Frame(box, bg=BG)
        buttons.pack(side="bottom", fill="x", pady=(6, 0))
        # Each as wide as its word (width 0: not the buttons' usual 11
        # letters at least), so the four fit the menu (2026-11-22).
        ttk.Button(buttons, text="SAVE", width=0,
                   command=self.save_here).pack(side="left")
        # At least about as wide as UNTARGET (negative: a minimum), so the
        # label can swap without the others moving far; never cut.
        self.target_btn = ttk.Button(buttons, text="TARGET", width=-9,
                                     command=self.target_picked)
        self.target_btn.pack(side="left", padx=(4, 0))
        ttk.Button(buttons, text="EDIT", width=0,
                   command=lambda: self.on_picked(self.nav.edit_waypoint)).pack(
            side="left", padx=(4, 0))
        ttk.Button(buttons, text="DELETE", width=0,
                   command=lambda: self.on_picked(self.nav.delete_waypoint)).pack(
            side="left", padx=(4, 0))
        # Names only (2026-11-22), as wide as name and distance were: that
        # width sets the whole menu's. A name too long ends in "...".
        self.pois = ttk.Treeview(box, show="tree", selectmode="browse")
        set_column(self.pois, "#0", POI_LIST_PX)
        scroll = self.poi_scroll = ttk.Scrollbar(box, orient="vertical",
                                                 command=self.pois.yview)
        self.pois.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.pois.pack(side="left", fill="both", expand=True)
        self.pois.bind("<Button-3>", self.poi_menu)
        self.pois.bind("<<TreeviewSelect>>", lambda e: self.label_target_btn())

        # Title, hint and list in one box, so it is plain which list the
        # words are about. Last in the menu.
        self.box = self.section("", key="Not analysed yet")
        box = self.box.body
        self.unplaced_hint = tk.Label(box, font=mono(9), fg=DIM, bg=BG,
                                      justify="left", anchor="w")
        self.unplaced_hint.pack(anchor="w", fill="x", pady=(0, 6))
        self.unplaced = tk.Listbox(box, font=mono(), bg=PANEL, fg=FG, width=22,
                                   height=4, relief="flat", bd=0,
                                   highlightthickness=0,
                                   selectbackground=PICK)
        self.unplaced.pack(fill="both", expand=True)
        self.unplaced.bind("<<ListboxSelect>>", self.open_unplaced)
        self.unplaced.bind("<Button-3>", self.unplaced_menu)
        for s in self.pill_menu.items:
            on_box(s.body)
        self.pill_menu.layout()

        # Right-click on a pin or a body: the menus every tab shares.
        self.menu = PopMenu(self.canvas, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground=PICK)
        # View from an orbital marker. Over the canvas, top right; only shown
        # while a body is open, as the system view has no markers.
        self.om_bar = tk.Frame(self.canvas, bg=BG)
        tk.Label(self.om_bar, text="view from", font=mono(9), fg=DIM,
                 bg=BG).pack(side="left", padx=(0, 4))
        for name, lat, lon in scl.OM_POINTS:
            ttk.Button(self.om_bar, text=name, width=5,
                       command=lambda la=lat, lo=lon: self.view_om(la, lo)).pack(
                side="left", padx=1)
        # Surveyed ground is raised by this much, or it would not show at all:
        # a few km of relief on a 1000 km body is under a pixel.
        tk.Label(self.om_bar, text="relief x", font=mono(9), fg=DIM,
                 bg=BG).pack(side="left", padx=(8, 2))
        self.relief = tk.StringVar(value="20")
        relief = ttk.Combobox(self.om_bar, textvariable=self.relief, width=4,
                              state="readonly",
                              values=("1", "5", "10", "20", "50", "100"))
        relief.pack(side="left")
        relief.bind("<<ComboboxSelected>>", lambda e: self.draw())
        self.canvas.tag_bind("pin", "<Enter>",
                             lambda e: self.canvas.config(cursor="hand2"))
        self.canvas.tag_bind("pin", "<Leave>",
                             lambda e: self.canvas.config(cursor=""))

    def section(self, title, key=None):
        return kept_section(self.state, self.scl, self.pill_menu, title, key,
                            make=MenuPill)

    # --- navigation ---------------------------------------------------------

    def show(self, system, body, marked=None, keep_follow=False):
        if not keep_follow:             # moved by hand: hold it there
            self.hold()
        self.system, self.body, self.zoom, self.yaw = system, body, 1.0, 0.0
        self.om, self.marked, self.turn = None, marked, (0.0, 0.0)
        self.system_var.set(system)
        survey = self.type_boxes["survey"]
        if body is None:
            self.om_bar.place_forget()
            survey.pack_forget()
            self.back.pack_forget()
        else:
            self.om_bar.place(relx=1.0, x=-10, y=10, anchor="ne")
            survey.pack(anchor="w", after=self.type_boxes["poi"])
            self.back.pack(anchor="w", fill="x", pady=(8, 0))
        self.pitch = SYSTEM_PITCH if body is None else math.radians(20)
        self.draw()

    def view_om(self, lat, lon):
        """Look down on the body from above one of its orbital markers, at
        the zoom already set. The view stays there as the body turns."""
        self.hold()
        self.om, self.turn = (lat, lon), (0.0, 0.0)
        self.draw()

    def recentre(self):
        """follow me ticked or unticked: drop any drag or hold away from you."""
        self.turn, self.held = (0.0, 0.0), None
        self.refresh()
        self.draw()

    def hold(self):
        """Keep a view picked by hand until you move on."""
        self.held = self.where_am_i()[:2]

    def where_am_i(self):
        """(system, body, planet-frame pos, system-frame pos) from the
        overlay's fix; body and pos are None off a body. The last body fix is
        kept after you leave its range, so one SPACE_AFTER old counts as
        deep space.

        The system position comes from the body fix where the body's centre
        and spin are known: the game's own freezes for a whole quantum jump,
        while the body fix keeps up."""
        with self.state.lock:
            st = self.state
            on_body = (st.pos is not None
                       and st.planet not in (None, "unknown")
                       and time.time() - st.fix_time <= SPACE_AFTER)
            # Turned as the body was when the fix was read, not as it is now.
            sys_pos = (starmap.system_point(st.bodies.get(st.planet), st.pos,
                                            st.fix_time) if on_body else None)
            me = (st.system, st.planet if on_body else None,
                  st.pos if on_body else None, st.sys_pos, st.fix_time)
        if sys_pos is not None:
            sys_pos = self.believe(me[4], sys_pos)
        return me[:3] + (sys_pos or me[3],)

    def believe(self, t, p):
        """The system point to show for the fix read at t: p, unless getting
        there from the last one would outrun light. Two refusals in a row at
        most, in case it was the last one that was misread."""
        if t != self.track_t:
            self.track_t = t
            if (self.track is not None and self.refused < 2
                    and starmap.faster_than_light(self.track, (t, p))):
                self.refused += 1
            else:
                self.track, self.refused = (t, p), 0
        return self.track[1]

    def follow_me(self, me):
        """Keep you in view without leaving the kind of view you are on: the
        system view shows your system, the body view your body, turned so you
        face the viewer. In deep space the body view holds where it is. A drag
        shifts the view from you (self.turn) and leaves it following."""
        system, body, pos, _ = me
        if self.held is not None:
            if not moved_on(self.held, me):
                return
            self.held = None
        if system not in starmap.SYSTEMS:
            return
        if self.body is None:
            if system != self.system:
                self.show(system, None, keep_follow=True)
            return
        if body is None:
            return
        if body != self.body:
            self.show(system, body, keep_follow=True)
        self.om = starmap.facing(pos)

    def show_waypoint(self, wid):
        """Open the view a waypoint is on, turned so it faces you."""
        wps, bodies = snapshot(self.state)
        w = self.scl.find_waypoint(wps, wid)
        if w is not None:                   # of a type not shown: show it
            self.types.get(w.get("type"), self.types["poi"]).set(True)
        where = starmap.waypoint_view(w, bodies) if w else None
        if where is None:
            name = (w or {}).get("name", "that waypoint")
            with self.state.lock:
                self.state.flash(f"{name}: system unknown, not on the star map")
            return
        system, body, om = where
        self.show(system, body, wid)
        if om is not None:
            self.view_om(*om)

    def centre_on(self, wid):
        """Turn the body view so waypoint wid is in the middle, at the zoom
        already set, and keep it there as the body turns."""
        w = self.scl.find_waypoint(snapshot(self.state)[0], wid)
        if w is not None:
            self.view_om(*starmap.facing((w["x"], w["y"], w["z"])))

    def fill_waypoints(self):
        """The Waypoints list, for the system you are in and the search.
        Rebuilt only when what it lists changes (or the Windows scale, which
        changes where names are cut), so a refresh keeps its selection and
        scroll. Every group is open during a search; after it, as they were
        left."""
        wps, _ = snapshot(self.state)
        text = self.poi_search.get()
        ids = {w["id"] for w in self.scl.search_waypoints(wps, text)}
        with self.state.lock:
            system = self.state.system
        rows = waypoint_tree(self.scl.waypoint_groups(self.state), system,
                             ids, self.scl.fmt_dist)
        seen = (bool(text.strip()), system,
                [(g, [r[:2] for r in members]) for g, members in rows],
                _dpi[0])
        tree = self.pois
        if seen == self.poi_seen:
            self.label_target_btn()
            return

        def cut(name, depth):
            """`name` cut to fit at `depth` (0 a group, 1 a waypoint): the
            tree's indent and open arrow take the left of it."""
            return fit_text(name, px(POI_LIST_PX) - (depth + 1) * 20 - px(8),
                            mono().measure)
        if self.poi_seen and not self.poi_seen[0]:
            shown = set(tree.get_children())
            self.poi_closed = (self.poi_closed - shown) | {
                g for g in shown if not tree.item(g, "open")}
        picked = tree.selection()
        tree.delete(*tree.get_children())
        if not rows:
            tree.insert("", "end", iid="none", text=(
                "none found" if seen[0] else f"none in {system} yet"
                if system else "no waypoints saved yet"))
        for group, members in rows:
            iid = f"group:{group}"
            tree.insert("", "end", iid=iid,
                        text=cut(f"{group} ({len(members)})", 0),
                        open=seen[0] or iid not in self.poi_closed)
            for wid, name, _ in members:
                tree.insert(iid, "end", iid=f"wp:{wid}", text=cut(name, 1))
        if picked and tree.exists(picked[0]):
            tree.selection_set(picked[0])
            tree.see(picked[0])
        self.poi_seen = seen
        self.label_target_btn()

    def picked_waypoint(self):
        """The id of the waypoint selected in the list, or None."""
        sel = self.pois.selection()
        return sel[0][3:] if sel and sel[0].startswith("wp:") else None

    def label_target_btn(self):
        """TARGET, or UNTARGET when the selected waypoint is the target."""
        with self.state.lock:
            target = self.state.target
        on = target is not None and self.picked_waypoint() == target
        self.target_btn.config(text="UNTARGET" if on else "TARGET")

    def save_here(self):
        """SAVE: a waypoint where you are, as the HUD's SAVE."""
        self.scl.on_hotkey(self.state, "save", self.scl.save_waypoint)()
        self.fill_waypoints()

    def target_picked(self):
        """TARGET: the selected waypoint becomes the target, or stops being
        it, as the HUD's TARGET."""
        wid = self.picked_waypoint()
        self.scl.on_hotkey(self.state, "target",
                           lambda st: self.scl.toggle_target(st, wid))()
        self.fill_target()
        self.label_target_btn()

    def fill_target(self):
        """The Target section: shown while there is a target, opened each
        time a new one is set, with the HUD's directions and turn arrow."""
        with self.state.lock:
            target = self.state.target
        sec = self.target_sec
        if (target is not None) != sec.shown:
            sec.show(target is not None)
        if target is not None:
            if target != self.targeted:
                sec.fold(True)
            self.target_text.config(text=self.scl.render_target(self.state))
            self.scl.compass_arrow(self.compass,
                                   self.scl.target_turn(self.state))
        self.targeted = target

    # --- the HUD's functions ----------------------------------------------

    def run(self, key, fn):
        """A button's action, logged as the HUD logs its own (on_hotkey),
        then the menu brought up to date."""
        self.scl.on_hotkey(self.state, key, fn)()
        self.fill_sections()

    def fill_sections(self):
        """The sections that follow the game: once a second, and after a
        button."""
        self.fill_location()
        self.fill_target()
        self.fill_survey()
        self.fill_analysis()
        self.wrap_readouts()
        self.pill_menu.layout()     # the Windows scale may have changed

    def wrap_readouts(self):
        """A readout line too long for the menu wraps at the boxes' right
        edge rather than widening every box; short ones stay as they are.
        The edge is the Waypoints list's, which sets the menu's width."""
        edge = self.pois.winfo_reqwidth() + self.poi_scroll.winfo_reqwidth()
        for lbl in (self.location, self.target_text, self.analysis_text,
                    self.unplaced_hint, *self.survey_lbls):
            if int(lbl["wraplength"]) != edge:
                lbl.config(wraplength=edge)
        if int(self.report["wraplength"]) != edge - 8:     # its padx
            self.report.config(wraplength=edge - 8)

    def fill_location(self):
        """The HUD's location readout, in its colour for the fix's health."""
        scl = self.scl
        self.location.config(text=scl.render_location(self.state),
                             fg=scl.STATUS_COLOURS[scl.health(self.state)])
        if self.report.winfo_manager():
            self.report.config(text="\n".join(scl.body_report(self.state)))

    def on_picked(self, fn):
        """EDIT or DELETE: fn(id) on the selected waypoint (the Navigator's
        Edit... and Delete, as everywhere else in the window)."""
        wid = self.picked_waypoint()
        if wid is None:
            with self.state.lock:
                self.state.flash("select a waypoint first")
            return
        fn(wid)
        self.fill_waypoints()

    def on_survey(self):
        """SURVEY: its menu; END while one runs, then the name of a
        building or line just traced."""
        st, scl = self.state, self.scl
        if st.trace is not None:
            ended = st.trace
            self.run("trace", scl.stop_trace)
            if scl.to_name(st, ended):
                self.nav.rename_trace(ended["name"], ended["kind"], None)
        elif st.survey:
            self.run("survey", scl.stop_survey)
        else:
            self.show_choices(not self.survey_choices.winfo_manager())

    def show_choices(self, show):
        """SURVEY's choices under it, in this box (True), or put away. A
        heading per group and its choices as pills, as many to a row as fit
        the menu's width; a pick starts it and puts them away."""
        box = self.survey_choices
        for w in box.winfo_children():
            w.destroy()
        self.survey_btn.state(["pressed" if show else "!pressed"])
        if not show:
            box.pack_forget()
            return
        edge = self.pois.winfo_reqwidth() + self.poi_scroll.winfo_reqwidth()

        def pick(key, fn):
            self.show_choices(False)
            self.run(key, fn)

        for heading, choices in choice_groups(
                self.scl.survey_menu_items(self.state)):
            tk.Label(box, text=heading, font=mono(9), fg=DIM, bg=BOX).pack(
                anchor="w", pady=(6, 2))
            row, used = None, edge
            for label, key, fn in choices:
                # A pill's width: its text and the pills' padding (pill_theme).
                w = ui_font().measure(label) + 2 * px(14) + px(6)
                if used + w > edge:             # a new row
                    row, used = tk.Frame(box, bg=BOX), 0
                    row.pack(anchor="w", pady=(0, px(4)))
                ttk.Button(row, text=label, style="Box.TButton", width=0,
                           command=lambda k=key, f=fn: pick(k, f)).pack(
                    side="left", padx=(0, px(6)))
                used += w
        box.pack(fill="x", pady=(4, 0), after=self.survey_btn.master)

    def fill_survey(self):
        """The Survey section: END and CANCEL while one runs, the HUD's
        survey block under them; opened when one starts."""
        st, scl = self.state, self.scl
        with st.lock:
            tracing, surveying = st.trace, st.survey
        busy = tracing is not None or bool(surveying)
        self.survey_btn.config(
            text=scl.end_label(tracing["kind"]) if tracing is not None
            else "END SURVEY" if surveying else "SURVEY")
        if (tracing is not None) != bool(self.cancel_btn.winfo_manager()):
            if tracing is not None:
                self.cancel_btn.pack(side="left", padx=(6, 0))
            else:
                self.cancel_btn.pack_forget()
        if busy != bool(self.survey_rows.winfo_manager()):
            if busy:
                self.survey_rows.pack(fill="x", pady=(6, 0))
            else:
                self.survey_rows.pack_forget()
        if busy and not self.surveying:
            self.survey_sec.fold(True)
            if self.survey_choices.winfo_manager():   # started elsewhere
                self.show_choices(False)
        self.surveying = busy
        if busy:
            *texts, colour = scl.survey_parts(st)
            for i, (lbl, text) in enumerate(zip(self.survey_lbls, texts)):
                bold = i == 1 and colour is not None
                lbl.config(text=text, fg=colour if bold else FG,
                           font=mono(9, bold))

    def on_analyse(self):
        """ANALYSE starts a run; STOP ends it and keeps what it has."""
        scl = self.scl

        def go(st):
            if st.analysis is None:
                scl.start_analysis(st)
            else:
                scl.finish_analysis(st, "stopped")
        self.run("analyse", go)

    def keep_analysis(self):
        """KEEP: the result stays, as it is already written."""
        with self.state.lock:
            self.state.replaced = None
        self.fill_sections()

    def report_clicked(self):
        """REPORT / HIDE clicked: kept for the next start, with the
        sections. A report shown by the program is not kept."""
        self.toggle_report()
        st = self.state
        self.scl.save_setting(st, "menu_sections", dict(
            st.cfg["menu_sections"], Report=bool(self.report.winfo_manager())))

    def toggle_report(self, show=None, open_section=True):
        """This place's report under the location readout, or not. `show`
        True opens it whatever it was (a run just finished)."""
        if show is None:
            show = not self.report.winfo_manager()
        if show:
            if open_section:
                self.location_sec.fold(True)
            self.report.pack(anchor="w", fill="x", pady=(6, 0),
                             before=self.report_btn)
            self.fill_location()
        else:
            self.report.pack_forget()
        self.report_btn.config(text="HIDE" if show else "REPORT")

    def fill_station_menu(self):
        """The station picker's choices, rebuilt each time it opens: the
        list arrives on another thread and depends on where you are."""
        st, scl, menu = self.state, self.scl, self.station_menu
        menu.delete(0, "end")
        with st.lock:
            names = scl.station_choices(st)
        if not names:
            menu.add_command(label="(no station list yet)", state="disabled")
            return
        menu.add_command(label=scl.NO_STATION, command=lambda: self.run(
            "station", lambda s: scl.link_station(s, None)))
        for name in names:
            menu.add_command(label=name, command=lambda n=name: self.run(
                "station", lambda s: scl.link_station(s, n)))

    def fill_analysis(self):
        """The Analysis section: how a run is going or what this place has,
        its label and station, KEEP / DISCARD after a run; opened when a
        run starts or a result waits."""
        st, scl = self.state, self.scl
        running = scl.analysis_progress(st)
        with st.lock:
            key, rec = scl.current_place(st)
            review = st.replaced is not None
            had, rec = rec is not None, dict(rec or {})
        if running:
            text = f"{running}\n{scl.ANALYSIS_ADVICE}"
        elif key is None:
            text = "no fix - nothing to analyse"
        else:
            text = f"{key}: {'analysed' if had else 'not analysed yet'}"
        self.analysis_text.config(text=text)
        self.analyse_btn.config(text="STOP" if running else "ANALYSE")
        self.label_var.set(rec.get("label") or scl.NO_LABEL)
        space = rec.get("scope") == "space"
        if space != bool(self.station_row.winfo_manager()):
            if space:
                self.station_row.pack(fill="x", pady=(6, 0),
                                      after=self.label_row)
            else:
                self.station_row.pack_forget()
        self.station_btn.config(
            text=(rec.get("station") or {}).get("name") or scl.NO_STATION)
        if review != bool(self.review_row.winfo_manager()):
            if review:
                self.review_row.pack(fill="x", pady=(6, 0))
            else:
                self.review_row.pack_forget()
        busy = bool(running) or review
        if busy and not self.analysing:
            self.analysis_sec.fold(True)
        if review and not self.reviewing:   # a result: its report with you
            self.toggle_report(True)
        self.analysing, self.reviewing = busy, review

    def poi_menu(self, e):
        """Right-click a waypoint in the list: select it and give its menu.
        A group's line has none."""
        row = self.pois.identify_row(e.y)
        if not row.startswith("wp:"):
            return
        self.pois.selection_set(row)
        self.menu.delete(0, "end")
        if self.nav.poi_list_menu(self.menu, row[4:], (e.x_root, e.y_root)):
            self.menu.tk_popup(e.x_root, e.y_root)

    def open_unplaced(self, _event):
        sel = self.unplaced.curselection()
        if sel:
            self.show(self.system, self.unplaced.get(sel[0]))

    def on_menu(self):
        """True while the pointer is on the left menu, not on the map."""
        return "menu" in self.canvas.gettags("current")

    def on_press(self, e):
        self.press, self.dragged = (None if self.on_menu()
                                    else (e.x, e.y)), False

    def on_drag(self, e):
        if self.press is None:
            return
        dx, dy = e.x - self.press[0], e.y - self.press[1]
        if abs(dx) + abs(dy) > 3:
            self.dragged = True
        # A body turns less the closer it is zoomed; the system view does not.
        step = 0.01 if self.body is None else starmap.turn_per_px(
            self.zoom, self.state.cfg["starmap_zoom_slowdown"])
        if self.follow.get() and self.held is None and self.om is not None:
            # Following: turned away from you, and still turning with you.
            self.turn = (self.turn[0] + dx * step,
                         max(-math.pi, min(math.pi, self.turn[1] + dy * step)))
        else:
            if self.dragged:
                self.om = None          # turned by hand: no longer held
            self.yaw += dx * step
            self.pitch = max(-math.pi / 2,
                             min(math.pi / 2, self.pitch + dy * step))
        self.press = (e.x, e.y)
        self.draw()

    def on_release(self, _e):
        if not self.dragged:
            current = self.canvas.find_withtag("current")
            wid = self.pins.get(current[0]) if current else None
            if wid is not None:
                self.nav.open_pin(wid)
            for tag in self.canvas.gettags("current"):
                if tag.startswith("body:"):
                    self.show(self.system, tag[5:])
                    break
        self.press = None

    def context_menu(self, e):
        """Right-click: the waypoint menu on a pin, the body menu on a planet
        or moon -- or, in a body's own view, anywhere else on it."""
        if self.on_menu():
            return
        c = self.canvas
        under = c.find_overlapping(e.x - 2, e.y - 2, e.x + 2, e.y + 2)
        pins = [self.pins[i] for i in under if i in self.pins]
        bodies = [tag[5:] for i in under for tag in c.gettags(i)
                  if tag.startswith("body:")]
        at = (e.x_root, e.y_root)
        self.menu.delete(0, "end")
        if pins:
            shown = self.nav.waypoint_menu(self.menu, pins[-1], "star", at)
        elif bodies:
            shown = self.nav.body_menu(self.menu, bodies[-1], "star", at,
                                       opened=bodies[-1] == self.body)
        elif self.body is not None:
            shown = self.nav.body_menu(self.menu, self.body, "star", at,
                                       opened=True)
        else:
            shown = False
        if shown:
            self.menu.tk_popup(e.x_root, e.y_root)

    def unplaced_menu(self, e):
        """Right-click in the list of bodies not yet placed: the body menu."""
        i = self.unplaced.nearest(e.y)
        if i < 0 or i >= self.unplaced.size():
            return
        self.menu.delete(0, "end")
        self.nav.body_menu(self.menu, self.unplaced.get(i), "star",
                           (e.x_root, e.y_root))
        self.menu.tk_popup(e.x_root, e.y_root)

    def on_wheel(self, e):
        if self.on_menu():
            return
        self.zoom = max(0.2, min(200.0, self.zoom * 1.15 ** (e.delta / 120)))
        self.draw()

    def refresh(self):
        """Redraw only when the data changed: a save from the overlay, a
        rename, a new analysis."""
        wps, bodies = snapshot(self.state)
        me = self.where_am_i()
        if self.follow.get():
            self.follow_me(me)
        self.fill_waypoints()
        self.fill_sections()
        with self.state.lock:
            cells = heightmap.cell_count(self.state.heightmap, self.body)
        # The SUB too: its icon is the pin (2026-11-08, a new SUB showed
        # the old icon until something else changed). Shown around too: it
        # moves a pin between the views.
        seen = (tuple((w["id"], w["name"], w["planet"], w.get("sub"),
                       w.get("parent")) for w in wps),
                tuple((k, tuple(v.get("centre") or ())) for k, v in bodies.items()),
                cells, me)
        self.ticks += 1
        # A turning globe is redrawn now and then, to keep up with its spin.
        if seen != self.seen or (self.body and self.ticks % 5 == 0
                                 and not self.press):
            self.seen = seen
            self.draw()

    # --- drawing ------------------------------------------------------------

    def draw(self):
        c = self.canvas
        # All but the menu, which is laid out on its own (PillMenu).
        c.addtag_all("old")
        c.dtag("menu", "old")
        c.delete("old")
        self.pins.clear()
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        # The window's picture, drawn here too: the empty canvas would
        # otherwise let clicks and drags through (Wallpaper).
        self.nav.wallpaper.under(c)
        wps, bodies = snapshot(self.state)
        wps = of_types(wps, self.types)
        self.subs = {wp["id"]: wp.get("sub", "") for wp in wps}
        self.names = {wp["id"]: wp["name"] for wp in wps}
        self.me = self.where_am_i()
        objs = starmap.system_view(bodies, wps, self.system)
        self.up = (starmap.back_to(self.body, bodies, self.system)
                   if self.body else None)
        self.back.config(text=f"< back to {self.up or 'system'}")
        if self.body is None:
            self.fill_box(" Bodies not analysed yet ", objs["unplaced"],
                          "These have waypoints but\nno place on the map "
                          "until\nyou ANALYSE them.\nClick one to open it.",
                          "None in this system.")
            self.draw_system(objs, wps, w, h)
        else:
            around = starmap.surroundings(self.body, bodies, wps, self.system)
            self.fill_box(" Moons not analysed yet ", around["unplaced_moons"],
                          f"Moons of {self.body} with no\nplace yet: ANALYSE "
                          "them\nto show them here.\nClick one to open it.",
                          "None for this body.")
            self.draw_body(wps, bodies, around, w, h)
        c.tag_raise("menuart")          # the menu over the map

    def fill_box(self, title, keys, hint, empty):
        """The side box: what is not on the view, and why."""
        self.box.set_title(title)
        self.unplaced.delete(0, "end")
        for key in keys:
            self.unplaced.insert("end", key)
        self.unplaced_hint.config(text=hint if keys else empty)

    def hand_cursor(self, tag):
        self.canvas.tag_bind(tag, "<Enter>",
                             lambda e: self.canvas.config(cursor="hand2"))
        self.canvas.tag_bind(tag, "<Leave>",
                             lambda e: self.canvas.config(cursor=""))

    def sphere(self, x, y, r, colours, tags=()):
        """A shaded ball: from the dark rim to a highlight toward the top left,
        in steps one colour shade apart, so no bands show at any zoom."""
        steps = max(3, min(140, int(r / 2)))
        for i in range(steps):
            k = 1 - i / steps                    # 1 at the rim, toward 0
            rr, off = r * k, r * (1 - k) * 0.4
            col = blend(colours, i / max(1, steps - 1))
            self.canvas.create_oval(x - off - rr, y - off - rr, x - off + rr,
                                    y - off + rr, fill=col, outline="",
                                    tags=tags)

    def mesh(self, radius):
        """(tiles, lo, hi) for the open body, rebuilt only when its survey or
        the relief changed."""
        relief = float(self.relief.get())
        with self.state.lock:
            rec = self.state.heightmap.get(self.body)
            k = (self.body, radius, relief,
                 len(rec["cells"]) if rec else 0)
            if k == self.mesh_key:
                return self.mesh_tiles
            heights = heightmap.coarse(rec, heightmap.GLOBE_CELLS)
        lo, hi = heightmap.height_range(heights.values())
        self.mesh_key = k
        self.mesh_tiles = (heightmap.globe_tiles(heights, radius,
                                                 exaggeration=relief), lo, hi)
        return self.mesh_tiles

    def draw_mesh(self, radius, yaw, pitch, scale, cx, cy):
        """The body as a shaded ball with its surveyed tiles on it, coloured
        by height and raised. Returns how many cells have been surveyed."""
        is_moon = any(self.body in m for m in starmap.MOONS.values())
        self.sphere(cx, cy, radius * scale, MOON if is_moon else PLANET)
        tiles, lo, hi = self.mesh(radius)
        near = []
        for corners, centre, h in tiles:
            n = starmap.rotate(centre, yaw, pitch)
            if n[1] > 0:                        # the far side
                continue
            flat = []
            for p in corners:
                sx, sy, _ = starmap.project(p, yaw, pitch, scale, cx, cy)
                flat += [sx, sy]
            near.append((n, flat, h))
        near.sort(key=lambda t: -t[0][1])       # farthest first
        for n, flat, h in near:
            # Outlined in its own colour: no seams between neighbours.
            col = heightmap.height_colour(h, lo, hi, heightmap.tile_light(n))
            self.canvas.create_polygon(*flat, fill=col, outline=col)
        return self.mesh_key[3]

    def pin(self, x, y, wid, label=None):
        """A location marker standing on (x, y) for waypoint `wid`, with its
        name (or `label`) beside it, ringed if SHOW ON MAP picked it. Clicking
        any part of it opens the waypoint (Navigator.open_pin)."""
        c = self.canvas
        ids = []
        sub = self.subs.get(wid, "")
        if wid == self.marked:
            ids.append(c.create_oval(x - 15, y - 27, x + 15, y + 3,
                                     outline=ACCENT, width=2, tags="pin"))
        ids.append(c.create_polygon(x, y, x - 5, y - 10, x + 5, y - 10,
                                    fill=PIN, outline="", tags="pin"))
        if subtypes.icon_of(sub):       # its icon in place of the pin's head
            ids += draw_icon(c, sub, x, y - 17, px(9), tags="pin")
        else:
            ids += [
                c.create_oval(x - 6, y - 18, x + 6, y - 6, fill=PIN,
                              outline="", tags="pin"),
                c.create_oval(x - 2, y - 14, x + 2, y - 10, fill=BG,
                              outline="", tags="pin")]
        ids.append(c.create_text(x + (px(9) + 3 if subtypes.icon_of(sub)
                                      else 9), y - 12,
                                 text=label or self.names.get(wid, ""),
                                 anchor="w",
                                 fill=label_colour(self.state.cfg,
                                                   "starmap_label_colour"),
                                 font=mono(9), tags=("pin", "label")))
        for item in ids:
            self.pins[item] = wid

    def you(self, x, y, label=False):
        """Where you are: the height map's cross, named when `label` is set."""
        c = self.canvas
        c.create_line(x - 8, y, x + 8, y, fill=ACCENT, width=2, tags="you")
        c.create_line(x, y - 8, x, y + 8, fill=ACCENT, width=2, tags="you")
        if label:
            c.create_text(x + 11, y, text="YOU", anchor="w", fill=ACCENT,
                          font=mono(9, True), tags="you")

    def header(self, title, hint="", centre=False):
        """The title at the top left, its hint lines below it; `centre`
        puts the title in the middle of the window's width instead (not of
        the map, which the side panels push right)."""
        c = self.canvas
        top = c.winfo_toplevel()
        x, anchor = (((top.winfo_rootx() + top.winfo_width() / 2
                       - c.winfo_rootx()), "n") if centre else (12, "nw"))
        ids = [c.create_text(x, 10, text=title, anchor=anchor, fill=ACCENT,
                             font=mono(16, True))]
        if hint:
            ids.append(c.create_text(12, 10 + px(26), text=hint, anchor="nw",
                                     fill=DIM, font=mono(9)))
        x0, _, x1, y1 = c.bbox(*ids)
        self.backdrop("header", (0 if x0 < 12 else x0 - 8, 0, x1 + 8, y1 + 6),
                      ids[0])

    def backdrop(self, key, box, below):
        """A see-through box (x0, y0, x1, y1) under canvas item `below` and
        what follows it, so text stays readable over a globe that fills the
        view. The canvas has no transparency of its own."""
        x0, y0, x1, y1 = (int(v) for v in box)
        rgb = tuple(int(BG[i:i + 2], 16) for i in (1, 3, 5))
        self.backdrops[key] = ImageTk.PhotoImage(  # Tk drops an image nothing holds
            Image.new("RGBA", (x1 - x0, y1 - y0), rgb + (190,)))
        self.canvas.tag_lower(self.canvas.create_image(
            x0, y0, image=self.backdrops[key], anchor="nw"), below)

    def legend(self, lo, hi, w, h):
        """What the surveyed tiles' colours mean, in the bottom right corner."""
        c = self.canvas
        if self.ramp is None:               # the same for every lo..hi
            self.ramp = tk.PhotoImage(width=160, height=14)
            for i in range(160):
                self.ramp.put(heightmap.height_colour(i, 0, 159),
                              to=(i, 0, i + 1, 14))
        x, y = w - 172, h - 34
        ids = (c.create_text(x, y - 4, anchor="sw", fill=FG,
                             font=mono(9), text="ground height"),
               c.create_image(x, y, image=self.ramp, anchor="nw"),
               c.create_text(x, y + 16, anchor="nw", fill=DIM,
                             font=mono(9), text=self.scl.fmt_dist(lo)),
               c.create_text(x + 160, y + 16, anchor="ne", fill=DIM,
                             font=mono(9), text=self.scl.fmt_dist(hi)))
        x0, y0, _, _ = c.bbox(*ids)
        self.backdrop("legend", (x0 - 8, y0 - 6, w, h), ids[0])

    def system_fit(self, objs, wps, w, h):
        """Zoom 1 of the system map: as close as the default view can get
        with every waypoint in it, and every body that has some, names and
        all. Worked out for the default view however it is turned now, so
        turning it does not change the scale."""
        name_h, pad = mono(9).metrics("linespace"), px(6)
        # How far each drawing reaches past its spot: see pin and sphere.
        spots = [(p, (15 + pad, max(15, 9 + mono(9).measure(n)) + pad,
                      27 + pad, 3 + pad))
                 for n, p in ((self.names.get(i, ""), p)
                              for i, p in objs["points"])]
        on = {wp["planet"] for wp in wps}
        for b in objs["bodies"]:
            if b["key"] in on:
                moon = b["label"] == "moon"
                r = MOON_PX if moon else PLANET_PX
                half = max(r, mono(9, True).measure(b["key"].upper()) / 2)
                named = r + 4 + name_h      # the name above a moon, else below
                spots.append((b["centre"], (half + pad, half + pad,
                                            (named if moon else r) + pad,
                                            (r if moon else named) + pad)))
        top = 10 + px(26) + name_h + pad    # clear of the header
        return (starmap.fit_view(spots, 0.0, SYSTEM_PITCH, w, h, top)
                or starmap.fit_scale([b["centre"] for b in objs["bodies"]]
                                     + [p for _, p in objs["points"]], w, h))

    def orbit_line(self, flat):
        """An orbit ring through the screen points `flat`, as thick as
        Settings > Star map says."""
        self.canvas.create_line(*flat, fill=ORBIT, dash=(3, 4),
                                width=self.state.cfg["starmap_ring_width"])

    def draw_system(self, objs, wps, w, h):
        c, yaw, pitch = self.canvas, self.yaw, self.pitch
        cx, cy = w / 2, h / 2
        scale = self.system_fit(objs, wps, w, h) * self.zoom

        for b in objs["bodies"]:
            if b["label"] == "moon":
                continue
            ring = starmap.orbit_ring(math.hypot(*b["centre"][:2]))
            flat = []
            for p in ring:
                sx, sy, _ = starmap.project(p, yaw, pitch, scale, cx, cy)
                flat += [sx, sy]
            self.orbit_line(flat)

        items = [(starmap.project((0, 0, 0), yaw, pitch, scale, cx, cy),
                  "sun", None)]
        items += [(starmap.project(b["centre"], yaw, pitch, scale, cx, cy),
                   "body", b) for b in objs["bodies"]]
        items += [(starmap.project(p, yaw, pitch, scale, cx, cy), "point", n)
                  for n, p in objs["points"]]
        system, _, _, sys_pos = self.me
        if system == self.system and sys_pos is not None:
            items.append((starmap.project(sys_pos, yaw, pitch, scale, cx, cy),
                          "you", None))
        items.sort(key=lambda it: -it[0][2])         # farthest first
        for (sx, sy, _), kind, what in items:
            if kind == "sun":
                self.sphere(sx, sy, SUN_PX, SUN)
            elif kind == "body":
                moon = what["label"] == "moon"
                r = MOON_PX if moon else PLANET_PX
                tags = ("body:" + what["key"],)
                self.sphere(sx, sy, r, MOON if moon else PLANET, tags)
                # Moons sit close to their planet: label them above, the
                # planet below, so the two names do not print over each other.
                c.create_text(sx, sy - r - 4 if moon else sy + r + 4,
                              text=what["key"].upper(),
                              anchor="s" if moon else "n", fill=PLANET[2],
                              tags=tags, font=mono(9, True))
            elif kind == "you":
                self.you(sx, sy, label=True)
            else:
                self.pin(sx, sy, what)
        for it in items:
            if it[1] == "body":
                self.hand_cursor("body:" + it[2]["key"])
        # At this scale you sit on the pixels of the body you are near, so
        # the cross goes on top of everything or that body's dot hides it.
        c.tag_raise("you")
        self.header(self.system.upper(), centre=True)
        # Shown by the ? pill at the bottom right. Bodies are drawn at fixed
        # sizes (SUN_PX, PLANET_PX, MOON_PX).
        self.help = ("drag to rotate  -  wheel to zoom  -  click a planet or "
                     "moon to open it, a pin to edit it  -  bodies not to "
                     "scale")

    def draw_body(self, wps, bodies, around, w, h):
        # The body's own frame turns with it; its moons and stations do not.
        # Turning the globe to where it is now keeps the two in step.
        spin = starmap.spin_angle(bodies.get(self.body), time.time())
        if self.om is not None:
            self.yaw, self.pitch = starmap.view_from(*self.om, spin)
            self.yaw += self.turn[0]
            self.pitch = max(-math.pi / 2,
                             min(math.pi / 2, self.pitch + self.turn[1]))
        c, yaw, pitch = self.canvas, self.yaw, self.pitch
        c.config(cursor="")
        cx, cy = w / 2, h / 2
        pts = starmap.body_points(wps, self.body)
        radius = starmap.body_radius(self.body, bodies, wps, self.scl.BODY_RADII)
        if radius is None:
            radius = max((math.sqrt(sum(v * v for v in p)) for _, p in pts),
                         default=1.0)
        own = yaw + spin
        # Inside `inner` everything is true scale: the globe, its waypoints,
        # its low stations. Beyond it, distances are pulled in.
        inner = max([radius * 1.6] + [math.sqrt(sum(v * v for v in p)) * 1.1
                                      for _, p in pts])
        outer = [(kind, key, d, starmap.compress(d, inner))
                 for kind, group in (("moon", around["moons"]),
                                     ("point", around["points"]))
                 for key, d in group]
        scale = starmap.fit_scale([(radius, 0, 0)] + [p for _, p in pts] +
                                  [q for *_, q in outer], w, h,
                                  fill=0.40) * self.zoom
        r_px = radius * scale

        for kind, _, _, q in outer:
            if kind == "moon":
                flat = []
                for p in starmap.orbit_ring(math.hypot(q[0], q[1])):
                    sx, sy, _ = starmap.project(p, yaw, pitch, scale, cx, cy)
                    flat += [sx, sy]
                self.orbit_line(flat)

        items = [(starmap.project(p, own, pitch, scale, cx, cy), "pin", n,
                  None) for n, p in pts]
        _, body, pos, _ = self.me
        if body == self.body:
            items.append((starmap.project(pos, own, pitch, scale, cx, cy),
                          "you", None, None))
        for kind, key, d, q in outer:
            far = self.scl.fmt_dist(math.sqrt(sum(v * v for v in d)))
            name = key.upper() if kind == "moon" else self.names.get(key, "")
            items.append((starmap.project(q, yaw, pitch, scale, cx, cy),
                          "moon" if kind == "moon" else "pin", key,
                          f"{name}  {far}"))
        poles = [(starmap.project((0, 0, s * radius * 1.1), yaw, pitch, scale,
                                  cx, cy), s) for s in (1, -1)]
        # Behind the body and inside its outline: hidden. Drawing them under
        # the sphere is not enough, as the pin stands up past its edge.
        behind = sorted([it for it in items if it[0][2] > 0 and not
                         starmap.hidden_by_sphere(*it[0], cx, cy, r_px)],
                        key=lambda it: -it[0][2])
        front = sorted([it for it in items if it[0][2] <= 0],
                       key=lambda it: -it[0][2])

        def draw_pole(spot, sign):
            sx, sy, _ = spot
            ex, ey, _ = starmap.project((0, 0, sign * radius), yaw, pitch,
                                        scale, cx, cy)
            c.create_line(ex, ey, sx, sy, fill=GRID, width=2)
            c.create_text(sx, sy, text="N" if sign > 0 else "S", fill=DIM,
                          font=mono(9, True))

        def draw_item(item):
            (sx, sy, _), kind, key, label = item
            if kind == "pin":
                self.pin(sx, sy, key, label)
                return
            if kind == "you":
                self.you(sx, sy)
                return
            tags = ("body:" + key,)
            self.sphere(sx, sy, MOON_PX + 2, MOON, tags)
            c.create_text(sx, sy - MOON_PX - 6, text=label, anchor="s",
                          fill=PLANET[2], tags=tags + ("label",),
                          font=mono(9, True))
            self.hand_cursor(tags[0])

        for spot, sign in poles:
            if spot[2] > 0:
                draw_pole(spot, sign)
        for item in behind:
            draw_item(item)
        surveyed = self.draw_mesh(radius, own, pitch, scale, cx, cy)
        for line in starmap.graticule(radius):
            for run in starmap.front_segments(line, own, pitch, scale, cx, cy):
                c.create_line(*run, fill=GRID)
        for spot, sign in poles:
            if spot[2] <= 0:
                draw_pole(spot, sign)
        for item in front:
            draw_item(item)
        # Something just beside the globe but behind it is drawn first, so the
        # globe would cover its name; the names go on top of everything.
        c.tag_raise("label")
        rec = bodies.get(self.body) or {}
        where = rec.get("system") or self.system
        facts = [f"radius {radius / 1000:,.0f} km",
                 f"{len(pts)} waypoint{'' if len(pts) == 1 else 's'}"]
        if surveyed:
            facts.append(f"{surveyed:,} cells surveyed")
        if spin:
            facts.append("turned as it is now")
        # Short lines: the marker buttons sit at the top right of the canvas.
        lines = ["  -  ".join(facts)]
        if outer:
            lines.append("beyond the globe: distances not to scale")
        self.header(f"{self.body.upper()}  ({where})", "\n".join(lines))
        self.help = ("drag to rotate  -  wheel to zoom  -  click a pin on "
                     "the ground for the height map, another to edit it, a "
                     "moon to open it")
        if surveyed:
            self.legend(*self.mesh_tiles[1:], w, h)


# ----------------------------------------------------------------------------
# Height map
# ----------------------------------------------------------------------------


# What the height map can draw (in the order its layers are set in Settings,
# traces.LAYERS), each as (switch, label, on as shipped).
HEIGHT_LAYERS = (("relief", "Filled relief", True),
                 ("samples", "Survey samples", False),
                 ("lines", "Height lines", True),
                 ("course", "Survey pattern", False),
                 ("lake", "Lakes, seas, rivers", True),
                 ("rail", "Railways", True),
                 ("drawn", "Roads, borders, lines", True),
                 ("zone", "Hostile areas, swamps, zones", True),
                 ("building", "Buildings", True))
HEIGHT_SWITCHES = HEIGHT_LAYERS + (
    ("poi", "POI waypoints", True),
    ("survey", "Survey waypoints", False),
    ("poi_height", "POI heights", False),
    ("names", "Waypoint names", True),
    ("frames", "Map frames", False))


def label_colour(cfg, key):
    """The colour waypoint names are drawn in, as config `key` names it
    (LABEL_COLOURS); white for a name not there."""
    return LABEL_COLOURS.get(cfg.get(key), FG)


def layer_choices(levels, opacities):
    """{kind: {"level": n, "opacity": %}} to keep as heightmap_layers, from
    Settings' {kind: level} and {kind: opacity}: only what is not as shipped
    (traces.LAYERS), so a kind left alone follows the shipped one."""
    chosen = {}
    for kind, (level, opacity) in traces.LAYERS.items():
        got = {}
        if levels.get(kind, level) != level:
            got["level"] = levels[kind]
        if opacity is not None and opacities.get(kind, opacity) != opacity:
            got["opacity"] = opacities[kind]
        if got:
            chosen[kind] = got
    return chosen


class LayerTable:
    """Each survey kind's level and opacity on the height map, as a grid in
    `parent` with Reset to shipped under it: the height map's own (Settings)
    or a map's (its Layers... window). `chosen` is what is set now, as
    heightmap_layers; `save` is called with layer_choices on every change.

    A map's also has a tick a kind to show it or not: `hidden` is the kinds
    not shown now, and `save_hidden` is called with them, sorted, on every
    change. Without `save_hidden` there are no ticks."""

    def __init__(self, parent, chosen, save, hidden=(), save_hidden=None,
                 blocks=2):
        """`blocks`: how many lists of kinds side by side."""
        self.save, self.save_hidden = save, save_hidden
        grid = tk.Frame(parent, bg=BG)
        grid.pack(anchor="w")
        lay = traces.layers(chosen)
        self.levels, self.opacities, self.shown = {}, {}, {}
        half = -(-len(lay) // blocks)
        heads = ("survey", "level", "opacity %")
        if save_hidden is not None:
            heads = ("show",) + heads
        step = len(heads) + 1
        for col in range(0, blocks * step, step):
            for k, text in enumerate(heads):
                tk.Label(grid, text=text, font=mono(9), fg=DIM, bg=BG).grid(
                    row=0, column=col + k, sticky="w", padx=(0, 8))
        for k, (kind, (level, opacity)) in enumerate(lay.items()):
            row, col = k % half + 1, k // half * step
            if save_hidden is not None:
                self.shown[kind] = tk.BooleanVar(value=kind not in hidden)
                ttk.Checkbutton(grid, variable=self.shown[kind],
                                command=self.ticked).grid(
                    row=row, column=col, sticky="w", padx=(0, 4))
                col += 1
            text = ("height map" if kind == "heightmap"
                    else traces.KINDS[kind]["menu"])
            tk.Label(grid, text=text, font=mono(), fg=FG, bg=BG).grid(
                row=row, column=col, sticky="w", padx=(0, 8))
            if kind == "heightmap":
                tk.Label(grid, text=str(level), font=mono(), fg=DIM,
                         bg=BG).grid(row=row, column=col + 1, sticky="w")
            else:
                self.levels[kind] = tk.StringVar(value=str(level))
                pick = ttk.Combobox(grid, textvariable=self.levels[kind],
                                    state="readonly", width=3,
                                    values=[str(v) for v in traces.LEVELS])
                pick.grid(row=row, column=col + 1, sticky="w", padx=(0, 8))
                pick.bind("<<ComboboxSelected>>", lambda e: self.changed())
            if opacity is not None:
                self.opacities[kind] = tk.StringVar(value=str(opacity))
                pick = ttk.Combobox(grid, textvariable=self.opacities[kind],
                                    state="readonly", width=4,
                                    values=[str(v) for v in range(0, 101, 5)])
                pick.grid(row=row, column=col + 2, sticky="w",
                          padx=(0, 32))
                pick.bind("<<ComboboxSelected>>", lambda e: self.changed())
        ttk.Button(parent, text="Reset to shipped",
                   command=self.reset).pack(anchor="w", pady=(6, 0))

    def changed(self):
        self.save(layer_choices(
            {k: int(v.get()) for k, v in self.levels.items()},
            {k: int(v.get()) for k, v in self.opacities.items()}))

    def ticked(self):
        self.save_hidden(sorted(k for k, v in self.shown.items()
                                if not v.get()))

    def reset(self):
        """Every level and opacity back to as shipped, every kind shown."""
        for kind, (level, opacity) in traces.LAYERS.items():
            if kind in self.levels:
                self.levels[kind].set(str(level))
            if kind in self.opacities:
                self.opacities[kind].set(str(opacity))
        self.save({})
        if self.save_hidden is not None:
            for var in self.shown.values():
                var.set(True)
            self.save_hidden([])


def height_switch_starts(cfg):
    """{switch: on} the height map starts with: as set in Settings, else as
    shipped."""
    chosen = cfg.get("heightmap_switches") or {}
    return {name: bool(chosen.get(name, on))
            for name, _, on in HEIGHT_SWITCHES}


class HeightMapTab:
    """The surveyed ground around you, flat and at full detail: one 100 m
    cell to a pixel at the start, grey where nothing has been measured."""

    # m a pixel; the closest ones are for buildings, tens of metres across
    ZOOMS = (0.390625, 0.78125, 1.5625, 3.125, 6.25, 12.5, 25.0, 50.0, 100.0,
             200.0, 400.0, 800.0)
    RELIEF_BLOCK = 4        # px a side of the filled view's blocks
    SETTLE_MS = 250         # quiet this long after a drag or zoom: full draw
    FRAME_PAD = 30          # px clear round a map's frame as it is set
    FRAMING_STEP = 2 ** 0.25    # a wheel click's zoom while a frame is set
    DIM_ALPHA = 110         # how dark the ground outside a map's frame is

    def __init__(self, parent, state, scl, follow, nav):
        self.state, self.scl, self.follow, self.nav = state, scl, follow, nav
        self.frame = tk.Frame(parent, bg=BG)
        self.body, self.centre = None, None
        self.offset = (0.0, 0.0)    # (east m, north m) dragged from you
        self.mpp = 100.0
        self.press, self.seen, self.image = None, None, None
        self.pictures = []      # water, washes, buildings: held as self.image
        self.held = None    # as the star map's: set by Go to
        self.contours = None    # (key, frame, (half_w, half_h), lines): draw
        self.relief = None      # (key, frame, (half_w, half_h), relief_grid)
        self.job, self.settle = None, None      # see schedule
        # Buildings drawn, as (outline px, (trace name, label)), and those
        # whose name shows by the pointer, as (outline px, label); see
        # draw_buildings. Found by the pointer being inside the outline, as
        # their fill is a picture, not a canvas item.
        self.building_px, self.building_names = [], []
        self.hovered = None         # the name shown by the pointer
        self.pin_items = {}         # a waypoint's drawn items -> its id
        self.goto_ids = []          # the waypoint ids of Go to's lines

        side = tk.Frame(self.frame, bg=BG)
        side.pack(side="left", fill="y", padx=(0, 8))
        self.panels = [side]    # hidden by the window's menu / collapse

        def section(title):
            """A folding section of the menu, as the star map's."""
            sec = kept_section(state, scl, side, title,
                               key=f"Height map: {title}")
            sec.pack(pady=(8, 0) if side.pack_slaves() else 0)
            return sec

        body = section("Body").body
        self.body_var = tk.StringVar()
        self.pick = ttk.Combobox(body, textvariable=self.body_var, width=14,
                                 state="readonly")
        self.pick.pack(anchor="w", pady=(0, 8))
        self.pick.bind("<<ComboboxSelected>>",
                       lambda e: self.open(self.body_var.get()))
        # Packed by place_star_back, while the body open is on a star map.
        self.star_back = ttk.Button(
            body, text="< back to star map",
            command=lambda: nav.back_to_star(self.body))
        tk.Label(body, text="Go to", font=mono(), fg=DIM, bg=BG).pack(anchor="w")
        self.goto_var = tk.StringVar()
        self.goto = ttk.Combobox(body, textvariable=self.goto_var, width=14,
                                 state="readonly")
        self.goto.pack(anchor="w")
        self.goto.bind("<<ComboboxSelected>>", lambda e: self.go_to())

        # What the map shows: its cells and contours, its colours.
        body = section("Info").body
        self.info = tk.Label(body, font=mono(), fg=FG, bg=BG, justify="left",
                             anchor="w")
        self.info.pack(anchor="w", fill="x")
        self.legend = tk.Canvas(body, width=170, height=34, bg=BG,
                                highlightthickness=0)
        self.legend.pack(anchor="w", pady=(8, 0))
        self.legend_key = None      # what the legend's bar was drawn for

        # The switches: layers, waypoint types, names.
        body = section("Show").body
        on = height_switch_starts(state.cfg)
        self.show = {}
        for name, text, _ in HEIGHT_LAYERS:
            self.show[name] = tk.BooleanVar(value=on[name])
            ttk.Checkbutton(body, text=text, variable=self.show[name],
                            command=self.draw).pack(anchor="w")
        self.types = type_switches(body, [t for t in TYPE_LABELS if on[t]],
                                   self.draw)
        self.show["poi_height"] = tk.BooleanVar(value=on["poi_height"])
        ttk.Checkbutton(body, text="POI heights",
                        variable=self.show["poi_height"],
                        command=self.draw).pack(anchor="w")
        self.show["names"] = tk.BooleanVar(value=on["names"])
        ttk.Checkbutton(body, text="Waypoint names",
                        variable=self.show["names"],
                        command=self.draw).pack(anchor="w")
        self.show["frames"] = tk.BooleanVar(value=on["frames"])
        ttk.Checkbutton(body, text="Map frames",
                        variable=self.show["frames"],
                        command=self.draw).pack(anchor="w")

        # Maps kept to print (maps.py): a new one from the view, framed
        # here; the rest of them is in the Maps tab.
        self.map_sec = section("Map")
        body = self.map_sec.body
        self.new_button = ttk.Button(body, text="New map...",
                                     command=self.new_map)
        self.new_button.pack(anchor="w")
        # Shown while a map's frame is being set (see frame_map).
        self.framing_row = tk.Frame(body, bg=BG)
        self.framing_text = tk.Label(self.framing_row, font=mono(9),
                                     fg=ACCENT, bg=BG, justify="left")
        self.framing_text.pack(anchor="w")
        ttk.Button(self.framing_row, text="Save", width=9,
                   command=self.save_frame).pack(side="left")
        ttk.Button(self.framing_row, text="Cancel", width=9,
                   command=self.cancel_frame).pack(side="left")
        # While a frame is set: {"map", "was": (centre, mpp), "back": what
        # to do once saved or cancelled, or None}. See frame_map.
        self.framing = None

        # How to read the map.
        tk.Label(section("Key").body, font=mono(9), fg=DIM, bg=BG,
                 justify="left",
                 anchor="w",
                 text="grey: not measured\nbrown: contour lines\n"
                      "pin number: ground height\n"
                      "dashes: survey course\n"
                      "blue: water, its level\n"
                      "+ : you are here\n"
                      "drag: move, wheel: zoom").pack(anchor="w", fill="x")

        self.canvas = tk.Canvas(self.frame, bg=heightmap.GREY,
                                highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Map>", lambda e: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Motion>", self.on_motion)
        # Right-click on a waypoint, a building or the ground: the menus
        # every tab shares.
        self.canvas.bind("<Button-3>", self.context_menu)
        self.menu = PopMenu(self.canvas, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground=PICK)

    def place_star_back(self):
        """Back to star map: there only while the body open is on one."""
        on = self.nav.body_system(self.body) in starmap.SYSTEMS
        if on and not self.star_back.winfo_manager():
            self.star_back.pack(anchor="w", fill="x", pady=(0, 8),
                                after=self.pick)
        elif not on and self.star_back.winfo_manager():
            self.star_back.pack_forget()

    def open(self, body):
        """Show `body`, around the busiest area surveyed on it."""
        self.end_framing()
        self.body, self.offset = body, (0.0, 0.0)
        self.goto_var.set("")
        self.goto_choices()
        with self.state.lock:
            self.centre = heightmap.centre_of(self.state.heightmap.get(body))
            # Nothing surveyed: the end of the last line traced there.
            ends = [t["points"][-1] for t in self.state.traces
                    if t["planet"] == body and t["points"]]
        if self.centre is None and ends:
            self.centre = heightmap.local_frame(ends[-1])[0]
        self.draw()

    def show_trace(self, name):
        """Show on height map, from the Surveys tab: open the trace's body,
        its layer switched on, and centre on it, held as Go to holds."""
        with self.state.lock:
            t = next((t for t in self.state.traces if t["name"] == name),
                     None)
            pts = [tuple(p) for p in t["points"]] if t else []
        if not pts or t["planet"] is None:
            return
        if t["planet"] != self.body:
            self.body_var.set(t["planet"])
            self.open(t["planet"])
        self.show[traces.KINDS[t["kind"]]["switch"]].set(True)
        mid = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
        with self.state.lock:
            self.held = (self.state.system, self.state.planet)
        self.offset = (0.0, 0.0)
        self.centre = heightmap.local_frame(mid)[0]
        self.draw()

    def set_switches(self, on):
        """Set switches by name ({name: on}, as HEIGHT_SWITCHES names them)
        from Settings, and redraw as a click on them would."""
        self.set_vars(on)
        self.draw()

    def set_vars(self, on):
        """Tick or clear the switches named in `on` ({name: on})."""
        for name, value in on.items():
            (self.types if name in self.types else self.show)[name].set(value)

    def switches(self):
        """{name: on} for every switch, as they are ticked now."""
        return {name: var.get() for name, var in
                list(self.show.items()) + list(self.types.items())}

    # --- maps (maps.py) -------------------------------------------------

    def new_map(self):
        """A new map of the view as it is, shown as the height map's
        switches show it, named and then framed."""
        if self.body is None or self.centre is None or self.framing:
            return
        with self.state.lock:
            ms = list(self.state.maps)
            layers = self.state.cfg.get("heightmap_layers")
        name = ask_name(self.frame, "New map", "Name the map",
                        maps.map_name(ms),
                        [m["name"] for m in maps.of_body(ms, self.body)],
                        can_clear=False)
        if not name:
            return
        c = self.canvas
        across, _ = maps.fit(c.winfo_width(), c.winfo_height(),
                             self.FRAME_PAD)
        switches, hidden = maps.from_view(self.switches())
        m = maps.new_map(name, self.body, self.centre, across * self.mpp,
                         switches, layers, hidden=hidden)
        self.scl.add_map(self.state, m)
        self.frame_map(m)

    def frame_map(self, m, back=None):
        """Set where map `m`'s frame is: it stays in the middle of the
        window while the ground is dragged and zoomed under it, in finer
        steps than the height map's own (FRAMING_STEP), until Save or
        Cancel; then `back` is run, if given."""
        if m["body"] != self.body:
            self.body_var.set(m["body"])
            self.open(m["body"])
        self.end_framing()
        self.framing = {"map": m, "was": (self.centre, self.mpp),
                        "back": back}
        with self.state.lock:
            self.held = (self.state.system, self.state.planet)
        self.offset = (0.0, 0.0)
        self.centre = list(m["centre"])
        c = self.canvas
        across, _ = maps.fit(c.winfo_width(), c.winfo_height(),
                             self.FRAME_PAD)
        if c.winfo_width() >= 50:
            self.mpp = m["width_m"] / across
        self.frame_controls()
        self.draw()

    def save_frame(self):
        if not self.framing:
            return
        c = self.canvas
        across, _ = maps.fit(c.winfo_width(), c.winfo_height(),
                             self.FRAME_PAD)
        self.scl.update_map(self.state, self.framing["map"]["id"], {
            "centre": list(heightmap.local_frame(self.centre)[0]),
            "width_m": across * self.mpp})
        self.end_framing(back=True)

    def cancel_frame(self):
        """The view back where it was before the frame was set."""
        if not self.framing:
            return
        self.centre, self.mpp = self.framing["was"]
        self.end_framing(back=True)

    def end_framing(self, back=False):
        """No frame being set any more; its `back` run if `back`."""
        was, self.framing = self.framing, None
        self.frame_controls()
        if was is None:
            return
        self.draw()
        if back and was["back"]:
            was["back"]()

    def frame_controls(self):
        """New map... unless a frame is being set; Save and Cancel while
        one is."""
        if self.framing:
            self.map_sec.fold(True)     # Save and Cancel in sight
            self.new_button.state(["disabled"])
            self.framing_text.config(
                text=f"Framing {self.framing['map']['name']}:\ndrag and "
                     "zoom the ground\nunder the frame, then Save.")
            self.framing_row.pack(anchor="w", pady=(4, 0),
                                  after=self.new_button)
        else:
            self.new_button.state(["!disabled"])
            self.framing_row.pack_forget()

    def goto_choices(self):
        """The open body's waypoints, for Go to. The list shows names, which
        repeat (many places are a Cave), so goto_ids says which waypoint each
        line is, by its place in the list. A to Z, ignoring case."""
        wps = of_types(snapshot(self.state)[0], self.types)
        names = {w["id"]: w["name"] for w in wps}
        self.goto_ids = sorted((i for i, _ in starmap.body_points(wps,
                                                                 self.body)),
                               key=lambda i: names[i].lower())
        values = [names[i] for i in self.goto_ids]
        if list(self.goto.cget("values")) != values:
            self.goto.config(values=values)

    def show_waypoint(self, wid):
        """SHOW ON HEIGHT MAP in the Waypoints tab: open the waypoint's body
        and centre on it, as Go to does."""
        wps, _ = snapshot(self.state)
        w = self.scl.find_waypoint(wps, wid)
        if w is None or w.get("frame", "planet") != "planet":
            name = (w or {}).get("name", "that waypoint")
            with self.state.lock:
                self.state.flash(f"{name}: not on a body, not on the height map")
            return
        if self.scl.in_orbit(w, self.state.cfg):
            with self.state.lock:
                self.state.flash(f"{w['name']}: in orbit, not on the height map")
            return
        self.types.get(w.get("type"), self.types["poi"]).set(True)
        if w["planet"] != self.body:
            self.body_var.set(w["planet"])
            self.open(w["planet"])
        self.offset = (0.0, 0.0)
        self.goto_choices()
        if wid in self.goto_ids:
            self.goto.current(self.goto_ids.index(wid))
        self.go_to()

    def go_to(self):
        """Centre on the waypoint picked in Go to. Following you would pull
        the map straight back, so it waits until you move on."""
        i = self.goto.current()
        if not 0 <= i < len(self.goto_ids):
            return
        wps, _ = snapshot(self.state)
        p = dict(starmap.body_points(wps, self.body)).get(self.goto_ids[i])
        if p is None:
            return
        with self.state.lock:
            self.held = (self.state.system, self.state.planet)
        self.centre = heightmap.local_frame(p)[0]
        self.draw()

    def on_press(self, e):
        self.press = (e.x, e.y)

    def on_release(self, _e):
        self.press = None

    def context_menu(self, e):
        """Right-click: the waypoint menu on a pin (pins are drawn over
        buildings, so one wins), the building menu on a building, else the
        menu of the body the map shows."""
        under = self.canvas.find_overlapping(e.x - 2, e.y - 2, e.x + 2, e.y + 2)
        pins = [self.pin_items[i] for i in under if i in self.pin_items]
        builds = [b for px, b in self.building_px
                  if traces.inside_ring(e.x, e.y, px)]
        at = (e.x_root, e.y_root)
        self.menu.delete(0, "end")
        if pins:
            shown = self.nav.waypoint_menu(self.menu, pins[-1], "height", at)
        elif builds:
            shown = self.nav.building_menu(self.menu, *builds[-1], at)
        elif self.body is not None:
            shown = self.nav.body_menu(self.menu, self.body, "height", at)
        else:
            shown = False
        if shown:
            self.menu.tk_popup(e.x_root, e.y_root)

    def on_motion(self, e):
        """A building's name by the pointer while the pointer is over it,
        where the name did not fit inside its walls (see draw_buildings)."""
        over = next((label for px, label in reversed(self.building_names)
                     if traces.inside_ring(e.x, e.y, px)), None)
        if over != self.hovered:
            self.hovered = over
            self.canvas.delete("name")
            if over:
                self.show_name(e, over)

    def on_drag(self, e):
        if self.press is None or self.centre is None:
            return
        dx, dy = e.x - self.press[0], e.y - self.press[1]
        self.press = (e.x, e.y)
        self.goto_var.set("")
        with self.state.lock:
            radius = self.radius()
            following = self.look()[2] is not None
        if following:       # still following: the map moves with you, offset
            self.offset = (self.offset[0] - dx * self.mpp,
                           self.offset[1] + dy * self.mpp)
        else:
            self.centre = heightmap.from_map(-dx * self.mpp, dy * self.mpp,
                                             heightmap.local_frame(self.centre),
                                             radius)
        self.schedule()

    def schedule(self):
        """Draw soon, for a drag or a zoom: events that arrive faster than a
        draw takes are folded into one, drawn rough from what is kept; the
        full draw follows once they stop."""
        if self.job is None:
            self.job = self.canvas.after_idle(self.rough)
        if self.settle is not None:
            self.canvas.after_cancel(self.settle)
        self.settle = self.canvas.after(self.SETTLE_MS, self.finish)

    def rough(self):
        self.job = None
        self.draw(quick=True)

    def finish(self):
        self.settle = None
        self.draw()

    def recentre(self):
        """follow me ticked or unticked: drop any drag or hold away from you."""
        self.offset, self.held = (0.0, 0.0), None
        self.refresh()
        self.draw()

    def on_wheel(self, e):
        """Zoom a step of ZOOMS in or out (the next one, from a map's own
        zoom), or a FRAMING_STEP while a map's frame is being set."""
        zin = e.delta > 0
        if self.framing:
            self.mpp = min(self.ZOOMS[-1], max(
                self.ZOOMS[0], self.mpp * self.FRAMING_STEP ** (-1 if zin
                                                                else 1)))
        else:
            finer = [z for z in self.ZOOMS if z < self.mpp * 0.999]
            coarser = [z for z in self.ZOOMS if z > self.mpp * 1.001]
            self.mpp = ((finer[-1] if finer else self.ZOOMS[0]) if zin
                        else (coarser[0] if coarser else self.ZOOMS[-1]))
        self.schedule()

    def radius(self):
        """The body's radius. Call with the lock held."""
        rec = self.state.heightmap.get(self.body)
        return rec["radius"] if rec else self.scl.BODY_RADII.get(self.body, 1.0)

    def look(self):
        """What the map depends on, to redraw only when it changes. Lock held.

        Where you are is in it twice: [2] only while the map follows you,
        which centres it; [5] whenever you are on this body, so your + moves
        while the map is held by Go to or not following. With only [2], the
        + froze on a held map until you left the body. The body's waypoints
        are in it too, so one saved where you stand shows at once.
        """
        st = self.state
        if self.held is not None and moved_on(self.held,
                                              (st.system, st.planet)):
            self.held = None
        here = st.planet == self.body and st.pos
        # A frame being set stays where it is.
        on_it = (self.follow.get() and self.held is None and here
                 and not self.framing)
        plan = st.survey_saved
        return (self.body, heightmap.cell_count(st.heightmap, self.body),
                tuple(st.pos) if on_it else None,
                plan and (plan["origin"], plan["leg"], plan["joined"]),
                tuple((len(t["points"]), t.get("label")) for t in st.traces
                      if t["planet"] == self.body),
                tuple(st.pos) if here else None,
                tuple((w["id"], w["name"], w.get("type"), w.get("sub"),
                       w.get("x"), w.get("y"))
                      for w in st.waypoints if w.get("planet") == self.body))

    def refresh(self):
        with self.state.lock:
            bodies = sorted(set(self.state.heightmap) | {
                t["planet"] for t in self.state.traces if t["planet"]})
            here = self.state.planet
            seen = self.look()
        if here in self.scl.BODY_RADII and here not in bodies:
            bodies.append(here)
        self.pick.config(values=bodies)
        self.goto_choices()
        if self.body is None and bodies:
            self.body_var.set(here if here in bodies else bodies[0])
            self.open(self.body_var.get())
        elif seen != self.seen:
            self.draw()

    def draw(self, quick=False):
        """Draw the map. `quick` while it is being dragged or zoomed: what is
        kept is used at whatever zoom it was made for, and the relief is
        drawn in blocks twice as big, so the map keeps up with the hand."""
        c = self.canvas
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50 or not c.winfo_ismapped() or self.body is None:
            return
        self.place_star_back()
        half_w, half_h = w / 2 * self.mpp, h / 2 * self.mpp
        with self.state.lock:
            st = self.state
            self.seen = self.look()
            here, pos = st.planet, st.pos
            radius = self.radius()
            if self.seen[2] is not None:
                self.centre = heightmap.from_map(*self.offset,
                                                 heightmap.local_frame(pos),
                                                 radius)
            frame = (heightmap.local_frame(self.centre)
                     if self.centre is not None else None)
            rec = st.heightmap.get(self.body)
            lay = traces.layers(st.cfg.get("heightmap_layers"))
            lakes = [(traces.parts(t), t.get("level"), t["kind"])
                     for t in st.traces
                     if t["planet"] == self.body and t["kind"] in traces.WATER
                     and len(t["points"])
                     >= traces.KINDS[t["kind"]]["least"]]
            # Contours and relief are worked out for twice the map each way
            # and kept until the survey, a lake or the zoom changes or you
            # pan off them. Only what is switched on is worked out.
            key = (self.body, self.seen[1], self.mpp,
                   sum(len(q) for p, _, _ in lakes for q in p))

            def stale(kept):
                if not frame:
                    return False
                # Rough: after a zoom, use what there is; the settled draw
                # makes it right. Panned off it at the same zoom is another
                # matter, or the ground you drag to would stay blank.
                if quick and kept and kept[0][0] == self.body and \
                        kept[0][2] != self.mpp:
                    return False
                return not (kept and kept[0] == key and heightmap.covers(
                    kept[1], kept[2], frame, radius, half_w, half_h))

            fresh = self.show["lines"].get() and stale(self.contours)
            fresh_relief = self.show["relief"].get() and stale(self.relief)
            # Cells just off that still shape the contours near its edge.
            around = (heightmap.local_cells(rec, radius, frame,
                                            2 * half_w + heightmap.REACH_M,
                                            2 * half_h + heightmap.REACH_M)
                      if fresh or fresh_relief else [])
            near = (heightmap.local_cells(rec, radius, frame, half_w, half_h)
                    if frame else [])
            body_lo, body_hi = heightmap.height_range(
                v[0] for v in (rec["cells"].values() if rec else ()))
            wps = [dict(x) for x in st.waypoints]
            # The last square, flown now or paused: where it would resume.
            plan = (dict(st.survey_saved) if st.survey_saved is not None
                    and st.survey_saved["planet"] == self.body
                    and st.survey_saved["origin"] is not None else None)
            rails = [list(t["points"]) for t in st.traces
                     if t["planet"] == self.body and t["kind"] == "railway"]
            # Lines and areas drawn by pattern: (points, kind, label, its
            # name in traces.KINDS).
            drawn, zones = [], []
            for t in st.traces:
                k = traces.KINDS[t["kind"]]
                # An area being flown has one point at first (2026-11-07:
                # a polygon of one point broke the draw).
                if (t["planet"] == self.body and "style" in k
                        and len(t["points"]) >= k["least"]):
                    (drawn if k["group"] == "line" else zones).append(
                        (list(t["points"]), k, t.get("label"), t["kind"]))
            rivers = [(run, t["width_m"]) for t in st.traces
                      if t["planet"] == self.body
                      and t["kind"] == "waterway" and len(t["points"]) >= 2
                      for run in traces.parts(t)]
            # One walked before offset_m was kept takes today's setting.
            buildings = [(list(t["points"]), t.get(
                "offset_m", st.cfg["building_wall_offset_m"]), t.get("label"),
                t["name"], t.get("shape", "squared"))
                for t in st.traces if t["planet"] == self.body
                and t["kind"] == "building"
                and len(t["points"]) >= traces.KINDS["building"]["least"]]
            # A QT-no target here: the overland leg from where you jump to.
            route = None
            tgt = self.scl.find_waypoint(st.waypoints, st.target)
            if (tgt is not None and tgt["planet"] == self.body
                    and tgt.get("frame", "planet") == "planet"):
                hop = self.scl.qt_hop(st.waypoints, tgt)
                if hop is not None:
                    # A point every 50 px, so a leg that only crosses the
                    # view still has points in it to draw.
                    n = max(16, min(2000, int(hop["dist"] / (50 * self.mpp))))
                    route = (hop["name"], self.scl.route_points(
                        hop["point"], (tgt["x"], tgt["y"], tgt["z"]), radius,
                        n))
        # Over a lake the HUD's ground is neither the bed nor the surface
        # (NOTES.md): the cells there shape no contour, shade or colour.
        # Each lake as its runs on the map (one, or more once merged).
        lake_xys = [(runs, level, kind) for runs, level, kind in (
            ([self.on_map(run, frame, radius) for run in runs], level, kind)
            for runs, level, kind in lakes) if None not in runs] \
            if frame else []
        around = self.outside(around, lake_xys)
        near = self.outside(near, lake_xys)
        interval = heightmap.contour_interval(body_lo, body_hi)
        if fresh:
            step = max(heightmap.CELL_M, 5 * self.mpp)  # grid points 5 px apart
            self.contours = (key, frame, (2 * half_w, 2 * half_h),
                             heightmap.contours_on_body(
                                 around, frame, radius, 2 * half_w,
                                 2 * half_h, step, interval))
        if fresh_relief:
            step = max(heightmap.RELIEF_STEP, 4 * self.mpp)
            self.relief = (key, frame, (2 * half_w, 2 * half_h),
                           heightmap.relief_grid(around, 2 * half_w,
                                                 2 * half_h, step))
        c.delete("all")
        self.pictures, self.building_px, self.building_names = [], [], []
        self.hovered = None
        faded = lay["heightmap"][1]     # the height map's opacity, %
        lo, hi = heightmap.height_range(z for _, _, z in near)
        img = tk.PhotoImage(width=w, height=h)
        img.put(heightmap.GREY, to=(0, 0, w, h))
        shift = (heightmap.frame_shift(self.relief[1], frame, radius)
                 if frame and self.relief and self.show["relief"].get()
                 else None)
        if shift:
            # Worked out one pixel to a block, then blown up by Tk: putting
            # every pixel took longer than working the colours out.
            block = self.RELIEF_BLOCK * (2 if quick else 1)
            small = ImageTk.PhotoImage(Image.fromarray(heightmap.fade(
                heightmap.relief_image(self.relief[3], shift, w, h, self.mpp,
                                       block, lo, hi), faded)))
            img.tk.call(img, "copy", small, "-zoom", block, block)
        if self.show["samples"].get():
            # Cells are 70-100 m: drawn 100 m wide they overlap, not gap.
            size = max(1, math.ceil(heightmap.CELL_M / self.mpp))
            for x, y, z in near:
                x0 = int(w / 2 + x / self.mpp - size / 2)
                y0 = int(h / 2 - y / self.mpp - size / 2)
                x1, y1 = min(w, x0 + size), min(h, y0 + size)
                x0, y0 = max(0, x0), max(0, y0)
                if x1 > x0 and y1 > y0:
                    img.put("#%02x%02x%02x" % tuple(heightmap.fade(
                        ImageColor.getrgb(heightmap.height_colour(z, lo, hi)),
                        faded)), to=(x0, y0, x1, y1))
        self.image = img                # Tk drops an image nothing holds
        c.create_image(0, 0, image=img, anchor="nw")
        if frame:
            self.draw_grid(frame, radius, w, h)
            if self.show["lines"].get():
                self.draw_contours(frame, radius, interval, w, h)
            # The surveys level by level (traces.LAYERS), then the survey
            # pattern and the QT leg over them all.
            for level in traces.LEVELS:
                here = {k for k, (at, _) in lay.items() if at == level}
                water = here & {"sea", "lake", "waterway"}
                if self.show["lake"].get():
                    # Water of one opacity in one picture, so a river
                    # running into a lake joins it.
                    for op in sorted({lay[k][1] for k in water}):
                        kinds = {k for k in water if lay[k][1] == op}
                        self.draw_lakes(
                            [x for x in lake_xys if x[2] in kinds],
                            rivers if "waterway" in kinds else [], op,
                            frame, radius, w, h)
                if self.show["zone"].get():
                    self.draw_zones([(p, k, label, lay[n][1])
                                     for p, k, label, n in zones if n in here],
                                    frame, radius, w, h)
                if self.show["rail"].get() and "railway" in here:
                    self.draw_rails(rails, frame, radius, w, h)
                if self.show["drawn"].get():
                    self.draw_drawn([(p, k, label)
                                     for p, k, label, n in drawn if n in here],
                                    frame, radius, w, h)
                if self.show["building"].get() and "building" in here:
                    self.draw_buildings(buildings, lay["building"][1], frame,
                                        radius, w, h)
            if plan and self.show["course"].get():
                self.draw_course(plan, frame, radius, w, h)
            if route:
                self.draw_route(*route, frame, radius, w, h)

        def spot(p):
            xy = heightmap.to_map(p, frame, radius)
            if xy and abs(xy[0]) <= half_w and abs(xy[1]) <= half_h:
                return w / 2 + xy[0] / self.mpp, h / 2 - xy[1] / self.mpp
            return None

        if frame:
            # POIs' heights can be switched off; survey waypoints' stay.
            elev = {wp["id"]: wp.get("elev") for wp in wps
                    if wp["planet"] == self.body
                    and (self.show["poi_height"].get()
                         or wp.get("type", "poi") != "poi")}
            subs = {wp["id"]: wp.get("sub", "") for wp in wps}
            names = {wp["id"]: wp["name"] for wp in wps}
            self.pin_items = {}
            for wid, p in starmap.body_points(of_types(wps, self.types),
                                              self.body):
                xy = spot(p)
                if xy:
                    sx, sy = xy
                    name = names[wid]
                    gap = 7
                    if subtypes.icon_of(subs.get(wid)):
                        items = draw_icon(c, subs[wid], sx, sy, px(8))
                        gap = px(8) + 3
                    else:
                        items = [c.create_oval(sx - 4, sy - 4, sx + 4, sy + 4,
                                               fill=PIN, outline=BG)]
                    # A spot height, as BM 1570 on a paper map. With names
                    # switched off the height still shows where it is on.
                    z = elev.get(wid)
                    text = " ".join(
                        ([name] if self.show["names"].get() else [])
                        + ([] if z is None else [f"{z:.0f}"]))
                    if text:
                        items.append(c.create_text(
                            sx + gap, sy, anchor="w", font=mono(9),
                            fill=label_colour(self.state.cfg,
                                              "heightmap_label_colour"),
                            text=text))
                    for item in items:      # right-click any part of it
                        self.pin_items[item] = wid
            xy = spot(pos) if here == self.body and pos else None
            if xy:
                sx, sy = xy
                # A dark edge first, so it shows over cells, lines and labels.
                for colour, wide in ((BG, 5), (ACCENT, 2)):
                    c.create_line(sx - 9, sy, sx + 9, sy, fill=colour,
                                  width=wide)
                    c.create_line(sx, sy - 9, sx, sy + 9, fill=colour,
                                  width=wide)
        else:
            c.create_text(w / 2, h / 2, fill=FG, font=mono(),
                          text=f"Nothing surveyed on {self.body} yet")
        if frame:
            self.draw_frames(frame, radius, w, h)
        self.scale_bar(w, h)
        self.draw_legend(lo, hi, bool(near))
        self.info.config(text=f"{self.seen[1]:,} cells surveyed\n"
                              f"{len(near):,} in view\n"
                              f"{2 * half_w / 1000:,.1f} x "
                              f"{2 * half_h / 1000:,.1f} km\n"
                              f"{self.mpp:g} m a pixel\n"
                              + (f"contour interval {interval:g} m"
                                 if rec else ""))

    def draw_frames(self, frame, radius, w, h):
        """Maps' frames. While one is being set (frame_map): its frame fixed
        in the middle of the window, the ground outside it dimmed. Else,
        with Map frames on, the frame of each map of this body, dashed, its
        name over its top left corner."""
        c = self.canvas
        if self.framing:
            fw, fh = maps.fit(w, h, self.FRAME_PAD)
            px = [(w / 2 - fw / 2, h / 2 - fh / 2),
                  (w / 2 + fw / 2, h / 2 - fh / 2),
                  (w / 2 + fw / 2, h / 2 + fh / 2),
                  (w / 2 - fw / 2, h / 2 + fh / 2)]
            dim = Image.new("RGBA", (w, h), (0, 0, 0, self.DIM_ALPHA))
            ImageDraw.Draw(dim).polygon(px, fill=(0, 0, 0, 0))
            self.pictures.append(ImageTk.PhotoImage(dim))
            c.create_image(0, 0, image=self.pictures[-1], anchor="nw")
            c.create_polygon(*[v for p in px for v in p], fill="",
                             outline=ACCENT, width=2)
            c.create_text(px[0][0], px[0][1] - 4, anchor="sw", fill=ACCENT,
                          font=mono(9, True),
                          text=self.framing["map"]["name"])
            return
        if not self.show["frames"].get():
            return
        with self.state.lock:
            ms = maps.of_body(self.state.maps, self.body)
        for m in ms:
            xys = self.on_map(maps.outline(m, radius), frame, radius)
            if not xys:
                continue
            px = [(w / 2 + x / self.mpp, h / 2 - y / self.mpp)
                  for x, y in xys]
            c.create_polygon(*[v for p in px for v in p], fill="",
                             outline=ACCENT, width=1, dash=(6, 4))
            c.create_text(px[0][0], px[0][1] - 3, anchor="sw", fill=ACCENT,
                          font=mono(8), text=m["name"])

    def draw_grid(self, frame, radius, w, h):
        """LAT and LON lines, labelled on the left and top edges."""
        c = self.canvas
        step, lines = heightmap.map_grid(frame, radius, w / 2 * self.mpp,
                                         h / 2 * self.mpp, 120 * self.mpp)
        places = max(0, math.ceil(-math.log10(step) - 1e-9))
        for kind, deg, runs in lines:
            edge = None                 # leftmost or topmost point in view
            for run in runs:
                flat = []
                for x, y in run:
                    sx, sy = w / 2 + x / self.mpp, h / 2 - y / self.mpp
                    flat += [sx, sy]
                    # Labels stay out of each other's corner and off the
                    # scale bar, bottom left.
                    free = (0 <= sx <= w and 20 <= sy <= h - 30
                            if kind == "lat" else
                            45 <= sx <= w and 0 <= sy <= h)
                    if free and (
                            edge is None or
                            (sx < edge[0] if kind == "lat" else sy < edge[1])):
                        edge = (sx, sy)
                c.create_line(*flat, fill=GRID_LINE, tags="grid")
            if edge:
                c.create_text(edge[0] + 3, edge[1] + (-2 if kind == "lat"
                                                      else 2),
                              anchor="sw" if kind == "lat" else "nw",
                              text=f"{deg:+.{places}f}", fill=FG,
                              font=mono(8), tags="grid")

    def draw_contours(self, frame, radius, interval, w, h):
        """The kept contour lines (see draw) over the cells, every
        INDEX_EVERY-th thicker and labelled with its height. Only near
        measured ground: see height_grid."""
        c = self.canvas
        half_w, half_h = w / 2 * self.mpp, h / 2 * self.mpp
        labels = []
        for level, line in self.contours[3]:
            index = heightmap.is_index(level, interval)
            xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                                 for p in line) if xy]
            for run in heightmap.in_view(xys, half_w, half_h):
                flat = [v for x, y in run
                        for v in (w / 2 + x / self.mpp, h / 2 - y / self.mpp)]
                c.create_line(*flat, fill=CONTOUR_INDEX if index else CONTOUR,
                              width=2 if index else 1)
                xy = heightmap.half_way(run, 80 * self.mpp) if index else None
                if xy:
                    labels.append((w / 2 + xy[0] / self.mpp,
                                   h / 2 - xy[1] / self.mpp, f"{level:g}"))
        # Labels last, each on a patch of background that breaks its line.
        for sx, sy, text in labels:
            t = c.create_text(sx, sy, text=text, fill=CONTOUR_INDEX,
                              font=mono(8))
            c.tag_lower(c.create_rectangle(*c.bbox(t), fill=heightmap.GREY,
                                           outline=""), t)

    def draw_course(self, plan, frame, radius, w, h):
        """The survey square: legs flown dim, the one you are on (or will
        resume at) bright, the next one faint, all dashed."""
        c = self.canvas
        k = plan["leg"]
        pts = survey.corners(k + 2, plan["spacing"])
        start = heightmap.local_frame(plan["origin"])
        xys = [heightmap.to_map(heightmap.from_map(x, y, start, radius),
                                frame, radius) for x, y in pts]
        for i, (p, q) in enumerate(zip(xys, xys[1:])):
            if p is None or q is None:
                continue
            colour = (COURSE[2] if i > k else
                      COURSE[1] if i == k else COURSE[0])
            c.create_line(w / 2 + p[0] / self.mpp, h / 2 - p[1] / self.mpp,
                          w / 2 + q[0] / self.mpp, h / 2 - q[1] / self.mpp,
                          fill=colour, width=4 if i == k else 3,
                          dash=(10, 6))

    @staticmethod
    def on_map(points, frame, radius):
        """Planet-frame points as (east m, north m) on the map, or None if
        any is on the far half of the body."""
        xys = [heightmap.to_map(p, frame, radius) for p in points]
        return None if None in xys else xys

    @staticmethod
    def outside(cells, lake_xys):
        """The cells (as local_cells gives them) outside every lake."""
        return mapprint.outside(cells, lake_xys)

    def draw_lakes(self, lake_xys, rivers, opacity, frame, radius, w, h):
        """Lakes as flat water: every lake painted into one mask as its fan
        of triangles (traces.fan), so lakes that overlap (one surveyed in
        parts) merge, and the shore line is the mask's edge. Drawn as an
        image, the water `opacity` % and the shore solid; the LAT/LON grid
        is raised back over it. Waterways go into the same mask as lines
        width_m wide to scale (never under 2 px), so a river running into a
        lake joins it."""
        c = self.canvas
        mask = Image.new("L", (w, h), 0)
        paint = ImageDraw.Draw(mask)
        half_w, half_h = w / 2 * self.mpp, h / 2 * self.mpp
        for points, width_m in rivers:
            xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                                 for p in points) if xy]
            for run in heightmap.in_view(xys, half_w, half_h):
                paint.line([(w / 2 + x / self.mpp, h / 2 - y / self.mpp)
                            for x, y in run], fill=255,
                           width=max(2, round(width_m / self.mpp)),
                           joint="curve")
        labels = []
        for runs, level, _ in lake_xys:
            pxs = [[(w / 2 + x / self.mpp, h / 2 - y / self.mpp)
                    for x, y in xys] for xys in runs]
            xs = [p[0] for px in pxs for p in px]
            ys = [p[1] for px in pxs for p in px]
            if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
                continue
            for px in pxs:
                for tri in traces.fan(px):
                    paint.polygon(tri, fill=255)
            if level is not None:
                labels.append((sum(xs) / len(xs), sum(ys) / len(ys), level))
        wet = np.array(mask) > 0
        if not wet.any():
            return
        # The shore: water within 2 px of land. Padded with the edge's own
        # pixels, so a lake running off the map has no shore along the edge.
        inner = wet
        for _ in range(2):
            p = np.pad(inner, 1, mode="edge")
            inner = (p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1]
                     & p[1:-1, :-2] & p[1:-1, 2:])
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[wet] = (*WATER, traces.alpha(opacity))
        rgba[wet & ~inner] = (*SHORE, 255)
        self.pictures.append(ImageTk.PhotoImage(Image.fromarray(rgba,
                                                                "RGBA")))
        c.tag_raise("grid", c.create_image(0, 0, image=self.pictures[-1],
                                           anchor="nw"))
        # The level, as a lake's elevation is printed on a paper map.
        for x, y, level in labels:
            c.create_text(x, y, text=f"{level:,.0f}", fill="#%02x%02x%02x"
                          % SHORE, font=mono(9))

    def draw_rails(self, rails, frame, radius, w, h):
        """Traced railways, as the map symbol for a multiple track railroad:
        two black lines (a wide black one with a thin light one down its
        middle) and ticks across them."""
        c = self.canvas
        half_w, half_h = w / 2 * self.mpp, h / 2 * self.mpp
        for points in rails:
            xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                                 for p in points) if xy]
            for run in heightmap.in_view(xys, half_w, half_h):
                px = [(w / 2 + x / self.mpp, h / 2 - y / self.mpp)
                      for x, y in run]
                flat = [v for p in px for v in p]
                c.create_line(*flat, fill=RAIL, width=6, joinstyle="round")
                c.create_line(*flat, fill=RAIL_GAP, width=2,
                              joinstyle="round")
                for x0, y0, x1, y1 in traces.ticks(px):
                    c.create_line(x0, y0, x1, y1, fill=RAIL, width=2)

    def draw_drawn(self, drawn, frame, radius, w, h):
        """Roads, borders and the other lines drawn by pattern, each in its
        kind's style, colour and width (traces.KINDS), its name by its
        middle."""
        c = self.canvas
        half_w, half_h = w / 2 * self.mpp, h / 2 * self.mpp
        for points, kind, label in drawn:
            xys = [xy for xy in (heightmap.to_map(p, frame, radius)
                                 for p in points) if xy]
            for run in heightmap.in_view(xys, half_w, half_h):
                self.stroke([(w / 2 + x / self.mpp, h / 2 - y / self.mpp)
                             for x, y in run], kind)
            mid = heightmap.half_way(xys, 0) if label and xys else None
            if mid is not None:
                c.create_text(w / 2 + mid[0] / self.mpp + 8,
                              h / 2 - mid[1] / self.mpp - 8, text=label,
                              anchor="sw", fill=kind["colour"], font=mono(8))

    def stroke(self, px, kind):
        """A line of screen points in its kind's style (traces.pattern),
        colour and width."""
        c = self.canvas
        colour, width = kind["colour"], kind["width"]
        runs, dots = traces.pattern(px, kind["style"])
        for r in runs:
            c.create_line(*[v for p in r for v in p], fill=colour,
                          width=width, joinstyle="round", capstyle="round")
        r = max(1.5, 0.6 * width)     # a dot's radius
        for x, y in dots:
            c.create_oval(x - r, y - r, x + r, y + r, fill=colour,
                          outline="")

    def draw_zones(self, zones, frame, radius, w, h):
        """Hostile areas, swamps and the other areas drawn by pattern, as
        (points, kind, label, opacity %): each washed over with its kind's
        fill at its opacity, its border in its style with the inside on the
        left (a hostile area's ticks point in), a swamp's tufts over it, its
        name in its middle. The LAT/LON grid is raised back over the wash."""
        c = self.canvas
        shown = []
        for points, kind, label, opacity in zones:
            xys = self.on_map(traces.ring(points), frame, radius)
            if not xys:
                continue
            px = [(w / 2 + x / self.mpp, h / 2 - y / self.mpp)
                  for x, y in xys]
            xs, ys = [p[0] for p in px], [p[1] for p in px]
            if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
                continue
            shown.append((px, kind, label, opacity))
        # The washes first, as one image, then everything over them.
        if any(kind["fill"] for _, kind, _, _ in shown):
            wash = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            paint = ImageDraw.Draw(wash)
            for px, kind, _, opacity in shown:
                if kind["fill"]:
                    paint.polygon(px, fill=ImageColor.getrgb(kind["fill"])
                                  + (traces.alpha(opacity),))
            self.pictures.append(ImageTk.PhotoImage(wash))
            c.tag_raise("grid", c.create_image(0, 0, image=self.pictures[-1],
                                               anchor="nw"))
        for px, kind, label, _ in shown:
            if kind["symbol"] == "tufts":
                self.draw_tufts(px, kind["colour"], w, h)
            border = traces.inward(px)
            self.stroke(border + border[:1], kind)
            if label:
                c.create_text(sum(p[0] for p in px) / len(px),
                              sum(p[1] for p in px) / len(px), text=label,
                              fill=kind["colour"], font=mono(8))

    def draw_tufts(self, px, colour, w, h):
        """A swamp's tufts (traces.tuft) on a staggered grid, where they
        fall inside the outline `px`."""
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon(px, fill=255)
        inside = np.array(mask) > 0
        step = traces.TUFT_EVERY_PX
        for row, y in enumerate(np.arange(step / 2, h, step)):
            for x in np.arange(step / 2 + (row % 2) * step / 2, w, step):
                if inside[int(y), int(x)]:
                    for seg in traces.tuft(x, y):
                        self.canvas.create_line(*seg, fill=colour, width=1)

    def draw_buildings(self, buildings, opacity, frame, radius, w, h):
        """Buildings walked round, squared off or as walked (traces.outline):
        tan with a dark outline, as on a large-scale paper map. A building's
        name is printed on it where it fits inside the walls; where it does
        not, it shows by the pointer while the pointer is over the building
        (names printed past the walls piled up and buried the map; see
        on_motion). The fill is a picture at `opacity` %, the outline over
        it solid. Right-click one for its menu (see context_menu)."""
        c = self.canvas
        shown = []
        for points, offset, label, name, shape in buildings:
            xys = self.on_map(points, frame, radius)
            if not xys:
                continue
            px = [(w / 2 + x / self.mpp, h / 2 - y / self.mpp)
                  for x, y in traces.outline(xys, offset, shape)]
            xs, ys = [p[0] for p in px], [p[1] for p in px]
            if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
                continue
            shown.append((px, name, label))
        if not shown:
            return
        fills = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        paint = ImageDraw.Draw(fills)
        for px, _, _ in shown:
            paint.polygon(px, fill=ImageColor.getrgb(BUILDING)
                          + (traces.alpha(opacity),))
        self.pictures.append(ImageTk.PhotoImage(fills))
        c.create_image(0, 0, image=self.pictures[-1], anchor="nw")
        for px, name, label in shown:
            c.create_polygon(*[v for p in px for v in p], fill="",
                             outline=BUILDING_EDGE, width=1)
            self.building_px.append((px, (name, label)))
            if not label:
                continue
            xs, ys = [p[0] for p in px], [p[1] for p in px]
            t = c.create_text(sum(xs) / len(xs), sum(ys) / len(ys),
                              text=label, fill=BUILDING_EDGE, font=mono(8))
            x0, y0, x1, y1 = c.bbox(t)
            if all(traces.inside_ring(x, y, px)
                   for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))):
                continue
            c.delete(t)
            self.building_names.append((px, label))

    def show_name(self, e, label):
        """A building's name by the pointer, as the pointer comes over it."""
        c = self.canvas
        c.delete("name")
        t = c.create_text(e.x + 14, e.y - 12, text=label, anchor="w",
                          fill=BUILDING_EDGE, font=mono(9), tags="name")
        x0, y0, x1, y1 = c.bbox(t)
        c.tag_lower(c.create_rectangle(x0 - 3, y0 - 1, x1 + 3, y1 + 1,
                                       fill=BUILDING, outline=BUILDING_EDGE,
                                       tags="name"), t)

    def draw_route(self, hop, points, frame, radius, w, h):
        """The overland leg to a QT-no target: a dashed line from `hop`, the
        place you jump to, along `points` to the target."""
        c = self.canvas
        half_w, half_h = w / 2 * self.mpp, h / 2 * self.mpp
        xys = [heightmap.to_map(p, frame, radius) for p in points]
        for run in heightmap.in_view([xy for xy in xys if xy], half_w, half_h):
            flat = [v for x, y in run
                    for v in (w / 2 + x / self.mpp, h / 2 - y / self.mpp)]
            if len(flat) >= 4:
                c.create_line(*flat, fill=BG, width=5)
                c.create_line(*flat, fill=ROUTE, width=3, dash=(8, 5))
        start = xys[0]
        if start and abs(start[0]) <= half_w and abs(start[1]) <= half_h:
            sx, sy = w / 2 + start[0] / self.mpp, h / 2 - start[1] / self.mpp
            c.create_text(sx + 7, sy + 12, anchor="w", fill=ROUTE,
                          font=mono(9, True), text=f"QT {hop}")

    def scale_bar(self, w, h):
        """A round length up to a fifth of the map wide, bottom left."""
        want = w / 5 * self.mpp
        step = 10 ** math.floor(math.log10(want))
        length = max(k * step for k in (1, 2, 5) if k * step <= want)
        c = self.canvas
        c.create_line(12, h - 14, 12 + length / self.mpp, h - 14, fill=FG,
                      width=3)
        c.create_text(12, h - 20, anchor="sw", fill=FG, font=mono(9),
                      text=self.scl.fmt_dist(length))

    def draw_legend(self, lo, hi, any_cells):
        """The height scale, low to high, for the cells in view.

        The bar's colours run the whole ramp whatever lo and hi are (one
        colour when they are equal), so it is drawn again only when that
        changes and the labels are just updated. Its 160 lines cost as much
        as the contours on every drag step (2026-10-17)."""
        lg = self.legend
        key = any_cells and hi > lo
        if any_cells and key == self.legend_key:
            lg.itemconfig("lo", text=self.scl.fmt_dist(lo))
            lg.itemconfig("hi", text=self.scl.fmt_dist(hi))
            return
        self.legend_key = key if any_cells else None
        lg.delete("all")
        lg.configure(height=18 + mono(9).metrics("linespace"))
        if not any_cells:
            return
        for i in range(160):
            lg.create_line(i, 0, i, 14, fill=heightmap.height_colour(
                lo + (hi - lo) * i / 159, lo, hi))
        lg.create_text(0, 18, anchor="nw", fill=DIM, font=mono(9),
                       text=self.scl.fmt_dist(lo), tags="lo")
        lg.create_text(160, 18, anchor="ne", fill=DIM, font=mono(9),
                       text=self.scl.fmt_dist(hi), tags="hi")


# ----------------------------------------------------------------------------
# Waypoint editor
# ----------------------------------------------------------------------------


class WaypointEditor:
    COLUMNS = (("name", 220), ("type", 70), ("sub", 120), ("qt", 40),
               ("private", 70), ("discord", 70), ("system", 80),
               ("body", 110),
               ("around", 120),
               ("lat", 80), ("lon", 85), ("height", 280), ("saved", 170),
               ("note", 200))

    def __init__(self, parent, state, scl, nav):
        self.state, self.scl, self.nav = state, scl, nav
        self.frame = tk.Frame(parent, bg=BG)
        self.seen = None

        bar = tk.Frame(self.frame, bg=BG)
        bar.pack(side="top", fill="x", pady=(0, 6))
        self.sys_var, self.body_var, self.type_var = (
            tk.StringVar(value="all") for _ in range(3))
        picks = []
        for text, var in (("System", self.sys_var), ("Body", self.body_var),
                          ("Type", self.type_var)):
            tk.Label(bar, text=text, font=mono(), fg=DIM, bg=BG).pack(
                side="left", padx=(0 if not picks else 18, 6))
            picks.append(ttk.Combobox(bar, textvariable=var, width=16,
                                      state="readonly", values=("all",)))
            picks[-1].pack(side="left")
        self.sys_pick, self.body_pick, type_pick = picks
        type_pick.config(values=["all"] + list(TYPE_LABELS.values()))
        self.sys_pick.bind("<<ComboboxSelected>>", lambda e: self.pick_system())
        self.body_pick.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        type_pick.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        # Narrows the list by name as you type.
        tk.Label(bar, text="Search", font=mono(), fg=DIM, bg=BG).pack(
            side="left", padx=(18, 6))
        self.search_var = tk.StringVar()
        ttk.Entry(bar, textvariable=self.search_var, font=mono(),
                  width=24).pack(side="left")
        self.search_var.trace_add("write", lambda *_: self.refresh())

        self.tree = ttk.Treeview(self.frame, show="headings",
                                 selectmode="extended",
                                 columns=[n for n, _ in self.COLUMNS])
        for name, width in self.COLUMNS:
            self.tree.heading(name, text=name.upper())
            set_column(self.tree, name, width, stretch=name == "note")
        sortable(self.tree, scl.sort_key)
        # Rebuilt on each right-click: the waypoint menu, then Copy/Export.
        self.menu = PopMenu(self.tree, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground=PICK)
        scroll = ttk.Scrollbar(self.frame, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        # Everything done to a waypoint is in its right-click menu; import
        # is in the Settings tab.
        self.tree.bind("<Button-3>", self.popup)

    def selected(self):
        """The id of the one selected waypoint (rows are keyed by id, as
        names repeat); None when none or several are."""
        sel = self.tree.selection()
        return sel[0] if len(sel) == 1 else None

    def chosen(self):
        """The selected waypoint records, in list order."""
        ids = self.tree.selection()
        wps, _ = snapshot(self.state)
        by_id = {w["id"]: w for w in wps}
        return [by_id[i] for i in ids if i in by_id]

    def popup(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.update_idletasks()
        self.menu.delete(0, "end")
        if len(self.tree.selection()) > 1:     # the menu's title
            self.menu.title = f"{len(self.tree.selection())} waypoints"
        wid = self.selected()           # one waypoint: its menu first
        if wid is not None and self.nav.waypoint_menu(
                self.menu, wid, "list", (event.x_root, event.y_root)):
            self.menu.add_separator()
        self.menu.add_command(label="Copy", command=self.copy)
        self.menu.add_command(label="Export...", command=self.export)
        self.menu.add_separator()
        flag_menu(self.menu, self.chosen(), self.set_flag)
        self.menu.tk_popup(event.x_root, event.y_root)

    def set_flag(self, flag, on):
        """Private or Share to Discord on or off for the waypoints picked."""
        picked = self.tree.selection()
        found, skipped = self.scl.flag_saved(
            self.state, "waypoint", picked, flag, on)
        flash_flagged(self.state, found, skipped, flag, on)
        self.refresh()
        offer_unpost(self.nav, [("waypoint", k) for k in picked], flag, on,
                     self.frame)

    def show_body(self, system, body):
        """Show a body's waypoints only: its system and body picked, every
        type, no search."""
        self.sys_var.set(system or "all")
        self.type_var.set("all")
        self.search_var.set("")
        self.refresh()
        self.body_var.set(body)
        self.refresh()

    def to_share(self, title, verb):
        """(waypoints, private ones left out) for Copy or Export, or None
        to stop. Private waypoints are left out, except one selected on its
        own: that one is shared to someone chosen, once OK'd."""
        chosen = self.chosen()
        if len(chosen) == 1 and chosen[0].get("private"):
            if ask_buttons(title, f"'{chosen[0]['name']}' is a private "
                           f"waypoint. {verb} it anyway?", self.frame,
                           ("OK", "Cancel")) != "OK":
                return None
            return chosen, 0
        wps = self.scl.exportable(chosen)
        return wps, len(chosen) - len(wps)

    def copy(self):
        got = self.to_share("Copy waypoint", "Copy")
        if got is None:
            return
        wps, left = got
        _, bodies = snapshot(self.state)
        placed = {sys: starmap.placed_bodies(bodies, sys)
                  for sys in starmap.SYSTEMS}
        texts = []
        for w in wps:
            around = starmap.attached_body(w, placed.get(w.get("system"), {}))
            texts.append(self.scl.waypoint_text(w, around))
        if not texts:
            if left:
                with self.state.lock:
                    self.state.flash("nothing copied: all private")
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append("\n\n".join(texts))
        with self.state.lock:
            self.state.flash(f"copied {len(texts)} waypoint"
                             + ("s" if len(texts) > 1 else "")
                             + (f", {left} private left out" if left else ""))

    def export(self):
        got = self.to_share("Export waypoint", "Export")
        if got is None:
            return
        wps, left = got
        if not wps:
            if left:
                show_message("Export waypoints",
                             "Nothing to export: they are all private.",
                             parent=self.frame)
            return
        path = filedialog.asksaveasfilename(
            parent=self.frame, title="Export waypoints",
            defaultextension=".json", filetypes=[("Waypoints", "*.json")],
            initialfile=(wps[0]["name"] if len(wps) == 1 else "waypoints")
            + ".json")
        if not path:
            return
        try:
            self.scl.export_json(path, wps)
        except OSError as exc:
            show_message("Export waypoints", str(exc), parent=self.frame)
            return
        with self.state.lock:
            self.state.flash(f"exported {len(wps)} to {os.path.basename(path)}"
                             + (f", {left} private left out" if left else ""))

    def restore(self, parent):
        """Put back waypoints deleted earlier, from the backups of
        waypoints.json, picked by name in ask_restore."""
        st, scl = self.state, self.scl
        wps, _ = snapshot(st)
        found = scl.deleted_waypoints(wps)
        if not found:
            show_message("Restore deleted waypoints",
                         "No deleted waypoints in the backups: the last "
                         f"{scl.BACKUP_KEEP} saves of waypoints.json hold "
                         "none that is gone now.", parent=parent)
            return
        rows = [(wid, w.get("name", "?"), TYPE_LABELS.get(w.get("type"), ""),
                 w.get("sub", ""), w.get("system", "?"), w.get("planet", "?"),
                 w.get("saved", ""), since) for wid, w, since in found]
        keys = ask_restore(parent, "waypoints", "waypoints.json",
                           (("name", 190), ("type", 70), ("sub", 120),
                            ("system", 75), ("body", 110), ("saved", 165),
                            ("gone since", 165)), rows)
        if not keys:
            return
        by_id = {wid: w for wid, w, _ in found}
        with st.lock:
            back = scl.restore_waypoints(st.waypoints,
                                         [by_id[k] for k in keys])
            if back:
                scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            if scl.sync_subs(st.waypoints):     # a SUB deleted since
                scl.save_json(scl.SUBTYPES_FILE, subtypes.current())
            st.flash(f"restored {len(back)} waypoint"
                     + ("s" if len(back) != 1 else ""))
        self.refresh()
        self.nav.refresh_tabs()

    def import_file(self, parent=None):
        """Import waypoints from a shared file, its dialogs over `parent`
        (the tab it is started from; the Settings tab)."""
        parent = parent or self.frame
        path = filedialog.askopenfilename(
            parent=parent, title="Import waypoints",
            filetypes=[("Waypoints", "*.json"), ("All files", "*.*")])
        if not path:
            return
        st, scl = self.state, self.scl
        try:
            good, bad = scl.read_import_file(path)
        except (OSError, ValueError) as exc:
            show_message("Import waypoints", str(exc), parent=parent)
            return
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        added, replaced, declined = [], [], []
        always = None                    # the answer ticked "apply to all"
        for w in good:
            w = scl.prepare_import(w, now)
            wps, _ = snapshot(st)
            # Only nearness clashes: names repeat (many places are a Cave).
            nearby = scl.import_conflicts(w, wps)
            replace = None
            if nearby:
                action = always
                if action is None:
                    action, to_all = ask_conflict(
                        parent, scl.conflict_text(w, nearby))
                    if to_all:
                        always = action
                if action == "no":
                    declined.append(w["name"])
                    continue
                if action == "replace":
                    replace = nearby[0][1]["id"]
            with st.lock:
                try:
                    scl.import_waypoint(st.waypoints, w, replace)
                except ValueError as exc:
                    bad.append((f"'{w['name']}'", [str(exc)]))
                    continue
                if replace is not None and st.target == replace:
                    st.target = w["id"]
            (replaced if replace else added).append(w["name"])
        with st.lock:
            if added or replaced:
                scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            if scl.sync_subs(st.waypoints):     # a SUB of theirs, now ours
                scl.save_json(scl.SUBTYPES_FILE, subtypes.current())
            st.flash(f"imported {len(added) + len(replaced)} waypoints")
        self.refresh()
        show_message("Import waypoints", scl.import_summary(
            os.path.basename(path), added, replaced, declined, bad),
            parent=parent)

    def select(self, wid):
        """Select a waypoint by id and scroll to it, clearing a filter that
        hides it."""
        self.refresh()
        if not self.tree.exists(wid):
            for var in (self.sys_var, self.body_var, self.type_var):
                var.set("all")
            self.search_var.set("")
            self.refresh()
        if self.tree.exists(wid):
            self.tree.selection_set(wid)
            self.tree.see(wid)

    def row_values(self, w, placed):
        around = starmap.attached_body(w, placed.get(w.get("system"), {}))
        if around and "parent" in w:
            around += " (set)"
        if w.get("frame") == "system":
            lat = lon = ""
        else:
            lat, lon, _ = self.scl.to_latlon(w["x"], w["y"], w["z"])
            lat, lon = f"{lat:+.3f}", f"{lon:+.3f}"
        note = (w.get("note") or "").splitlines()
        return (w["name"], TYPE_LABELS.get(w.get("type"), ""),
                w.get("sub", ""), "yes" if w.get("qt") else "no",
                "yes" if w.get("private") else "no",
                "yes" if w.get("share") else "no",
                w.get("system", "unknown"), w["planet"],
                around or "", lat, lon,
                self.scl.describe_height(w), w.get("saved", ""),
                note[0] if note else "")

    def refresh(self):
        wps, bodies = snapshot(self.state)
        placed = {sys: starmap.placed_bodies(bodies, sys)
                  for sys in starmap.SYSTEMS}
        wtype = {v: k for k, v in TYPE_LABELS.items()}.get(self.type_var.get(),
                                                           "all")
        shown, systems, bodies = self.scl.filter_waypoints(
            wps, self.sys_var.get(), self.body_var.get(), wtype)
        shown = self.scl.search_waypoints(shown, self.search_var.get())
        for var, names in ((self.sys_var, systems), (self.body_var, bodies)):
            if var.get() not in names:
                var.set("all")
        seen = (tuple((w["id"], w["name"], w["planet"], w.get("note"),
                       w.get("parent"),
                       w.get("type"), w.get("sub"), w.get("qt"),
                       w.get("private"), w.get("share"))
                      for w in wps),
                tuple((sys, tuple(p)) for sys, p in placed.items()),
                self.sys_var.get(), self.body_var.get(), wtype,
                self.search_var.get())
        if seen == self.seen:
            return
        self.seen = seen
        self.sys_pick.config(values=["all"] + systems)
        self.body_pick.config(values=["all"] + bodies)
        chosen = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for w in sorted(shown, key=lambda q: (q.get("system", ""), q["planet"],
                                            q["name"].lower())):
            self.tree.insert("", "end", iid=w["id"],
                             values=self.row_values(w, placed))
        sort_rows(self.tree)
        chosen = [n for n in chosen if self.tree.exists(n)]
        if chosen:
            self.tree.selection_set(chosen)

    def pick_system(self):
        """A new system: its bodies are the choices, starting from all."""
        self.body_var.set("all")
        self.refresh()



class SurveysTab:
    """Every survey kept: each traced line (railway, lake, sea, waterway,
    building, in traces.json) and each body's height map (all its cells,
    in heightmap.json: a cell does not remember which survey measured it,
    so one height survey cannot be told from another). Right-click for
    the menu; deleted ones come back from Settings > Survey."""

    COLUMNS = (("name", 140), ("label", 200), ("kind", 100), ("body", 100),
               ("started", 165), ("size", 235), ("private", 70),
               ("discord", 70))

    def __init__(self, parent, state, scl, nav):
        self.state, self.scl, self.nav = state, scl, nav
        self.frame = tk.Frame(parent, bg=BG)
        self.seen = None
        tk.Label(self.frame, font=mono(9), fg=DIM, bg=BG, justify="left",
                 anchor="w",
                 text="Your traces, and each body's height map: every height "
                      "survey on a body adds to the one map, so it is kept "
                      "and deleted whole.\nRight-click for the menu; "
                      "deleted ones come back from Settings > Survey."
                 ).pack(side="top", fill="x", pady=(0, 6))
        self.tree = ttk.Treeview(self.frame, show="headings",
                                 selectmode="extended",
                                 columns=[n for n, _ in self.COLUMNS])
        for name, width in self.COLUMNS:
            self.tree.heading(name, text=name.upper())
            set_column(self.tree, name, width, stretch=name == "label")
        sortable(self.tree, scl.sort_key)
        scroll = ttk.Scrollbar(self.frame, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        # The notes of the one survey picked, under the list: what it is
        # like there (cover, ground, ...), kept as they are typed.
        box = RoundFrame(self.frame, text=" Notes ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=6)
        box.pack(side="bottom", fill="x", pady=(6, 0))
        self.notes_title = tk.Label(box, font=mono(9), fg=DIM, bg=BG,
                                    anchor="w")
        self.notes_title.pack(fill="x")
        self.notes = tk.Text(box, height=6, wrap="word", font=mono(),
                             bg=PANEL, fg=FG, insertbackground=FG,
                             relief="flat", padx=6, pady=4)
        self.notes.pack(fill="x", pady=(4, 0))
        self.notes.bind("<KeyRelease>", lambda e: self.save_notes_soon())
        self.notes.bind("<FocusOut>", lambda e: self.save_notes())
        self.noted = None           # the trace whose notes are shown
        self.notes_job = None       # a save waiting for typing to stop
        self.notes_backed = None    # the trace last backed up as typed in
        self.show_notes(None)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Button-3>", self.popup)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.on_select())
        self.menu = PopMenu(self.tree, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground=PICK)

    NOTES_SAVE_MS = 1500    # notes are saved this long after typing stops

    def on_select(self):
        """The notes of the one trace picked; those being written are kept
        first. A height map, or more than one row, has none to show."""
        sel = self.tree.selection()
        name = (sel[0][6:] if len(sel) == 1 and sel[0].startswith("trace:")
                else None)
        if name != self.noted:
            self.save_notes()
            self.show_notes(name)

    def show_notes(self, name):
        """Show trace `name`'s notes to be written in, or none (None)."""
        self.noted, self.notes_backed = name, None
        with self.state.lock:
            t = next((t for t in self.state.traces if t["name"] == name),
                     None) if name else None
            text = t.get("notes", "") if t else ""
            label = t.get("label", "") if t else ""
        self.notes.config(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", text)
        self.notes.edit_modified(False)
        if t is None:
            self.noted = None
            self.notes.config(state="disabled")
            self.notes_title.config(text="Pick one survey to read or write "
                                         "its notes.")
        else:
            self.notes_title.config(text=f"{name}  {label}".rstrip()
                                    + "  (kept as you type)")

    def save_notes_soon(self):
        """Save once typing stops for NOTES_SAVE_MS."""
        if self.notes_job is not None:
            self.frame.after_cancel(self.notes_job)
        self.notes_job = self.frame.after(self.NOTES_SAVE_MS, self.save_notes)

    def save_notes(self):
        """Keep the notes shown, if they were changed."""
        if self.notes_job is not None:
            self.frame.after_cancel(self.notes_job)
            self.notes_job = None
        if self.noted and self.notes.edit_modified():
            # Only the first save since the survey was picked keeps a
            # backup, as a report's notes (see scl.save_json).
            self.scl.note_trace(self.state, self.noted,
                                self.notes.get("1.0", "end-1c"),
                                backup=self.notes_backed != self.noted)
            self.notes_backed = self.noted
            self.notes.edit_modified(False)

    # Rows are keyed "trace:<name>" or "map:<body>".

    def refresh(self):
        def flags(rec):
            return tuple("yes" if rec.get(f) else "no"
                         for f in self.scl.FLAGS)

        with self.state.lock:
            ts = [(t["name"], t["kind"], t.get("planet"), t.get("label", ""),
                   t.get("started"), len(t["points"]), flags(t))
                  for t in self.state.traces]
            maps = {b: (len(r["cells"]), flags(r))
                    for b, r in self.state.heightmap.items()}
        seen = (tuple(ts), tuple(sorted(maps.items())))
        if seen == self.seen:
            return
        self.seen = seen
        with self.state.lock:
            lengths = {t["name"]: traces.length(t) for t in self.state.traces}
        rows = [(f"map:{b}", (b or "", 0, ""), ("height map", "",
                 "height map", b, "", f"{n:,} cells") + flagged)
                for b, (n, flagged) in maps.items()]
        for name, kind, body, label, started, n, flagged in ts:
            when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(started))
                    if started else "")
            rows.append((f"trace:{name}", (body or "", 1, kind, name), (
                name, label, kind, body or "?", when,
                f"{n:,} points, {self.scl.fmt_dist(lengths.get(name, 0))}")
                + flagged))
        chosen = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        # By body, its height map first, then its traces by kind and name.
        for iid, _, values in sorted(rows, key=lambda r: r[1]):
            self.tree.insert("", "end", iid=iid, values=values)
        sort_rows(self.tree)
        chosen = [i for i in chosen if self.tree.exists(i)]
        if chosen:
            self.tree.selection_set(chosen)

    def popup(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        self.tree.update_idletasks()
        self.menu.delete(0, "end")
        sel = self.tree.selection()
        at = (event.x_root, event.y_root)
        self.menu.title = (sel[0].partition(":")[2] if len(sel) == 1
                           else f"{len(sel)} surveys")
        if len(sel) == 1:               # one: its own menu first
            kind, _, key = sel[0].partition(":")
            if kind == "map":
                self.nav.body_menu(self.menu, key, "surveys", at)
            else:
                self.menu.add_command(
                    label="Show on height map",
                    command=lambda: self.nav.trace_to_height(key))
                values = self.tree.item(sel[0], "values")
                if values[2] == "building":
                    self.nav.building_menu(self.menu, key, values[1], at)
                elif traces.KINDS[values[2]]["named"]:
                    self.menu.add_command(
                        label="Rename...",
                        command=lambda: self.nav.rename_trace(
                            key, values[2], values[1], at))
            self.menu.add_separator()
        elif all(i.startswith("trace:") for i in sel):
            self.menu.add_command(label="Merge...", command=self.merge)
        self.menu.add_command(label="Delete...", command=self.delete)
        self.menu.add_separator()
        names, bodies = self.picked_keys()
        with self.state.lock:
            recs = ([dict(t) for t in self.state.traces if t["name"] in names]
                    + [dict(r) for b, r in self.state.heightmap.items()
                       if b in bodies])
        flag_menu(self.menu, recs, self.set_flag)
        self.menu.tk_popup(*at)

    def picked_keys(self):
        """(trace names, height map bodies) of the rows picked."""
        sel = self.tree.selection()
        return ([i[6:] for i in sel if i.startswith("trace:")],
                [i[4:] for i in sel if i.startswith("map:")])

    def set_flag(self, flag, on):
        """Private or Share to Discord on or off for the surveys picked."""
        names, bodies = self.picked_keys()
        found, skipped = (a + b for a, b in zip(
            self.scl.flag_saved(self.state, "trace", names, flag, on),
            self.scl.flag_saved(self.state, "heightmap", bodies, flag, on)))
        flash_flagged(self.state, found, skipped, flag, on)
        self.refresh()
        offer_unpost(self.nav, [("trace", n) for n in names]
                     + [("heightmap", b) for b in bodies], flag, on,
                     self.frame)

    def delete(self):
        """Delete the selected traces and height maps, once asked."""
        sel = self.tree.selection()
        if not sel:
            return
        lines = []
        for iid in sel:
            name, label, kind, body, _, size = self.tree.item(iid,
                                                              "values")[:6]
            lines.append(f"  {body} height map ({size})" if iid.startswith(
                "map:") else f"  {name}{' ' + label if label else ''} "
                             f"({kind}, {body})")
        if not ask_yes_no(
                "Delete surveys",
                f"Delete {'this' if len(sel) == 1 else f'these {len(sel)}'}?"
                "\n\n" + "\n".join(lines) + "\n\nA height map goes whole: "
                "every cell surveyed on that body. They can be put back from "
                "Settings > Survey while they are still in the backups.",
                parent=self.frame):
            return
        names = [i[6:] for i in sel if i.startswith("trace:")]
        bodies = [i[4:] for i in sel if i.startswith("map:")]
        gone = (self.scl.delete_traces(self.state, names)
                + self.scl.delete_heightmaps(self.state, bodies))
        kept = [k for k in names + bodies if k not in gone]
        with self.state.lock:
            self.state.flash(f"deleted {len(gone)} survey"
                             + ("s" if len(gone) != 1 else ""))
        if kept:
            show_message("Delete surveys", "Not deleted, still running: "
                         + ", ".join(kept) + ".\nEnd it in the overlay "
                         "first.", parent=self.frame)
        self.refresh()
        self.nav.refresh_tabs()

    def merge(self):
        """Merge the selected traces into one, once asked: of one kind, on
        one body, overlapping (traces.merge_problem). Lakes or seas whose
        levels differ are asked which level to keep."""
        names = [i[6:] for i in self.tree.selection() if i.startswith("trace:")]
        st = self.state
        with st.lock:
            ts = sorted((dict(t) for t in st.traces if t["name"] in names),
                        key=lambda t: t["name"])
            running = st.trace["name"] if st.trace else None
            rec = st.heightmap.get(ts[0]["planet"]) if ts else None
        if not ts:
            return
        radius = ((rec or {}).get("radius")
                  or self.scl.BODY_RADII.get(ts[0]["planet"], 1e6))
        problem = (f"{running} is still running; end it in the overlay first"
                   if running in names else traces.merge_problem(ts, radius))
        if problem:
            show_message("Merge surveys", f"Not merged: {problem}.",
                         parent=self.frame)
            return
        keep = ts[0]["name"]
        lines = [f"  {t['name']}{' ' + t['label'] if t.get('label') else ''}"
                 + (f", level {t['level']:,.1f} m" if "level" in t else "")
                 + (f", {t['width_m']:g} m wide" if "width_m" in t else "")
                 for t in ts]
        text = (f"Merge these into {keep}?\n\n" + "\n".join(lines)
                + "\n\nThe others are deleted. They can be put back from "
                "Settings > Survey while they are still in the backups, but "
                f"{keep} stays merged.")
        if len({t.get("width_m") for t in ts}) > 1:
            text += f"\n{keep} keeps its width, {ts[0]['width_m']:g} m."
        levels = list(dict.fromkeys(t["level"] for t in ts if "level" in t))
        level = None
        if len(levels) > 1:
            buttons = [f"Keep {v:,.1f} m" for v in levels]
            got = ask_buttons("Merge surveys", text + "\n\nWhich level to "
                              "keep?", self.frame, buttons + ["Cancel"])
            if got not in buttons:
                return
            level = levels[buttons.index(got)]
        elif not ask_yes_no("Merge surveys", text, parent=self.frame):
            return
        merged = self.scl.merge_traces(st, names, level)
        with st.lock:
            st.flash(f"merged into {merged}" if merged else "not merged")
        self.refresh()
        self.nav.refresh_tabs()

    def restore(self, parent):
        """Put back traces and height maps deleted earlier, from the backups
        of traces.json and heightmap.json, picked by name in ask_restore."""
        st, scl = self.state, self.scl
        with st.lock:
            have_traces = [dict(t) for t in st.traces]
            have_maps = set(st.heightmap)
        found = ([("t", n, t, s) for n, t, s in scl.deleted_traces(have_traces)]
                 + [("m", b, r, s)
                    for b, r, s in scl.deleted_heightmaps(have_maps)])
        if not found:
            show_message("Restore deleted surveys",
                         "No deleted surveys in the backups: the last "
                         f"{scl.BACKUP_KEEP} saves of traces.json and of "
                         "heightmap.json hold none that is gone now.",
                         parent=parent)
            return
        rows = []
        for i, (what, key, rec, since) in enumerate(found):
            if what == "m":
                rows.append((str(i), "height map", "", "height map", key, "",
                             f"{len(rec['cells']):,} cells", since))
            else:
                started = rec.get("started")
                rows.append((str(i), key, rec.get("label", ""), rec["kind"],
                             rec.get("planet") or "?",
                             time.strftime("%Y-%m-%d %H:%M",
                                           time.localtime(started))
                             if started else "",
                             f"{len(rec['points']):,} points", since))
        keys = ask_restore(parent, "surveys", "traces.json and heightmap.json",
                           (("name", 120), ("label", 190), ("kind", 100),
                            ("body", 100), ("started", 165), ("size", 130),
                            ("gone since", 165)), rows)
        if not keys:
            return
        picked = [found[int(k)] for k in keys]
        back = (scl.restore_traces(st, [r for w, _, r, _ in picked
                                        if w == "t"])
                + scl.restore_heightmaps(st, [(k, r) for w, k, r, _ in picked
                                              if w == "m"]))
        with st.lock:
            st.flash(f"restored {len(back)} survey"
                     + ("s" if len(back) != 1 else ""))
        self.refresh()
        self.nav.refresh_tabs()


def survey_kinds(scl):
    """Settings > Survey: each kind of survey as (title, what it does and
    records, [(config key, label, choices)] that can be set). The limits
    quoted are read from the code, so the text keeps up with them."""
    fast = {k: v["max_speed"] for k, v in traces.KINDS.items()}
    squares = " or ".join(f"{s:g}" for s in survey.PATTERNS.values() if s)
    misread = (f"A fix that climbed or dropped over {scl.TRACE_MAX_CLIMB:g} "
               "m/s, or moved faster than {} m/s, is taken for a misread.")

    def where(kind):            # its place in the SURVEY menu
        k = traces.KINDS[kind]
        return f"SURVEY > {traces.GROUPS[k['group']]} > {k['menu']}"

    drawn = [k for k in traces.KINDS.values()
             if "style" in k and k["group"] == "line"]
    zones = [k for k in traces.KINDS.values()
             if "style" in k and k["group"] == "area"]

    return [
        ("Height survey (SURVEY > free fly or a square)",
         "Records the ground height under you, flying or walking. Free fly "
         "goes where you like; a square winds out clockwise from the target, "
         f"legs due N, E, S or W, lines {squares} m apart.\n"
         "Records: the mean height of each ~100 m cell (heightmap.json), a "
         "survey-NNN waypoint at the first spot, the square to resume "
         "(survey.json). A position that jumped faster than "
         f"{scl.SURVEY_MAX_SPEED / 1000:g} km/s is dropped.",
         [("survey_max_height_m", "Record below (m over the sphere)",
           (1000, 2000, 3000, 4000, 5000, 6000))]),
        (f"Railway ({where('railway')})",
         "Fly or ride along a track; only where it runs matters, not its "
         "height.\nRecords: railway-NNN in traces.json, a point every "
         f"{traces.STEP_M:g} m or more, drawn as the railroad symbol. "
         + misread.format(f"{fast['railway']:g}"), []),
        (f"Lake ({where('lake')})",
         "Start low over the water, then follow the shore all the way round; "
         "END LAKE.\nRecords: lake-NNN: its level (your camera's height at "
         "the first fix, less the setting below) and the route; all between "
         "the start and the route is water. "
         + misread.format(f"{fast['lake']:g}"),
         [("water_below_cam_m", "Water below camera (m)",
           (0, 0.5, 1, 2, 3, 5, 10))]),
        (f"Sea ({where('sea')})",
         "A lake too big to skim slowly: the same level (the lake's Water "
         "below camera) and the same water, flown faster.\nRecords: sea-NNN. "
         + misread.format(f"{fast['sea']:g}"), []),
        (f"Waterway ({where('waterway')})",
         "A river or canal: fly along its middle from one end to the other; "
         "END TRACE.\nRecords: waterway-NNN, drawn as water this wide, to "
         "scale. Each keeps the width it started with (width_m in "
         "traces.json). " + misread.format(f"{fast['waterway']:g}"),
         [("waterway_width_m", "Width of a new one (m)",
           (5, 10, 20, 30, 50, 100, 200))]),
        (f"Lines (SURVEY > {traces.GROUPS['line']} > road, border ...)",
         "Fly, ride or drive along it from one end to the other; END TRACE, "
         "then name it. Each kind is drawn its own way: "
         + ", ".join(f"{k['menu']} {k['style']}" for k in drawn)
         + ". A hostile border's ticks are on your left as you fly it.\n"
         "Records: <kind>-NNN in traces.json, a point every "
         f"{traces.STEP_M:g} m or more. "
         + misread.format(f"{traces.LINE['max_speed']:g}"), []),
        (f"Building ({where('building')})",
         "Walk round it on foot in first person, close along the walls; END "
         "BUILDING, then name it.\nRecords: building-NNN, a point every "
         f"{traces.BUILDING_STEP_M:g} m, drawn squared off and pulled in by "
         "the offset (kept as offset_m). Faster than "
         f"{fast['building']:g} m/s (over at most "
         f"{traces.KINDS['building']['max_gap']:g} s) is a misread; "
         f"{scl.TRACE_LOST_DROPS} in a row is LOST TRACK.",
         [("building_wall_offset_m", "Wall offset of a new one (m)",
           (0, 0.25, 0.5, 0.75, 1, 1.5))]),
        (f"Areas (SURVEY > {traces.GROUPS['area']} > hostile area, swamp "
         "...)",
         "Fly along its border all the way round, back to where you started; "
         "END AREA, then name it. Each kind is drawn its own way: "
         + ", ".join(f"{k['menu']} ("
                     + ("washed over, " if k["fill"] else "")
                     + f"border {k['style']}"
                     + (f", {k['symbol']}" if k["symbol"] else "") + ")"
                     for k in zones)
         + ". A hostile area's ticks point inside.\nRecords: <kind>-NNN in "
         f"traces.json, a point every {traces.STEP_M:g} m or more; a lap "
         "flown on past the start is cut where it came back within "
         f"{traces.AREA_LAP_M:g} m of it. "
         + misread.format(f"{traces.AREA['max_speed']:g}"), []),
    ]


class MapsTab:
    """Maps kept to print (maps.py): the list on the left, grouped by body,
    searched and filtered by body; the map picked in the middle, its pages
    as they print (mapprint.py); what can be set for it on the right. New
    maps are made, and maps framed, on the height map."""

    ALL = "all bodies"
    NOTES_SAVE_MS = 1500    # notes are saved this long after typing stops
    RENDER_MS = 300         # the page is drawn again this long after a change
    # What a map shows besides its surveys (maps.SWITCHES), as ticked here.
    ALSO = (("relief", "Filled relief"), ("lines", "Height lines"),
            ("samples", "Survey samples"), ("poi", "POI waypoints"),
            ("survey", "Survey waypoints"), ("names", "Waypoint names"))

    def __init__(self, parent, state, scl, nav):
        self.state, self.scl, self.nav = state, scl, nav
        self.frame = tk.Frame(parent, bg=BG)
        self.seen = None            # what the list was last filled from
        self.mid = None             # the map picked
        self.shown = None           # its "updated" as last shown
        self.backed = None          # the map last backed up as it was set
        self.notes_job = None       # a save waiting for typing to stop
        self.notes = None           # its notes box, while one is picked
        self.info = None            # and the line saying where and when
        self.drawn = None           # what the page was last drawn from
        self.render_job = None      # a draw waiting for changes to stop
        self.picture = None         # the page drawn, held for Tk

        # The left menu, in folding sections as the star map's: the maps,
        # then what can be set for the one picked (show_options).
        left = tk.Frame(self.frame, bg=BG)
        left.pack(side="left", fill="y", padx=(0, 8))
        self.panels = [left]            # hidden by menu / collapse
        # As tall as its list needs (fit_list), not the whole column.
        sec = kept_section(state, scl, left, "Maps", key="Maps: Maps")
        sec.pack()
        self.left = left
        tk.Label(sec.body, text="Search", font=mono(), fg=DIM,
                 bg=BG).pack(anchor="w")
        self.search = tk.StringVar()
        ttk.Entry(sec.body, textvariable=self.search, font=mono(),
                  width=24).pack(anchor="w", fill="x", pady=(0, 6))
        self.search.trace_add("write", lambda *a: self.fill())
        tk.Label(sec.body, text="Body", font=mono(), fg=DIM,
                 bg=BG).pack(anchor="w")
        self.body_var = tk.StringVar(value=self.ALL)
        self.body_pick = ttk.Combobox(sec.body, textvariable=self.body_var,
                                      state="readonly", width=22,
                                      values=[self.ALL])
        self.body_pick.pack(anchor="w", pady=(0, 6))
        self.body_pick.bind("<<ComboboxSelected>>", lambda e: self.fill())
        box = tk.Frame(sec.body, bg=BG)
        box.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(box, show="tree", selectmode="browse")
        set_column(self.tree, "#0", MAPS_LIST_PX)
        scroll = ttk.Scrollbar(box, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.picked())
        tk.Label(sec.body, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="New maps are made on the height\nmap: New map..."
                 ).pack(anchor="w", pady=(6, 0))
        # The picked map's sections, under the list.
        self.options = tk.Frame(left, bg=BG)
        self.options.pack(fill="x")
        self.fit_job = None
        for w, ev in ((self.tree, "<<TreeviewOpen>>"),
                      (self.tree, "<<TreeviewClose>>"),
                      (left, "<Configure>"), (self.options, "<Configure>")):
            w.bind(ev, lambda e: self.fit_soon(), add="+")

        # The map picked: a page of it as it prints, as big as fits.
        middle = tk.Frame(self.frame, bg=BG)
        middle.pack(side="left", fill="both", expand=True)
        # A switch a page, as many as the map takes (see page_switches).
        self.pages_row = tk.Frame(middle, bg=BG)
        self.pages_row.pack(anchor="w")
        self.page = tk.IntVar(value=1)
        self.page_count = None
        self.sheet = tk.Label(middle, bg=BG, fg=DIM, font=mono())
        self.sheet.pack(fill="both", expand=True, pady=(4, 0))
        self.sheet.bind("<Configure>", lambda e: self.render_soon())
        self.show_options(None)

    def current(self):
        """The map picked, or None if there is none (or it is gone)."""
        with self.state.lock:
            return next((m for m in self.state.maps
                         if m["id"] == self.mid), None)

    def render_soon(self):
        """Draw the page once changes stop for RENDER_MS."""
        if self.render_job is not None:
            self.frame.after_cancel(self.render_job)
        self.render_job = self.frame.after(self.RENDER_MS, self.render)

    def look(self):
        """What the page is drawn from: the map, its body's surveys and
        waypoints, the page shown and the room for it. Lock held."""
        m = next((m for m in self.state.maps if m["id"] == self.mid), None)
        if m is None:
            return None
        body = m["body"]
        return (m["id"], m.get("updated"),
                heightmap.cell_count(self.state.heightmap, body),
                tuple((t["name"], len(t["points"]), t.get("label"),
                       t.get("notes"))
                      for t in self.state.traces if t["planet"] == body),
                tuple((w["id"], w["name"], w.get("type"), w.get("sub"),
                       w.get("x"), w.get("y"), w.get("z"))
                      for w in self.state.waypoints
                      if w.get("planet") == body),
                self.page.get(), self.sheet.winfo_width(),
                self.sheet.winfo_height())

    def gathered(self, m):
        """What map `m` is drawn from (mapprint.gather). Lock held."""
        st = self.state
        rec = st.heightmap.get(m["body"])
        radius = (rec["radius"] if rec
                  else self.scl.BODY_RADII.get(m["body"], 1.0))
        return mapprint.gather(m, rec, radius, st.traces, st.waypoints,
                               st.cfg["building_wall_offset_m"])

    def render(self):
        """The page shown, drawn small enough to fit (mapprint), or a word
        on picking a map."""
        self.render_job = None
        st = self.state
        with st.lock:
            self.drawn = self.look()
            m = next((dict(m) for m in st.maps if m["id"] == self.mid), None)
            if m is not None:
                data = self.gathered(m)
        if m is None:
            self.picture = None
            self.sheet.config(image="", text="")
            return
        data["system"] = self.nav.body_system(m["body"])
        w, h = self.sheet.winfo_width(), self.sheet.winfo_height()
        if w < 50 or h < 50:
            return
        dpi = min(w / maps.PAGE_MM[0], h / maps.PAGE_MM[1]) * 25.4
        # A page that cannot be drawn says so, rather than leave the last
        # map drawn in its place (2026-11-08: one did, unnoticed).
        try:
            page, numbers, overflow = mapprint.page1(m, data, dpi)
            pages = [page] + mapprint.later_pages(m, data, dpi, overflow)
        except Exception as exc:
            self.state.log(f"map {m['name']} not drawn: "
                           + traceback.format_exc().replace("\n", " | "))
            self.picture = None
            self.sheet.config(image="", text=f"{m['name']} could not be "
                              f"drawn: {exc}\n(details in the log)")
            return
        self.page_switches(len(pages))
        self.picture = ImageTk.PhotoImage(pages[self.page.get() - 1])
        self.sheet.config(image=self.picture, text="")
        # Waypoints numbered for the first time keep their numbers.
        if numbers != (m.get("numbers") or {}):
            self.scl.update_map(self.state, m["id"], {"numbers": numbers},
                                backup=False)

    def page_switches(self, count):
        """A switch for each of the map's `count` pages, page 1 picked if
        the one picked is gone."""
        if count == self.page_count:
            return
        self.page_count = count
        for child in self.pages_row.winfo_children():
            child.destroy()
        if self.page.get() > count:
            self.page.set(1)
        for n in range(1, count + 1):
            ttk.Radiobutton(self.pages_row, text=f"Page {n}", value=n,
                            variable=self.page,
                            command=self.render_soon).pack(side="left",
                                                           padx=(0, 10))

    # --- the list -------------------------------------------------------

    def refresh(self):
        """The list, when the maps changed; the map picked, when it changed
        elsewhere (framed on the height map) or what it shows did."""
        with self.state.lock:
            seen = tuple((m["id"], m["name"], m["body"], m.get("notes", ""))
                         for m in self.state.maps) + (_dpi[0],)
        if seen != self.seen:      # the Windows scale moves where names are cut
            self.fill()
        m = self.current()
        if m is not None and m.get("updated") != self.shown:
            self.shown = m.get("updated")
            self.show_info(m)
        with self.state.lock:
            seen = self.look()
        if seen != self.drawn and self.render_job is None:
            self.render_soon()

    def fill(self):
        """The list as searched and filtered, the map picked kept picked."""
        with self.state.lock:
            ms = [dict(m) for m in self.state.maps]
        self.seen = tuple((m["id"], m["name"], m["body"], m.get("notes", ""))
                          for m in ms) + (_dpi[0],)
        bodies = sorted({m["body"] for m in ms})
        self.body_pick.config(values=[self.ALL] + bodies)
        if self.body_var.get() not in bodies:
            self.body_var.set(self.ALL)
        body = None if self.body_var.get() == self.ALL else self.body_var.get()
        self.tree.delete(*self.tree.get_children())

        def cut(name, depth):
            """`name` ended in "..." if too long for the list at `depth` (0
            a body, 1 a map), as in the star map's Waypoints list."""
            return fit_text(name, px(MAPS_LIST_PX) - (depth + 1) * 20 - px(8),
                            mono().measure)

        for b, group in maps.listed(ms, body, self.search.get()):
            self.tree.insert("", "end", iid=f"body:{b}", open=True,
                             text=cut(f"{b} ({len(group)})", 0))
            for m in group:
                self.tree.insert(f"body:{b}", "end", iid=f"map:{m['id']}",
                                 text=cut(m["name"], 1))
        iid = f"map:{self.mid}"
        if self.mid and self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)
        self.fit_soon()

    def fit_soon(self):
        """fit_list once things settle: a fold or a resize comes as several
        events."""
        if self.fit_job is None:
            self.fit_job = self.frame.after_idle(self.fit_list)

    def fit_list(self):
        """The list as tall as its lines shown (open groups and their maps),
        at most what the left column has room for beside everything else in
        it (the other sections, open or folded); three lines at least."""
        self.fit_job = None
        tree, left = self.tree, self.left
        shown = list_lines(tree)
        if left.winfo_height() > 1:       # laid out: the room is known
            other = left.winfo_reqheight() - tree.winfo_reqheight()
            room = (left.winfo_height() - other) // px(ROW_PX)
            shown = min(shown, room)
        rows = max(3, shown)
        if int(tree["height"]) != rows:
            tree.configure(height=rows)

    def select(self, mid):
        """Pick map `mid`, the search and filter cleared if they hide it."""
        if not self.tree.exists(f"map:{mid}"):
            self.search.set("")
            self.body_var.set(self.ALL)
            self.fill()
        if self.tree.exists(f"map:{mid}"):
            self.tree.selection_set(f"map:{mid}")
            self.tree.see(f"map:{mid}")

    def picked(self):
        """A map picked in the list (a body line picks none): shown, with
        its options. One made before maps had this tab is brought up to
        date first (maps.settled)."""
        sel = self.tree.selection()
        mid = sel[0][4:] if sel and sel[0].startswith("map:") else None
        if mid == self.mid:
            return
        self.save_notes()
        self.mid, self.backed = mid, None
        m = self.current()
        if m is not None and maps.settled(m):
            self.scl.update_map(self.state, mid, maps.settled(m))
        self.shown = m.get("updated") if m else None
        self.show_options(m)
        self.render_soon()

    # --- the options ----------------------------------------------------

    def show_options(self, m):
        """What can be set for map `m`; nothing with none picked (None)."""
        self.notes = self.info = None
        for child in self.options.winfo_children():
            child.destroy()
        if m is None:
            return
        box = self.box(" Map ")
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", fill="x")
        tk.Label(row, text="Name:", font=mono(), fg=FG, bg=BG).pack(
            side="left", padx=(0, 6))
        self.name_var = tk.StringVar(value=m["name"])
        name = ttk.Entry(row, textvariable=self.name_var, font=mono())
        name.pack(side="left", fill="x", expand=True)
        name.bind("<Return>", lambda e: self.rename())
        name.bind("<FocusOut>", lambda e: self.rename())
        self.info = tk.Label(box, font=mono(9), fg=DIM, bg=BG,
                             justify="left", anchor="w")
        self.info.pack(anchor="w", fill="x", pady=(4, 0))
        self.show_info(m)
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", pady=(4, 0))
        tk.Label(row, text="Print in:", font=mono(), fg=FG, bg=BG).pack(
            side="left", padx=(0, 6))
        self.ink = tk.StringVar(value=maps.ink_of(m))
        for value, text in maps.INKS.items():
            ttk.Radiobutton(row, text=text, value=value, variable=self.ink,
                            command=lambda: self.keep(
                                {"ink": self.ink.get()})).pack(
                side="left", padx=(0, 8))
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", pady=(6, 0))
        ttk.Button(row, text="Reposition on height map",
                   command=lambda: self.nav.map_to_height(self.current())
                   ).pack(side="left")
        ttk.Button(row, text="Delete...", command=self.delete).pack(
            side="left", padx=(6, 0))
        ttk.Button(box, text="Export PDF...", command=self.export_pdf).pack(
            anchor="w", pady=(6, 0))
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", pady=(6, 0))
        self.flag_vars = {f: tk.BooleanVar(value=bool(m.get(f)))
                          for f in self.scl.FLAGS}
        ttk.Checkbutton(row, text="Private - never shared",
                        variable=self.flag_vars["private"],
                        command=lambda: self.set_flag("private")).pack(
            side="left")
        self.share_box = ttk.Checkbutton(
            row, text="Share to Discord", variable=self.flag_vars["share"],
            command=lambda: self.set_flag("share"))
        self.share_box.pack(side="left", padx=(12, 0))
        self.share_box.state(["disabled" if m.get("private")
                              or m.get(discordshare.FROM) else "!disabled"])

        box = self.box(" Surveys shown ")
        LayerTable(box, m["layers"],
                   lambda chosen: self.keep({"layers": chosen}),
                   m.get("hidden", ()),
                   lambda hidden: self.keep({"hidden": hidden}), blocks=1)

        box = self.box(" Also shown ")
        on = maps.view_switches(m)
        self.also = {}
        for name, text in self.ALSO:
            self.also[name] = tk.BooleanVar(value=on[name])
            ttk.Checkbutton(box, text=text, variable=self.also[name],
                            command=self.also_ticked).pack(anchor="w")

        box = self.box(" Legend ")
        lg = maps.legend_of(m)
        corner = {v: k for k, v in maps.CORNERS.items()}[lg["corner"]]
        self.legend_vars = {"corner": tk.StringVar(value=corner),
                            "w_mm": tk.StringVar(value=f"{lg['w_mm']:g}"),
                            "h_mm": tk.StringVar(value=f"{lg['h_mm']:g}")}
        grid = tk.Frame(box, bg=BG)
        grid.pack(anchor="w")
        for r, (key, text, values) in enumerate((
                ("corner", "Corner", list(maps.CORNERS)),
                ("w_mm", "Width mm", [str(v) for v in range(
                    20, int(maps.FRAME_MM[0]) + 1, 5)]),
                ("h_mm", "Height mm", [str(v) for v in range(
                    15, int(maps.FRAME_MM[1]) + 1, 5)]))):
            tk.Label(grid, text=text + ":", font=mono(), fg=FG,
                     bg=BG).grid(row=r, column=0, sticky="w", padx=(0, 6))
            pick = ttk.Combobox(grid, textvariable=self.legend_vars[key],
                                state="readonly", values=values,
                                width=13 if key == "corner" else 5)
            pick.grid(row=r, column=1, sticky="w", pady=1)
            pick.bind("<<ComboboxSelected>>", lambda e: self.legend_set())
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="What does not fit goes on page 2. With waypoint "
                      "names off,\neach waypoint has a number, kept once "
                      "given.").pack(anchor="w", pady=(4, 0))
        ttk.Button(box, text="Renumber waypoints",
                   command=lambda: self.keep({"numbers": {}})).pack(
            anchor="w", pady=(4, 0))

        box = self.box(" Compass ")
        cp = maps.compass_of(m)
        self.compass_vars = {
            "on": tk.BooleanVar(value=cp["on"]),
            "plate": tk.BooleanVar(value=cp["plate"]),
            "corner": tk.StringVar(value={v: k for k, v in
                                          maps.CORNERS.items()}[cp["corner"]]),
            "size_mm": tk.StringVar(value=f"{cp['size_mm']:g}")}
        ttk.Checkbutton(box, text="Show the north arrow",
                        variable=self.compass_vars["on"],
                        command=self.compass_set).pack(anchor="w")
        grid = tk.Frame(box, bg=BG)
        grid.pack(anchor="w", pady=(2, 0))
        for r, (key, text, values) in enumerate((
                ("corner", "Corner", list(maps.CORNERS)),
                ("size_mm", "Size mm", [str(v) for v in range(
                    int(maps.COMPASS_MM[0]), int(maps.COMPASS_MM[1]) + 1,
                    2)]))):
            tk.Label(grid, text=text + ":", font=mono(), fg=FG,
                     bg=BG).grid(row=r, column=0, sticky="w", padx=(0, 6))
            pick = ttk.Combobox(grid, textvariable=self.compass_vars[key],
                                state="readonly", values=values,
                                width=13 if key == "corner" else 5)
            pick.grid(row=r, column=1, sticky="w", pady=1)
            pick.bind("<<ComboboxSelected>>", lambda e: self.compass_set())
        ttk.Checkbutton(box, text="On a white plate",
                        variable=self.compass_vars["plate"],
                        command=self.compass_set).pack(anchor="w",
                                                       pady=(2, 0))

        box = self.box(" Notes ")
        # Both wrap in the menu's width (the list of maps sets it) rather
        # than widening it: a Text is 80 letters wide unless told.
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 wraplength=px(MAPS_LIST_PX),
                 text="What the map is for, printed with it. Kept as you "
                      "type.").pack(anchor="w")
        self.notes = text_box(box, 10)
        self.notes.config(width=1)
        self.notes.insert("1.0", m.get("notes", ""))
        self.notes.edit_modified(False)
        self.notes.pack(fill="x", pady=(4, 0))
        self.notes.bind("<KeyRelease>", lambda e: self.save_notes_soon())
        self.notes.bind("<FocusOut>", lambda e: self.save_notes())

    def box(self, title):
        """A folding section of the left menu for the picked map; returns
        what to put in it."""
        title = title.strip()
        sec = kept_section(self.state, self.scl, self.options, title,
                           key=f"Maps: {title}")
        sec.pack(pady=(8, 0))
        return sec.body

    def show_info(self, m):
        """Where map `m` is, how big, and when it was made and changed."""
        if self.info is None:
            return
        try:
            self.info.config(text=(
                f"on {m['body']}, {self.scl.fmt_dist(m['width_m'])} x "
                f"{self.scl.fmt_dist(maps.height_m(m['width_m']))}\n"
                "made " + time.strftime("%Y-%m-%d %H:%M",
                                        time.localtime(m["created"]))
                + ", changed " + time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(m["updated"]))))
        except tk.TclError:             # the options were just rebuilt
            pass

    def keep(self, fields):
        """Set `fields` on the map picked, save and draw it again: only the
        first change since it was picked keeps a backup (scl.save_json)."""
        if self.mid is None:
            return
        m = self.scl.update_map(self.state, self.mid, fields,
                                backup=self.backed != self.mid)
        self.backed = self.mid
        if m is not None:
            self.shown = m["updated"]
            self.show_info(m)
            self.render_soon()

    def set_flag(self, flag):
        """Private or Share to Discord ticked on the map picked. A private
        map cannot be shared: its tick goes off and grey."""
        if self.mid is None:
            return
        on = self.flag_vars[flag].get()
        found, skipped = self.scl.flag_saved(self.state, "map", [self.mid],
                                             flag, on)
        flash_flagged(self.state, found, skipped, flag, on)
        m = self.current() or {}
        self.flag_vars["share"].set(bool(m.get("share")))
        self.share_box.state(["disabled" if m.get("private")
                              or m.get(discordshare.FROM) else "!disabled"])
        offer_unpost(self.nav, [("map", self.mid)], flag, on, self.frame)

    def compass_set(self):
        v = self.compass_vars
        self.keep({"compass": {"on": v["on"].get(),
                               "corner": maps.CORNERS[v["corner"].get()],
                               "size_mm": float(v["size_mm"].get()),
                               "plate": v["plate"].get()}})

    def legend_set(self):
        v = self.legend_vars
        self.keep({"legend": {"corner": maps.CORNERS[v["corner"].get()],
                              "w_mm": float(v["w_mm"].get()),
                              "h_mm": float(v["h_mm"].get())}})

    def also_ticked(self):
        self.keep({"switches": {name: var.get()
                                for name, var in self.also.items()}})

    def rename(self):
        m = self.current()
        name = " ".join(self.name_var.get().split())
        if m is None or not name or name == m["name"]:
            if m is not None:
                self.name_var.set(m["name"])
            return
        self.scl.update_map(self.state, m["id"], {"name": name})
        self.fill()

    def save_notes_soon(self):
        """Save once typing stops for NOTES_SAVE_MS."""
        if self.notes_job is not None:
            self.frame.after_cancel(self.notes_job)
        self.notes_job = self.frame.after(self.NOTES_SAVE_MS, self.save_notes)

    def save_notes(self):
        """Keep the notes shown, if they were changed."""
        if self.notes_job is not None:
            self.frame.after_cancel(self.notes_job)
            self.notes_job = None
        if self.mid is None or self.notes is None:
            return
        try:
            if not self.notes.edit_modified():
                return
            text = self.notes.get("1.0", "end-1c").rstrip()
        except tk.TclError:             # the box is already gone
            return
        m = self.scl.update_map(self.state, self.mid, {"notes": text},
                                backup=self.backed != self.mid)
        self.backed = self.mid
        self.notes.edit_modified(False)
        if m is not None:
            self.shown = m["updated"]
            self.show_info(m)

    # --- exporting ------------------------------------------------------

    def export_pdf(self):
        """Save every page of the map picked as one PDF, with a name or
        notes just typed. A file that cannot be written (open in a viewer,
        say) or a page that cannot be drawn says so."""
        self.rename()
        self.save_notes()
        st = self.state
        m = self.current()
        if m is None:
            return
        path = filedialog.asksaveasfilename(
            parent=self.frame, title="Export PDF", defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf")], initialfile=mapprint.pdf_name(m))
        if not path:
            return
        with st.lock:
            m = dict(m)
            data = self.gathered(m)
        data["system"] = self.nav.body_system(m["body"])
        try:
            count = mapprint.save_pdf(m, data, path)
        except Exception as exc:
            st.log(f"map {m['name']} not exported: "
                   + traceback.format_exc().replace("\n", " | "))
            show_message("Export PDF", f"{m['name']} was not exported:\n{exc}",
                         parent=self.frame)
            return
        with st.lock:
            st.flash(f"exported {m['name']}, {count} page"
                     + ("s" if count > 1 else "")
                     + f", to {os.path.basename(path)}")

    # --- deleting and restoring -----------------------------------------

    def delete(self):
        """Delete the map picked, once asked."""
        m = self.current()
        if m is None:
            return
        if not ask_yes_no("Delete map", f"Delete the map {m['name']} "
                          f"({m['body']})?\n\nIt can be put back from "
                          "Settings > Height map while it is still in the "
                          "backups.", parent=self.frame):
            return
        self.save_notes()
        gone = self.scl.delete_maps(self.state, [m["id"]])
        with self.state.lock:
            self.state.flash(f"deleted map {', '.join(gone)}")
        self.mid = None
        self.show_options(None)
        self.render_soon()
        self.fill()

    def restore(self, parent):
        """Put back maps deleted earlier, from the backups of maps.json,
        picked by name in ask_restore."""
        st, scl = self.state, self.scl
        with st.lock:
            have = [dict(m) for m in st.maps]
        found = scl.deleted_maps(have)
        if not found:
            show_message("Restore deleted maps",
                         "No deleted maps in the backups: the last "
                         f"{scl.BACKUP_KEEP} saves of maps.json hold none "
                         "that is gone now.", parent=parent)
            return
        rows = [(str(i), m.get("name", ""), m.get("body", ""),
                 time.strftime("%Y-%m-%d %H:%M",
                               time.localtime(m.get("created", 0))),
                 scl.fmt_dist(m.get("width_m", 0)), since)
                for i, (_, m, since) in enumerate(found)]
        keys = ask_restore(parent, "maps", "maps.json",
                           (("name", 180), ("body", 110), ("made", 165),
                            ("across", 110), ("gone since", 165)), rows)
        if not keys:
            return
        back = scl.restore_maps(st, [found[int(k)][1] for k in keys])
        with st.lock:
            st.flash(f"restored {len(back)} map"
                     + ("s" if len(back) != 1 else ""))
        self.fill()


class SettingsTab:
    """What is done to the program's data as a whole rather than to one
    waypoint, a tab per section: how the star map turns, what the height
    map's switches start as, importing a file of waypoints or reports
    someone shared, the SUB categories a waypoint can be given, and each
    kind of survey with the settings it has."""

    NO_ICON = "(plain pin)"
    SECTIONS = ("Star map", "Height map", "Waypoints", "Reports", "Survey",
                "Discord", "Backup", "Other")

    def __init__(self, parent, state, scl, nav, editor, set_hud, follow):
        self.state, self.scl, self.nav = state, scl, nav
        self.frame = tk.Frame(parent, bg=BG)
        self.seen = None
        # One section to a tab, as the main window's.
        self.sections = ttk.Notebook(self.frame)
        self.sections.pack(fill="both", expand=True)
        page = {}
        for name in self.SECTIONS:
            page[name] = tk.Frame(self.sections, bg=BG)
            self.sections.add(page[name], text=name)
        self.switch_box(page["Other"], set_hud, follow)
        self.reader_boxes(page["Other"])
        self.background_box(page["Other"])
        self.version_box(page["Other"])
        self.backup_page(page["Backup"])
        self.discord_page(page["Discord"])

        survey_page = scrolled(page["Survey"])
        self.restore_box(survey_page, "surveys",
                         "traces.json and heightmap.json",
                         lambda: nav.surveys.restore(self.frame),
                         row=0, column=0, columnspan=2)
        # Each kind of survey in a box, two to a row: what it does and
        # records, and what can be set for it (in use from its next start).
        for k, (title, text, settings) in enumerate(survey_kinds(scl)):
            box = RoundFrame(survey_page, text=f" {title} ",
                                font=mono(), fg=ACCENT, bg=BG, bd=2,
                                relief="groove", padx=10, pady=4)
            box.grid(row=k // 2 + 1, column=k % 2, sticky="nsew", padx=4,
                     pady=3)
            tk.Label(box, text=text, font=mono(9), fg=DIM, bg=BG,
                     justify="left", anchor="w", wraplength=px(540)).pack(
                anchor="w", fill="x")
            for key, label, choices in settings:
                row = tk.Frame(box, bg=BG)
                row.pack(anchor="w", pady=(4, 0))
                tk.Label(row, text=label + ":", font=mono(), fg=FG,
                         bg=BG).pack(side="left", padx=(0, 6))
                var = tk.StringVar(value=f"{state.cfg[key]:g}")
                pick = ttk.Combobox(row, textvariable=var, state="readonly",
                                    width=6, values=[f"{c:g}" for c in choices])
                pick.pack(side="left")
                pick.bind("<<ComboboxSelected>>",
                          lambda e, k=key, v=var: scl.save_setting(
                              state, k, float(v.get())))
        for col in (0, 1):
            survey_page.columnconfigure(col, weight=1, uniform="kind")

        box = RoundFrame(page["Star map"], text=" Turning ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w")
        tk.Label(row, text="Turning slows when zoomed in:", font=mono(),
                 fg=FG, bg=BG).pack(side="left", padx=(0, 6))
        slowdown = tk.StringVar(
            value=f"{state.cfg['starmap_zoom_slowdown']:g}")
        pick = ttk.Combobox(row, textvariable=slowdown, state="readonly",
                            width=5, values=("0", "0.25", "0.5", "0.75", "1",
                                             "1.5", "2"))
        pick.pack(side="left")
        pick.bind("<<ComboboxSelected>>", lambda e: scl.save_setting(
            state, "starmap_zoom_slowdown", float(slowdown.get())))
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="In a planet's or moon's view, a drag turns it less "
                      "the closer you are zoomed.\n0 turns the same at every "
                      "zoom; at 1 the ground under the mouse keeps pace with "
                      "it; above 1 slower still."
                 ).pack(anchor="w", pady=(6, 0))

        box = RoundFrame(page["Star map"], text=" Rings ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w")
        tk.Label(row, text="Thickness of the orbit rings:", font=mono(),
                 fg=FG, bg=BG).pack(side="left", padx=(0, 6))
        ring_width = tk.StringVar(value=state.cfg["starmap_ring_width"])
        pick = ttk.Combobox(row, textvariable=ring_width, state="readonly",
                            width=5, values=("1", "2", "3", "4", "5"))
        pick.pack(side="left")
        pick.bind("<<ComboboxSelected>>",
                  lambda e: self.save_ring_width(int(ring_width.get())))
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="In pixels. A change here is made on the star map at "
                      "once."
                 ).pack(anchor="w", pady=(6, 0))

        # Switches, layers and restoring maps: taller than some windows.
        height_page = scrolled(page["Height map"])
        box = RoundFrame(height_page, text=" Switches ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="What each switch starts as. A change here is made "
                      "on the height map at once too."
                 ).pack(anchor="w", pady=(0, 6))
        grid = tk.Frame(box, bg=BG)
        grid.pack(anchor="w")
        on = height_switch_starts(state.cfg)
        self.starts = {}
        half = (len(HEIGHT_SWITCHES) + 1) // 2
        for k, (name, text, _) in enumerate(HEIGHT_SWITCHES):
            self.starts[name] = tk.BooleanVar(value=on[name])
            ttk.Checkbutton(grid, text=text, variable=self.starts[name],
                            command=lambda n=name: self.save_starts(n)).grid(
                row=k % half, column=k // half, sticky="w", padx=(0, 24))
        ttk.Button(box, text="Reset to shipped",
                   command=self.reset_starts).pack(anchor="w", pady=(6, 0))

        box = RoundFrame(height_page, text=" Layers ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="Where each survey is drawn, level 3 at the bottom "
                      "to 6 on top, and how solid it is.\nUnder them the "
                      "grey of ground not measured, then the height map "
                      "(level 2);\nover them the waypoints. Lines are "
                      "always solid. A change here is made on the\nheight "
                      "map at once. An open map has its own (its Layers... "
                      "button)."
                 ).pack(anchor="w", pady=(0, 6))
        LayerTable(box, state.cfg.get("heightmap_layers"), self.save_layers)
        # nav.maps is only set once every tab exists: looked up on click.
        self.restore_box(height_page, "maps", "maps.json",
                         lambda: nav.maps.restore(self.frame))

        box = RoundFrame(page["Waypoints"], text=" Names ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="The colour of waypoint names on each map. A change "
                      "here is made on the map at once."
                 ).pack(anchor="w", pady=(0, 6))
        grid = tk.Frame(box, bg=BG)
        grid.pack(anchor="w")
        for row, (key, text, ground, tab) in enumerate((
                ("starmap_label_colour", "Star map", BG, "star"),
                ("heightmap_label_colour", "Height map", heightmap.GREY,
                 "height"))):
            tk.Label(grid, text=text + ":", font=mono(), fg=FG, bg=BG).grid(
                row=row, column=0, sticky="w", padx=(0, 6), pady=2)
            var = tk.StringVar(value=state.cfg.get(key) if state.cfg.get(key)
                               in LABEL_COLOURS else "white")
            pick = ttk.Combobox(grid, textvariable=var, state="readonly",
                                width=8, values=list(LABEL_COLOURS))
            pick.grid(row=row, column=1, sticky="w", padx=(0, 10))
            # A name as the map shows it, on that map's ground.
            sample = tk.Label(grid, text=" Shady Glen 1570 ", font=mono(9),
                              bg=ground, fg=LABEL_COLOURS[var.get()])
            sample.grid(row=row, column=2, sticky="w")
            pick.bind("<<ComboboxSelected>>",
                      lambda e, k=key, v=var, s=sample, t=tab:
                      self.save_label_colour(k, v.get(), s, t))

        box = RoundFrame(page["Waypoints"], text=" Import ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        ttk.Button(box, text="Import waypoints...",
                   command=lambda: editor.import_file(self.frame)).pack(
            anchor="w")
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="Adds the waypoints from a file someone exported for "
                      "you.\nOne within 1,000 m of one of yours asks first."
                 ).pack(anchor="w", pady=(6, 0))

        self.restore_box(page["Waypoints"], "waypoints", "waypoints.json",
                         lambda: editor.restore(self.frame))

        box = RoundFrame(page["Reports"], text=" Import ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        # nav.reports is only set once every tab exists: looked up on click.
        ttk.Button(box, text="Import reports...",
                   command=lambda: nav.reports.import_file(self.frame)).pack(
            anchor="w")
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="Adds the analysis reports from a file someone exported "
                      "for you.\nOne for a body you have a report for, or a "
                      "place within 20 km of one of yours, asks first."
                 ).pack(anchor="w", pady=(6, 0))

        self.restore_box(page["Reports"], "reports", "bodies.json",
                         lambda: nav.reports.restore(self.frame))

        box = RoundFrame(page["Waypoints"], text=" SUB categories ",
                            font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=10, pady=8)
        box.pack(anchor="nw", fill="both", expand=True, padx=4, pady=4)
        self.tree = ttk.Treeview(box, show="headings", selectmode="browse",
                                 columns=("name", "icon", "used"), height=14)
        for name, title, width in (("name", "SUB", 320), ("icon", "ICON", 260),
                                   ("used", "WAYPOINTS", 100)):
            self.tree.heading(name, text=title)
            set_column(self.tree, name, width, stretch=name == "name")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.edit())
        side = tk.Frame(box, bg=BG)
        side.pack(side="left", fill="y", padx=(10, 0))
        for text, command in (("Add...", self.add), ("Edit...", self.edit),
                              ("Delete...", self.delete),
                              ("Restore built-ins", self.restore)):
            ttk.Button(side, text=text, command=command).pack(fill="x",
                                                              pady=(0, 6))
        tk.Label(side, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text="What a waypoint is,\nand its icon on the maps.\n\n"
                      "A rename or delete\ncarries over to the\nwaypoints "
                      "with it.\n\nRestore built-ins adds\nback any you "
                      "deleted.").pack(anchor="w", pady=(6, 0))

    def backup_page(self, page):
        """A button and what it does, for backing up, restoring and wiping
        everything at once."""
        for title, label, command, text in (
                (" Back up ", "Back up everything...", self.back_up,
                 "All your waypoints, reports, height maps, surveys, maps and "
                 "settings, with their automatic backups, in one zip file in "
                 "a folder you choose."),
                (" Restore ", "Restore everything...", self.restore_all,
                 "Replaces all your data with a backup and restarts SC "
                 "Locator. The data you have now is kept in a safety copy "
                 "first."),
                (" Wipe ", "Wipe all data...", self.wipe_all,
                 "Deletes all your data, including the automatic backups, "
                 "and restarts SC Locator as new. Backups you saved "
                 "elsewhere are not touched.")):
            box = RoundFrame(page, text=title, font=mono(), fg=ACCENT,
                                bg=BG, bd=2, relief="groove", padx=10, pady=8)
            box.pack(anchor="nw", fill="x", padx=4, pady=4)
            ttk.Button(box, text=label, width=24, command=command).pack(
                side="left", anchor="n")
            tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                     text=text, wraplength=px(860)).pack(side="left",
                                                         padx=(12, 0))

    def discord_page(self, page):
        """Where the org's share channels are, the token Import reads them
        with, linking this account, and whose shares are let in. Saved to
        discord.json a moment after each change."""
        page = scrolled(page)
        self.discord_job, self.publishing = None, False
        # Held while discord.json's dict is changed or saved: a Publish
        # thread notes each post in it as it goes.
        self.discord_lock = threading.Lock()
        try:
            self.discord = discordshare.load_settings(
                self.scl.DISCORD_FILE, self.scl.DISCORD_ID_FILE)
        except ValueError as exc:
            self.discord = None         # never saved over: it holds the key
            tk.Label(page, font=mono(), fg=UPDATE_NOTICE, bg=BG,
                     justify="left", wraplength=px(860),
                     text=f"{exc}\n\nNothing here can be changed until it "
                          "is put right or deleted.").pack(anchor="w",
                                                          padx=4, pady=8)
            return
        cfg = self.discord

        def section(title, text):
            box = RoundFrame(page, text=f" {title} ", font=mono(),
                                fg=ACCENT, bg=BG, bd=2, relief="groove",
                                padx=10, pady=8)
            box.pack(anchor="nw", fill="x", padx=4, pady=4)
            tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                     text=text, wraplength=px(860)).pack(anchor="w",
                                                         pady=(0, 6))
            return box

        def entry(parent, var, **kw):
            var.trace_add("write", lambda *_: self.save_discord_soon())
            return ttk.Entry(parent, textvariable=var, font=mono(), width=90,
                             **kw)

        box = section("Channels", "A webhook for each share channel: in "
                      "Discord, the channel's settings > Integrations > "
                      "Webhooks > Copy Webhook URL. Traces and height maps "
                      "go to Surveys. Anyone with a webhook URL can post "
                      "with it, so keep them inside the org.")
        self.webhook_vars = {}
        for name in discordshare.CHANNELS:
            row = tk.Frame(box, bg=BG)
            row.pack(anchor="w", fill="x", pady=1)
            tk.Label(row, text=f"{name.capitalize()}:", font=mono(), fg=FG,
                     bg=BG, width=10, anchor="w").pack(side="left")
            var = tk.StringVar(value=cfg["webhooks"][name])
            self.webhook_vars[name] = var
            entry(row, var).pack(side="left", fill="x", expand=True)
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", fill="x", pady=(6, 1))
        tk.Label(row, text="Links:", font=mono(), fg=FG, bg=BG, width=10,
                 anchor="w").pack(side="left")
        self.link_channel_var = tk.StringVar(value=cfg["link_channel"])
        entry(row, self.link_channel_var).pack(side="left", fill="x",
                                               expand=True)
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 wraplength=px(860),
                 text="Links: the channel where members post their link "
                      "codes. In Discord, right-click the channel > Copy "
                      "Link (a webhook URL of that channel works too, but "
                      "it needs none). With it, the four share channels "
                      "can be closed to members' own messages."
                 ).pack(anchor="w", pady=(4, 0))

        box = section("Read token", "The token of a bot that only reads, "
                      "for Import: View Channel and Read Message History on "
                      "the share channels, and Message Content intent on in "
                      "the Developer Portal. It posts nothing and need not "
                      "be running. Not a member's own token: that is "
                      "against Discord's terms.")
        self.token_var = tk.StringVar(value=cfg["token"])
        entry(box, self.token_var, show="\u2022").pack(anchor="w", fill="x")

        box = section("Your account", "Link account makes your link code "
                      "and copies it. Post it as a message in the link "
                      "channel (Links, above), from your own Discord "
                      "account, and keep that message: what you share is taken in as "
                      "yours because it is signed with the key that code "
                      "belongs to. Deleting it unlinks you.")
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", fill="x")
        ttk.Button(row, text="Link account", width=16,
                   command=self.link_account).pack(side="left")
        self.link_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.link_var, font=mono(),
                  state="readonly", width=60).pack(side="left", fill="x",
                                                   expand=True, padx=(12, 0))
        if cfg["key"]:
            try:
                self.link_var.set(discordshare.link_code(cfg["key"]))
            except ImportError:
                pass

        lists = section("Who is let in", "Discord user ids, one to a line "
                        "(Discord: Settings > Advanced > Developer Mode, "
                        "then right-click a member > Copy User ID). "
                        "Anything after the id, such as a name, is only a "
                        "note. An empty allow list lets every linked member "
                        "in; the blacklist wins over it.")
        row = tk.Frame(lists, bg=BG)
        row.pack(anchor="w", fill="x")
        self.id_boxes = {}
        for key, title in (("allow", "Allow list"), ("block", "Blacklist")):
            col = tk.Frame(row, bg=BG)
            col.pack(side="left", fill="both", expand=True, padx=(0, 8))
            tk.Label(col, text=title, font=mono(), fg=FG, bg=BG).pack(
                anchor="w")
            box = text_box(col, 5)
            box.config(width=40, wrap="none")
            set_text(box, "\n".join(cfg[key]))
            box.pack(anchor="w", fill="x")
            box.bind("<KeyRelease>", lambda e: self.save_discord_soon())
            box.bind("<<Paste>>", lambda e: self.save_discord_soon())
            note = tk.Label(col, font=mono(9), fg=DIM, bg=BG, anchor="w")
            note.pack(anchor="w")
            self.id_boxes[key] = (box, note)
        self.show_id_notes()

        row = tk.Frame(page, bg=BG)
        row.pack(anchor="nw", fill="x", padx=4, pady=8)
        self.test_button = ttk.Button(row, text="Test connection", width=16,
                                      command=self.test_discord)
        self.test_button.pack(side="left")
        self.test_status = tk.Label(row, font=mono(9), fg=DIM, bg=BG)
        self.test_status.pack(side="left", padx=(12, 0))

        box = section("Share", "Publish new posts every item marked Share "
                      "to Discord that has not been posted yet, or has "
                      "changed since: a changed one goes again as an "
                      "update. Share all posts every one of them again. "
                      "Private items are never posted. Import... reads the "
                      "channels and shows what others shared, to take in "
                      "or skip.")
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", fill="x")
        self.publish_buttons = [
            ttk.Button(row, text="Publish new", width=16,
                       command=self.publish),
            ttk.Button(row, text="Share all...", width=16,
                       command=lambda: self.publish(everything=True)),
            ttk.Button(row, text="Import...", width=16,
                       command=self.import_discord)]
        for button in self.publish_buttons:
            button.pack(side="left", padx=(0, 8))
        self.publish_status = tk.Label(row, font=mono(9), fg=DIM, bg=BG)
        self.publish_status.pack(side="left", padx=(4, 0))

    def show_id_notes(self):
        """Under each list: how many ids it has, or which lines are not
        ids."""
        for box, note in self.id_boxes.values():
            lines = box.get("1.0", "end-1c").splitlines()
            bad = discordshare.id_problems(lines)
            if bad:
                note.config(fg=UPDATE_NOTICE, text="No user id at the start "
                            "of line " + ", ".join(map(str, bad)))
            else:
                count = len(discordshare.user_ids(lines))
                note.config(fg=DIM, text=f"{count} user id"
                            + ("" if count == 1 else "s"))

    def write_discord(self):
        """discord.json and discord-id.json as they are in memory. The
        caller holds discord_lock."""
        discordshare.save_settings(self.scl.DISCORD_FILE, self.discord,
                                   self.scl.DISCORD_ID_FILE)

    def unpost(self, items, parent):
        """Offer to take the posts of `items` [(kind, id)], which have just
        stopped being shared, off Discord; if yes, on a thread of its own,
        saying at the end what went and what did not."""
        cfg = self.discord
        if cfg is None:
            return
        with self.discord_lock:
            items = [(k, i) for k, i in items
                     if discordshare.posted_messages(cfg["posted"], k, i)]
        if not items:
            return
        some = (f"{len(items)} of them were" if len(items) > 1
                else "It was")
        if ask_buttons(
                "Remove from Discord",
                f"{some} posted to Discord. Remove the posts too?\n\n"
                "Members who already took them in keep their copies; "
                "nobody else can take them in once they are removed.",
                parent, ("Remove", "Leave them")) != "Remove":
            return
        scl, found, st = self.scl, {"step": (0, 0)}, self.state
        with st.lock:               # waypoints and maps are kept by id
            names = {("waypoint", w["id"]): w["name"] for w in st.waypoints}
            names.update({("map", m["id"]): m["name"] for m in st.maps})
        with self.discord_lock:
            posts = {(k, i): len(discordshare.posted_messages(
                cfg["posted"], k, i)) for k, i in items}

        def label(k, i):
            return f"{scl.SHARE_KIND_NAMES[k]} '{names.get((k, i), i)}'"

        # While it runs: a small window by the pointer saying how far it
        # has got; then the result in its place.
        win, pane = popup(parent, "Remove from Discord")
        busy = tk.Label(pane, font=mono(), fg=ACCENT, bg=BG, padx=20,
                        pady=14, text="Removing from Discord...")
        busy.pack()
        place_near(win)
        win.deiconify()

        def run():
            try:
                found["result"] = discordapi.unpost(
                    cfg, items, lock=self.discord_lock,
                    save=self.write_discord,
                    progress=lambda d, t: found.update(step=(d, t)))
            except Exception as exc:        # shown, not lost on a thread
                found["result"] = ([], [(k, i, f"stopped: {exc}")
                                        for k, i in items])

        def watch():
            if "result" not in found:
                done, total = found["step"]
                if total and win.winfo_exists():
                    busy.config(text="Removing from Discord: post "
                                f"{min(done + 1, total)} of {total}...")
                self.frame.after(200, watch)
                return
            if win.winfo_exists():
                win.destroy()
            removed, failed = found["result"]
            with st.lock:
                st.flash(f"removed {len(removed)} from Discord")
            out = []
            if removed:
                out.append("Deleted from Discord:")
                out += [f"  {label(k, i)}: {posts[(k, i)]} post"
                        + ("s" if posts[(k, i)] != 1 else "")
                        for k, i in removed]
                out += ["", "Members who took it in before keep their "
                        "copies."]
            if failed:
                out += ([""] if out else []) + ["Not removed:"]
                out += [f"  {label(k, i)}: {why}"
                        for k, i, why in failed[:30]]
            show_message("Remove from Discord", "\n".join(out), parent)

        threading.Thread(target=run, daemon=True).start()
        self.frame.after(200, watch)

    def save_discord_soon(self):
        """Save a moment after the typing stops, not at every key."""
        if self.discord_job:
            self.frame.after_cancel(self.discord_job)
        self.discord_job = self.frame.after(600, self.save_discord)

    def save_discord(self):
        """What the page shows, into discord.json. False if it could not be
        written."""
        self.discord_job = None
        cfg = self.discord
        self.show_id_notes()
        try:
            with self.discord_lock:
                cfg["webhooks"] = {n: v.get().strip()
                                   for n, v in self.webhook_vars.items()}
                cfg["token"] = self.token_var.get().strip()
                cfg["link_channel"] = self.link_channel_var.get().strip()
                for key, (box, _note) in self.id_boxes.items():
                    cfg[key] = [ln.rstrip() for ln in
                                box.get("1.0", "end-1c").splitlines()
                                if ln.strip()]
                self.write_discord()
        except OSError as exc:
            show_message("Not saved", "The Discord settings could not be "
                         f"saved:\n\n{exc}", self.frame.winfo_toplevel())
            return False
        return True

    def link_account(self):
        """Make the key if there is none yet, and copy its link code."""
        parent = self.frame.winfo_toplevel()
        cfg = self.discord
        try:
            if not cfg["key"]:
                cfg["key"] = discordshare.new_key()
                if not self.save_discord():
                    cfg["key"] = ""
                    return
            code = discordshare.link_code(cfg["key"])
        except ImportError:
            show_message("Link account", "Linking needs the cryptography "
                         "package, which is not installed:\n\n"
                         "    pip install cryptography", parent)
            return
        self.link_var.set(code)
        self.frame.clipboard_clear()
        self.frame.clipboard_append(code)
        show_message("Link account", f"Your link code is copied:\n\n{code}"
                     "\n\nPaste it as a message in the link channel, from "
                     "your own Discord account, and keep that message. "
                     "Test connection then shows whether it is found.",
                     parent)

    def test_discord(self):
        """Save, then try each webhook, the token and the channels on a
        thread of its own; what it finds is shown when it is done. Until
        then, beside the button: a turning mark, how much of it is done
        and the step it is on, so it is plain that something is happening
        and how far it has to go."""
        if self.discord_job:
            self.frame.after_cancel(self.discord_job)
        if not self.save_discord():
            return
        cfg = json.loads(json.dumps(self.discord))
        found = {"step": "Starting", "done": 0.0, "tick": 0}

        def run():
            try:
                found["lines"] = discordapi.test_connection(
                    cfg, progress=lambda text, done: found.update(
                        step=text, done=done))
            except ImportError:
                found["lines"] = ["Checking the link code needs the "
                                  "cryptography package: pip install "
                                  "cryptography"]
            except Exception as exc:        # shown, not lost on a thread
                found["lines"] = [f"The test failed: {exc}"]

        def done():
            if "lines" not in found:
                found["tick"] += 1
                mark = "|/-\\"[found["tick"] % 4]
                self.test_status.config(
                    fg=ACCENT, text=f"{mark}  {found['done']:4.0%}  "
                    f"{found['step']}...")
                self.frame.after(200, done)
                return
            self.test_button.config(state="normal")
            self.test_status.config(text="", fg=DIM)
            show_message("Test connection", "\n".join(found["lines"]),
                         self.frame.winfo_toplevel())

        self.test_button.config(state="disabled")
        self.test_status.config(fg=ACCENT, text="|    0%  Starting...")
        threading.Thread(target=run, daemon=True).start()
        self.frame.after(200, done)

    def import_discord(self):
        """Open the Import window, or bring the open one to the front."""
        if self.publishing or not self.save_discord():
            return
        if getattr(self, "importer", None) and \
                self.importer.win.winfo_exists():
            self.importer.win.lift()
            return
        self.importer = DiscordImport(self)

    def publish(self, everything=False):
        """Publish new, or Share all (`everything`): post on a thread of its
        own, with how far it has got beside the buttons, and say at the end
        what went and what did not."""
        parent = self.frame.winfo_toplevel()
        title = "Share all" if everything else "Publish new"
        if self.publishing or not self.save_discord():
            return
        scl, cfg = self.scl, self.discord
        if not cfg["key"]:
            show_message(title, "Link your account first (Link account, "
                         "above): every post is signed with its key.",
                         parent)
            return
        items = scl.shareable_items(self.state)
        with self.discord_lock:
            todo = discordshare.to_publish(items, cfg["posted"], everything)
        if not todo:
            show_message(title, "Nothing to post: no item is marked Share "
                         "to Discord." if not items else "Nothing to post: "
                         f"all {len(items)} items marked Share to Discord "
                         "went out as they are now.", parent)
            return
        if everything and ask_buttons(
                title, f"Post all {len(todo)} items marked Share to Discord "
                "again, unchanged ones too?", parent,
                ("Cancel", "Share all")) != "Share all":
            return
        labels = {(k, i): scl.share_label(k, i, it) for k, i, it in items}
        found = {"step": (0, len(todo))}

        def save():
            self.write_discord()

        def run():
            try:
                found["result"] = discordapi.publish(
                    cfg, items, scl.share_text, time.time(), everything,
                    progress=lambda d, t: found.update(step=(d, t)),
                    lock=self.discord_lock, save=save)
            except Exception as exc:        # shown, not lost on a thread
                found["result"] = ([], [(k, i, f"stopped: {exc}")
                                        for k, i, _it, _u in todo])

        def watch():
            if "result" not in found:
                done, total = found["step"]
                self.publish_status.config(
                    text=f"Posting {min(done + 1, total)} of {total}...")
                self.frame.after(250, watch)
                return
            self.publishing = False
            for button in self.publish_buttons:
                button.config(state="normal")
            self.publish_status.config(text="")
            sent, failed = found["result"]
            updates = sum(1 for _k, _i, u in sent if u)
            text = f"Posted {len(sent)}" + (
                f", {updates} of them as updates" if updates else "") + (
                ":" if sent else ".")
            shown = 40              # the message box is 30 lines high
            text += "".join(f"\n  {labels.get((k, i), i)}"
                            + (" (update)" if u else "")
                            for k, i, u in sent[:shown])
            if len(sent) > shown:
                text += f"\n  ... and {len(sent) - shown} more"
            if failed:
                text += f"\n\nNot posted: {len(failed)}"
                text += "".join(f"\n  {labels.get((k, i), i)}: {why}"
                                for k, i, why in failed[:30])
                if len(failed) > 30:
                    text += f"\n  ... and {len(failed) - 30} more"
            show_message(title, text, parent)

        self.publishing = True
        for button in self.publish_buttons:
            button.config(state="disabled")
        threading.Thread(target=run, daemon=True).start()
        self.frame.after(250, watch)

    def close_for_restart(self):
        """Close by the normal way out; the restart follows from there."""
        self.nav.updates.root.after_idle(self.nav.updates.root.destroy)

    def back_up(self):
        parent = self.frame.winfo_toplevel()
        path = filedialog.asksaveasfilename(
            parent=parent, title="Back up everything to",
            defaultextension=".zip", initialfile=databackup.default_name(),
            filetypes=[("Zip files", "*.zip")])
        if not path:
            return
        try:
            self.scl.back_up_all(self.state, path)
        except (OSError, databackup.BackupError) as exc:
            show_message("Backup failed", f"No backup was made.\n\n{exc}",
                         parent)
            return
        show_message("Backup made", f"Your data is backed up in:\n{path}",
                     parent)

    def restore_all(self):
        parent = self.frame.winfo_toplevel()
        path = filedialog.askopenfilename(
            parent=parent, title="Restore everything from",
            filetypes=[("Zip files", "*.zip")])
        if not path:
            return
        scl = self.scl
        try:
            info = databackup.verify(path, scl.data_names())
        except databackup.BackupError as exc:
            show_message("Not restored", f"Nothing was changed.\n\n{exc}",
                         parent)
            return
        count = info["counts"].get(scl.WAYPOINT_FILE.name, 0)
        if ask_buttons(
                "Restore everything",
                f"Backup made {info.get('created', 'at an unknown time')} by "
                f"version {info.get('version', '?')}, with {count} "
                "waypoints.\n\n"
                "This replaces ALL your data with that backup; anything "
                "added since it was made goes. What you have now is kept in "
                "bak\\before-restore-*.zip.\n\n"
                "SC Locator will close and start again.",
                parent, ("Cancel", "Restore")) != "Restore":
            return
        try:
            scl.request_restore(self.state, path)
        except (OSError, databackup.BackupError) as exc:
            show_message("Not restored", f"Nothing was changed.\n\n{exc}",
                         parent)
            return
        self.close_for_restart()

    def wipe_all(self):
        """Two questions, Cancel first in each so that Enter cannot do it."""
        parent = self.frame.winfo_toplevel()
        st = self.state
        with st.lock:
            what = (f"{len(st.waypoints)} waypoints, {len(st.bodies)} bodies' "
                    f"reports, {len(st.heightmap)} height maps, "
                    f"{len(st.traces)} traces and {len(st.maps)} maps")
        answer = ask_buttons(
            "Wipe all data",
            f"This deletes ALL your data: {what}, your settings and every "
            "automatic backup.\n\nBackups you saved elsewhere are not "
            "touched. Without one, this data is gone for good.",
            parent, ("Cancel", "Back up first...", "Continue"))
        if answer == "Back up first...":
            self.back_up()
            return
        if answer != "Continue":
            return
        if ask_buttons(
                "Are you sure?",
                "Are you absolutely sure? This cannot be undone.\n\n"
                "SC Locator will close, delete everything and start again "
                "as new.", parent,
                ("Cancel", "Yes, wipe everything")) != "Yes, wipe everything":
            return
        try:
            self.scl.request_wipe(st)
        except OSError as exc:
            show_message("Not wiped", f"Nothing was changed.\n\n{exc}", parent)
            return
        self.close_for_restart()

    def switch_box(self, page, set_hud, follow):
        """The HUD overlay and follow me: kept, in use at once."""
        scl, state = self.scl, self.state
        box = RoundFrame(page, text=" HUD and maps ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove", padx=10,
                            pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        hud = tk.BooleanVar(value=state.cfg["hud_on"])

        def switch_hud():
            scl.save_setting(state, "hud_on", hud.get())
            set_hud(hud.get())

        def switch_follow():
            scl.save_setting(state, "follow_me", follow.get())
            self.nav.star.recentre()
            self.nav.height.recentre()

        for var, text, command, hint in (
                (hud, "HUD overlay", switch_hud,
                 "The overlay over the game. Off for a second screen: "
                 "the Star map's left menu has all it does."),
                (follow, "follow me", switch_follow,
                 "The star map and the height map keep you in view.")):
            row = tk.Frame(box, bg=BG)
            row.pack(anchor="w", fill="x")
            ttk.Checkbutton(row, text=text, variable=var, width=14,
                            command=command).pack(side="left", anchor="n")
            tk.Label(row, font=mono(9), fg=DIM, bg=BG, justify="left",
                     text=hint).pack(side="left", padx=(12, 0))

    def reader_boxes(self, page):
        """Finding the HUD text on screen, and the diagnostic log."""
        scl = self.scl
        box = RoundFrame(page, text=" Find HUD text ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove", padx=10,
                            pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        self.find_button = ttk.Button(box, text="Find HUD text", width=24,
                                      command=self.find_hud)
        self.find_button.pack(side="left", anchor="n")
        col = tk.Frame(box, bg=BG)
        col.pack(side="left", fill="x", padx=(12, 0))
        tk.Label(col, font=mono(9), fg=DIM, bg=BG, justify="left",
                 wraplength=px(860),
                 text="Looks on every screen for the game's HUD text "
                      "(r_DisplayInfo 2) and reads it from there from then "
                      "on. Use it when the overlay never gets a fix, or after "
                      "changing screens or resolution. Press it, then switch "
                      f"to the game: it keeps looking for "
                      f"{scl.FIND_SECONDS} s.").pack(anchor="w")
        self.find_status = tk.Label(col, font=mono(9), fg=FG, bg=BG,
                                    justify="left", wraplength=px(860))
        self.find_status.pack(anchor="w", pady=(4, 0))
        self.show_reading_area()

        box = RoundFrame(page, text=" Diagnostic log ", font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove", padx=10,
                            pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        self.log_var = tk.BooleanVar(value=self.state.cfg["log"])
        ttk.Checkbutton(box, text="Write a diagnostic log",
                        variable=self.log_var, command=self.switch_log).pack(
            side="left", anchor="n")
        col = tk.Frame(box, bg=BG)
        col.pack(side="left", fill="x", padx=(12, 0))
        tk.Label(col, font=mono(9), fg=DIM, bg=BG, justify="left",
                 wraplength=px(860),
                 text="For a bug report: switch it on, make the problem "
                      "happen, and send session.log. It records what the "
                      f"screen reader sees and every error, in "
                      f"{scl.LOG_FILE}.").pack(anchor="w")
        ttk.Button(col, text="Open log folder", command=lambda: os.startfile(
            scl.LOG_FILE.parent)).pack(anchor="w", pady=(4, 0))

    def show_reading_area(self, found=None, colour=FG):
        """Under the button: where the HUD is read from now, and what the
        last Find saw, if anything."""
        cfg = self.state.cfg
        l, t, r, b = cfg["region"]
        text = (f"Reading from screen {cfg['monitor']}, "
                f"{l:.0%}-{r:.0%} across, {t:.0%}-{b:.0%} down.")
        self.find_status.config(text=(found + "\n" + text) if found else text,
                                fg=colour)

    def find_hud(self):
        """Look for the HUD text on a thread of its own, saying beside the
        button how far it has got; when found, read from there at once."""
        scl, st = self.scl, self.state
        cfg = dict(st.cfg)
        found = {"step": "starting"}

        def run():
            try:
                with scl.open_capture() as sct:
                    found["result"] = scl.find_hud(
                        sct, cfg, progress=lambda t: found.update(step=t))
            except Exception as exc:        # shown, not lost on a thread
                found["result"] = {"why": f"The search failed: {exc}"}

        def watch():
            if "result" not in found:
                self.find_status.config(
                    fg=ACCENT, text="Switch to the game now. Looking: "
                    f"{found['step']}...")
                self.frame.after(250, watch)
                return
            self.find_button.config(state="normal")
            res = found["result"]
            if "why" in res:
                self.show_reading_area(f"Not found. {res['why']}",
                                       UPDATE_NOTICE)
                st.log(f"find HUD text: not found: {res['why']}")
                return
            scl.save_setting(st, "monitor", res["monitor"])
            scl.save_setting(st, "region", res["region"])
            st.log(f"find HUD text: screen {res['monitor']}, region "
                   f"{res['region']}, read {res['read']}")
            with st.lock:
                st.flash(f"HUD text found on screen {res['monitor']}")
            self.show_reading_area(f"Found. A read there gave: {res['read']}")

        self.find_button.config(state="disabled")
        threading.Thread(target=run, daemon=True).start()
        self.frame.after(250, watch)

    def switch_log(self):
        on = self.log_var.get()
        self.scl.save_setting(self.state, "log", on)
        self.scl.set_log(self.state, on)

    def background_box(self, page):
        """The picture behind the main window: kept, in use at once."""
        scl, state = self.scl, self.state
        box = RoundFrame(page, text=" Background ", font=mono(), fg=ACCENT,
                            bg=BG, bd=2, relief="groove", padx=10, pady=8)
        box.pack(anchor="nw", fill="x", padx=4, pady=4)
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", fill="x")
        choice = tk.StringVar(value=state.cfg["background"])
        names = ttk.Combobox(row, textvariable=choice, width=32,
                             state="readonly")
        # Listed again each time it opens: pictures added to the folder
        # since show up.
        names.config(postcommand=lambda: names.config(values=[NO_PICTURE] + (
            background_names(scl.BACKGROUND_DIRS))))
        names.pack(side="left")

        def picked(e):
            scl.save_setting(state, "background", choice.get())
            self.nav.wallpaper.show(background_file(
                choice.get(), scl.BACKGROUND_DIRS, scl.DEFAULT_BACKGROUND))

        def open_folder():
            scl.BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(scl.BACKGROUND_DIR)

        names.bind("<<ComboboxSelected>>", picked)
        ttk.Button(row, text="Open folder", command=open_folder).pack(
            side="left", padx=(6, 0))
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text=f"Put your own pictures ({', '.join(PICTURE_TYPES)}) "
                      f"in\n{scl.BACKGROUND_DIR}").pack(anchor="w",
                                                        pady=(4, 0))

    def version_box(self, page):
        """Which version this is, any newer one, and the way to it."""
        updates = self.nav.updates
        box = RoundFrame(page, text=" Version ", font=mono(), fg=ACCENT,
                            bg=BG, bd=2, relief="groove", padx=10, pady=8)
        box.pack(anchor="nw", fill="both", expand=True, padx=4, pady=4)
        self.version_text = tk.Label(box, font=mono(), fg=FG, bg=BG,
                                     justify="left", anchor="w")
        self.version_text.pack(anchor="w", fill="x")
        row = tk.Frame(box, bg=BG)
        row.pack(anchor="w", pady=8)
        ttk.Button(row, text="Check for updates",
                   command=updates.check).pack(side="left")
        self.install_button = ttk.Button(row, text="Install update...",
                                         command=updates.offer)
        self.install_button.pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Release page",
                   command=lambda: webbrowser.open(updater.RELEASE_PAGE)
                   ).pack(side="left", padx=(6, 0))
        tk.Label(box, text="Latest release notes:", font=mono(9), fg=DIM,
                 bg=BG).pack(anchor="w")
        self.release_notes = text_box(box, 10, readonly=True)
        self.release_notes.pack(fill="both", expand=True)
        self.notes_shown = ""

    def show_version(self):
        updates = self.nav.updates
        info = updates.info or {}
        lines = [f"Installed: {updater.VERSION}"]
        if updates.available:
            lines.append(f"Available: {info['latest']} ({info['published']})")
        if updates.checking:
            lines.append("Checking...")
        elif info.get("error"):
            lines.append(f"Could not check: {info['error']}")
        elif updates.available:
            lines.append("A newer version is out.")
        elif info:
            lines.append("Up to date.")
        self.version_text.config(text="\n".join(lines))
        self.install_button.config(state="normal" if updates.available
                                   and updates.fraction is None else "disabled")
        notes = info.get("notes", "")
        if notes != self.notes_shown:   # setting it loses the scroll position
            set_text(self.release_notes, notes)
            self.notes_shown = notes

    def restore_box(self, page, what, file, command, **grid):
        """The Restore box for deleted `what` kept in backups of `file`:
        packed in `page`, or gridded there with `grid` given. `command`
        runs on click (the tabs it calls on exist only by then)."""
        box = RoundFrame(page, text=" Restore ", font=mono(), fg=ACCENT,
                            bg=BG, bd=2, relief="groove", padx=10, pady=8)
        if grid:
            box.grid(sticky="ew", padx=4, pady=4, **grid)
        else:
            box.pack(anchor="nw", fill="x", padx=4, pady=4)
        ttk.Button(box, text=f"Restore deleted {what}...",
                   command=command).pack(side="left", anchor="n")
        tk.Label(box, font=mono(9), fg=DIM, bg=BG, justify="left",
                 text=f"The {what} you deleted that are still in the backups "
                      f"of {file} (its last {self.scl.BACKUP_KEEP} saves), by "
                      "name, to put back as they were.",
                 wraplength=px(860)).pack(side="left", padx=(12, 0))

    def save_starts(self, name):
        """Keep how every switch starts, and set switch `name` on the height
        map now as well."""
        self.scl.save_setting(self.state, "heightmap_switches",
                              {n: v.get() for n, v in self.starts.items()})
        self.nav.height.set_switches({name: self.starts[name].get()})

    def reset_starts(self):
        """Every height map switch back to how it starts as shipped, here
        and on the height map."""
        self.scl.save_setting(self.state, "heightmap_switches", {})
        on = height_switch_starts(self.state.cfg)
        for name, value in on.items():
            self.starts[name].set(value)
        self.nav.height.set_switches(on)

    def save_label_colour(self, key, name, sample, tab):
        """Keep the colour of waypoint names on one map (`tab`: "star" or
        "height"), show it on the sample and redraw that map."""
        self.scl.save_setting(self.state, key, name)
        sample.config(fg=LABEL_COLOURS[name])
        getattr(self.nav, tab).draw()

    def save_ring_width(self, width):
        """Keep how thick the orbit rings are and redraw the star map."""
        self.scl.save_setting(self.state, "starmap_ring_width", width)
        self.nav.star.draw()

    def save_layers(self, chosen):
        """Keep the height map's own levels and opacities (`chosen`, as
        layer_choices gives them), and redraw it with them."""
        self.scl.save_setting(self.state, "heightmap_layers", chosen)
        self.nav.height.draw()

    def refresh(self):
        """The version box, and the SUB list, redrawn when it or the
        waypoints' use of it changes."""
        self.show_version()
        subs = subtypes.current()
        with self.state.lock:
            used = {}
            for w in self.state.waypoints:
                if w.get("sub"):
                    used[w["sub"]] = used.get(w["sub"], 0) + 1
        seen = (tuple(map(tuple, subs)), tuple(sorted(used.items())))
        if seen == self.seen:
            return
        self.seen = seen
        chosen = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        for name, icon in sorted(subs, key=lambda p: p[0].lower()):
            self.tree.insert("", "end", iid=name, values=(
                name, icon or self.NO_ICON, used.get(name, 0)))
        chosen = [n for n in chosen if self.tree.exists(n)]
        if chosen:
            self.tree.selection_set(chosen)

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def after_change(self, name=None):
        """Show a change here and on every tab; keep `name` selected."""
        self.refresh()
        if name and self.tree.exists(name):
            self.tree.selection_set(name)
            self.tree.see(name)
        self.nav.refresh_tabs()

    def ask(self, title, name="", icon=""):
        got = ask_sub(self.frame, title, name, icon,
                      list(subtypes.ICONS), self.NO_ICON)
        if got is None:
            return None
        return got[0], ("" if got[1] == self.NO_ICON else got[1])

    def add(self):
        got = self.ask("Add a SUB")
        if got is None:
            return
        try:
            self.scl.add_sub(self.state, *got)
        except ValueError as exc:
            show_message("Add a SUB", str(exc), parent=self.frame)
            return
        self.after_change(subtypes.current()[-1][0])

    def edit(self):
        old = self.selected()
        if old is None:
            return
        icon = next(i for n, i in subtypes.current() if n == old)
        got = self.ask("Edit a SUB", old, icon)
        if got is None:
            return
        at = [n for n, _ in subtypes.current()].index(old)
        try:
            self.scl.edit_sub(self.state, old, *got)
        except ValueError as exc:
            show_message("Edit a SUB", str(exc), parent=self.frame)
            return
        self.after_change(subtypes.current()[at][0])

    def delete(self):
        name = self.selected()
        if name is None:
            return
        n = self.scl.sub_users(self.state, name)
        if not ask_yes_no(
                "Delete a SUB", f"Delete the SUB '{name}'?" + (
                    f"\n\n{n} waypoint{'' if n == 1 else 's'} "
                    f"{'has' if n == 1 else 'have'} it, and will have no "
                    "SUB." if n else ""), parent=self.frame):
            return
        self.scl.delete_sub(self.state, name)
        self.after_change()

    def restore(self):
        self.scl.restore_subs(self.state)
        self.after_change()


def ask_sub(parent, title, name, icon, icons, no_icon):
    """A SUB's name and icon: a box to type the name in, the icons to pick
    from (`icons`, or `no_icon` for the plain pin) with a preview. Returns
    (name, icon) or None if cancelled (its x, CANCEL or Escape)."""
    win, pane = popup(parent, title)
    answer = {}
    tk.Label(pane, text="Name", font=mono(), fg=DIM, bg=BG).grid(
        row=0, column=0, sticky="w", padx=(10, 6), pady=(10, 3))
    name_var = tk.StringVar(value=name)
    entry = ttk.Entry(pane, textvariable=name_var, font=mono(), width=36)
    entry.grid(row=0, column=1, sticky="we", padx=(0, 10), pady=(10, 3))
    tk.Label(pane, text="Icon", font=mono(), fg=DIM, bg=BG).grid(
        row=1, column=0, sticky="w", padx=(10, 6), pady=3)
    icon_var = tk.StringVar(value=icon or no_icon)
    pick = ttk.Combobox(pane, textvariable=icon_var, state="readonly",
                        values=[no_icon] + list(icons), width=34)
    pick.grid(row=1, column=1, sticky="we", padx=(0, 10), pady=3)
    preview = tk.Canvas(pane, width=56, height=56, bg=PANEL,
                        highlightthickness=0)
    preview.grid(row=2, column=1, sticky="w", pady=3)

    def show(_e=None):
        preview.delete("all")
        chosen = icon_var.get()
        if chosen == no_icon:           # the plain pin, as the maps draw it
            preview.create_polygon(28, 44, 23, 34, 33, 34, fill=PIN)
            preview.create_oval(22, 16, 34, 28, fill=PIN, outline="")
            preview.create_oval(26, 20, 30, 24, fill=BG, outline="")
        else:
            draw_icon(preview, chosen, 28, 28, 22, icon=chosen)

    pick.bind("<<ComboboxSelected>>", show)
    show()
    row = tk.Frame(pane, bg=BG)
    row.grid(row=3, column=0, columnspan=2, sticky="e", padx=10, pady=10)

    def ok(_e=None):
        answer["got"] = (name_var.get(), icon_var.get())
        win.destroy()

    ttk.Button(row, text="OK", command=ok).pack(side="left")
    ttk.Button(row, text="CANCEL", command=win.destroy).pack(
        side="left", padx=(6, 0))
    entry.bind("<Return>", ok)
    win.bind("<Escape>", lambda e: win.destroy())
    place_near(win)
    win.deiconify()
    entry.focus_set()
    entry.select_range(0, "end")
    win.grab_set()
    win.wait_window()
    return answer.get("got")


def ask_conflict(parent, text, can_keep=True):
    """Yes, No or Replace for one clashing import, as ("yes" | "no" |
    "replace", apply_to_all). Closing the window is No. Without `can_keep`
    there is no Yes: both cannot be kept."""
    win, pane = popup(parent, "Import")
    answer = {"action": "no"}
    to_all = tk.BooleanVar(value=False)
    body = text_box(pane, min(30, text.count("\n") + 2))
    body.config(width=80, wrap="none")
    set_text(body, text)
    body.config(state="disabled")
    body.pack(fill="both", expand=True, padx=10, pady=(10, 6))
    ttk.Checkbutton(pane, text="apply to all remaining conflicts",
                    variable=to_all).pack(anchor="w", padx=10)
    row = tk.Frame(pane, bg=BG)
    row.pack(anchor="e", padx=10, pady=10)

    def choose(action):
        answer["action"] = action
        win.destroy()

    for label, action in (("YES", "yes"), ("NO", "no"),
                          ("REPLACE", "replace")):
        if action == "yes" and not can_keep:
            continue
        ttk.Button(row, text=label, command=lambda a=action: choose(a)).pack(
            side="left", padx=(6, 0))
    place_near(win)
    win.deiconify()
    win.grab_set()
    win.wait_window()
    return answer["action"], to_all.get()


# Import from Discord: what each group of sorted posts is called, and which
# can be taken in.
IMPORT_GROUPS = (("new", "New"), ("update", "Updates"),
                 ("clash", "Clash with yours"), ("skipped", "Skipped before"),
                 ("already", "Already have"), ("error", "Errors"),
                 ("blacklisted", "Blacklisted"),
                 ("not_allowed", "Not on the allow list"))
TAKEABLE = ("new", "update", "clash", "skipped")


class DiscordImport:
    """Import from Discord: the share channels read on a thread, their posts
    sorted (discordshare.sort_imports) into a list by group, to take in or
    skip, item by item or all of a group at once. Sorted again after each
    step, against our items as they are then."""

    def __init__(self, tab):
        self.tab, self.state, self.scl = tab, tab.state, tab.scl
        self.cfg, self.lock = tab.discord, tab.discord_lock
        self.rows, self.groups = {}, {}
        self.messages, self.files, self.problems = [], {}, []
        win, pane = popup(tab.frame, "Import from Discord")
        self.win = win
        self.status = tk.Label(pane, font=mono(9), fg=DIM, bg=BG, anchor="w",
                               text="Reading the channels...")
        self.status.pack(anchor="w", fill="x", padx=10, pady=(10, 4))
        frame = tk.Frame(pane, bg=BG)
        frame.pack(fill="both", expand=True, padx=10)
        cols = (("kind", "KIND", 110), ("from", "FROM", 140),
                ("when", "POSTED", 140), ("detail", "", 380))
        tree = self.tree = ttk.Treeview(frame, columns=[c for c, _, _ in cols],
                                        selectmode="extended", height=20)
        tree.heading("#0", text="NAME", anchor="w")
        set_column(tree, "#0", 300)
        for col, title, width in cols:
            tree.heading(col, text=title, anchor="w")
            set_column(tree, col, width)
        bar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=bar.set)
        tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        tree.bind("<Double-1>", lambda e: self.details())
        row = tk.Frame(pane, bg=BG)
        # Packed ahead of the list, so a short window shrinks the list,
        # not the buttons off the bottom.
        row.pack(side="bottom", anchor="e", padx=10, pady=10, before=frame)
        self.buttons = []
        for label, command in (("Take in", self.take), ("Skip", self.skip),
                               ("Details", self.details),
                               ("Close", win.destroy)):
            b = ttk.Button(row, text=label, command=command)
            b.pack(side="left", padx=(6, 0))
            self.buttons.append(b)
        for b in self.buttons[:3]:
            b.state(["disabled"])
        win.bind("<Escape>", lambda e: win.destroy())
        place_near(win)
        win.deiconify()
        self.read()

    def read(self):
        """Read the channels on a thread; sort when they are in."""
        with self.lock:
            cfg = json.loads(json.dumps(self.cfg))
        found = {"step": "Reading the channels..."}

        def run():
            try:
                found["got"] = discordapi.read_channels(
                    cfg, progress=lambda text: found.update(step=text))
            except Exception as exc:        # shown, not lost on a thread
                found["got"] = ([], {}, [f"Reading failed: {exc}"])

        def watch():
            if not self.win.winfo_exists():
                return
            if "got" not in found:
                self.status.config(text=found["step"])
                self.win.after(250, watch)
                return
            self.messages, self.files, problems = found["got"]
            self.problems = problems
            for b in self.buttons[:3]:
                b.state(["!disabled"])
            self.sort()

        threading.Thread(target=run, daemon=True).start()
        self.win.after(250, watch)

    def sort(self):
        """Sort the posts against our items as they are now, and list
        them."""
        scl, cfg = self.scl, self.cfg
        with self.lock:
            try:
                mine = discordshare.public_of(cfg["key"]) \
                    if cfg["key"] else None
            except ImportError:
                mine = None
            self.groups = discordshare.sort_imports(
                self.messages, self.files, mine=mine,
                allow=discordshare.user_ids(cfg["allow"]),
                block=discordshare.user_ids(cfg["block"]),
                imported=dict(cfg["imported"]),
                skipped=dict(cfg["skipped"]), checks=scl.DISCORD_CHECKS,
                clash=scl.discord_clash(self.state))
        tree = self.tree
        was_open = {g for g, _ in IMPORT_GROUPS
                    if tree.exists(g) and tree.item(g, "open")}
        tree.delete(*tree.get_children())
        self.rows = {}
        for group, title in IMPORT_GROUPS:
            entries = self.groups.get(group) or []
            if not entries:
                continue
            tree.insert("", "end", iid=group, text=f"{title} "
                        f"({len(entries)})", open=group in was_open or (
                            not was_open and group in TAKEABLE
                            and group != "skipped"))
            for n, e in enumerate(entries):
                iid = f"{group}:{n}"
                self.rows[iid] = (group, e)
                tree.insert(group, "end", iid=iid, text=self.label(e),
                            values=(scl.SHARE_KIND_NAMES.get(
                                        e.get("kind"), "?"),
                                    e.get("name", "?"), self.when(e),
                                    self.detail(group, e)))
        total = sum(len(v) for v in self.groups.values())
        text = (f"{total} shared item" + ("s" if total != 1 else "")
                + f" in {len(self.messages)} messages. Pick some and Take "
                "in or Skip; a group's row picks all of it.")
        if self.problems:
            text += "  Not read: " + "; ".join(self.problems)
        self.status.config(text=text)

    def label(self, e):
        if "item" not in e:
            return f"message {e['message']}"
        return self.scl.share_label(e["kind"], e["id"], e["item"])

    @staticmethod
    def when(e):
        at = e.get("at")
        if isinstance(at, (int, float)):
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
        return str(at or "")

    @staticmethod
    def detail(group, e):
        if e.get("problems"):
            return "; ".join(e["problems"])
        if e.get("clashes"):
            return "clashes with " + ", ".join(e["clashes"][:3])
        if e.get("ours") and group in ("update", "already"):
            return "yours already, as it came in before"
        return ""

    def picked(self):
        """The (group, entry) rows picked: a group's row stands for all of
        its rows."""
        out, seen = [], set()
        for iid in self.tree.selection():
            kids = self.tree.get_children(iid) if iid in self.groups else ()
            for one in (kids or (iid,)):
                if one in self.rows and one not in seen:
                    seen.add(one)
                    out.append(self.rows[one])
        return out

    def note(self, record, *args):
        """Note in discord.json what was done with a post."""
        with self.lock:
            record(self.cfg, *args)
            try:
                self.tab.write_discord()
            except OSError as exc:
                return str(exc)
        return None

    def take(self):
        """Take in the rows picked: a new one or an update straight away,
        one that clashes after asking (Yes as well, No skips it, Replace)."""
        scl, rows = self.scl, [r for r in self.picked() if r[0] in TAKEABLE]
        if not rows:
            show_message("Take in", "Pick something new, an update, a clash "
                         "or one skipped before.", self.win)
            return
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        taken, skipped, failed, always = 0, 0, [], None
        for group, e in rows:
            clashes = [] if e.get("ours") else scl.discord_clash(
                self.state)(e["kind"], e["id"], e["item"])
            action = "update" if e.get("ours") else "add"
            if clashes:
                can_keep = not (e["kind"] == "report"
                                and clashes[0] == e["id"])
                answer = always if always != "yes" or can_keep else None
                if answer is None:
                    answer, to_all = ask_conflict(
                        self.win, self.clash_text(e, clashes, can_keep),
                        can_keep)
                    if to_all:
                        always = answer
                if answer == "no":
                    self.note(discordshare.record_skipped, e)
                    skipped += 1
                    continue
                action = "replace" if answer == "replace" else "add"
            try:
                ours = scl.take_in(self.state, e, action, now)
            except ValueError as exc:
                failed.append(f"{self.label(e)}: {exc}")
                continue
            problem = self.note(discordshare.record_imported, e, ours,
                                time.time())
            if problem:
                failed.append(f"{self.label(e)}: taken in, but not noted "
                              f"as taken in: {problem}")
            taken += 1
        self.refresh_tabs()
        self.sort()
        text = f"Took in {taken}" + (f", skipped {skipped}" if skipped
                                     else "") + "."
        if failed:
            text += "\n\nNot taken in:\n" + "\n".join(
                f"  {line}" for line in failed[:30])
        show_message("Take in", text, self.win)

    def clash_text(self, e, clashes, can_keep):
        what = self.label(e)
        out = [f"{what}, shared by {e['name']}, clashes with yours: "
               f"{', '.join(clashes)}.", ""]
        if can_keep:
            out.append("YES takes it in as well (under a name of its own "
                       "if the name is taken).")
        out += ["NO leaves it out: skipped until it changes.",
                f"REPLACE puts it in place of {clashes[0]}."]
        return "\n".join(out)

    def skip(self):
        """Skip the rows picked: not offered again until they change."""
        rows = [r for r in self.picked() if r[0] in ("new", "update",
                                                     "clash")]
        for _group, e in rows:
            self.note(discordshare.record_skipped, e)
        self.sort()

    def details(self):
        """What a picked post holds, as its text would say it."""
        rows = self.picked()
        if not rows:
            return
        group, e = rows[0]
        if "item" not in e:
            text = "\n".join(e.get("problems") or ["(nothing)"])
        else:
            text = self.scl.share_text(e["kind"], e["id"], e["item"]).replace(
                "**", "")
            text += f"\n\nShared by {e.get('name', '?')} on {self.when(e)}"
            more = self.detail(group, e)
            if more:
                text += f"\n{more}"
        details_window(self.win, self.label(e), text)

    def refresh_tabs(self):
        """Every tab shows what came in now, not at the next tick."""
        nav = self.tab.nav
        nav.refresh_tabs()
        for tab in (nav.reports, nav.surveys, nav.maps):
            if tab is not None:
                tab.refresh()


def ask_restore(parent, what, file, columns, rows):
    """Restore deleted `what` (reports, waypoints, surveys) found in the
    backups of `file`: `rows` as (key, *values for `columns`) in a list to
    pick from, by the pointer. Returns the keys picked, or None if
    cancelled (its x, CANCEL or Escape)."""
    win, pane = popup(parent, f"Restore deleted {what}")
    answer = {}
    tk.Label(pane, font=mono(9), fg=DIM, bg=BG, justify="left",
             text=f"{what.capitalize()} found in the backups of {file} that "
                  "are gone now.\nPick one or more (Ctrl-click, "
                  "Shift-click) and RESTORE: each comes back as it was."
             ).pack(anchor="w", padx=10, pady=(10, 6))
    tree = ttk.Treeview(pane, show="headings", selectmode="extended",
                        columns=[n for n, _ in columns],
                        height=min(15, max(3, len(rows))))
    for name, width in columns:
        tree.heading(name, text=name.upper())
        tree.column(name, width=px(width), stretch=name == "name")
    for key, *values in rows:
        tree.insert("", "end", iid=key, values=values)
    tree.pack(fill="both", expand=True, padx=10)
    buttons = tk.Frame(pane, bg=BG)
    buttons.pack(anchor="e", padx=10, pady=10)

    def ok(_e=None):
        if tree.selection():
            answer["keys"] = list(tree.selection())
            win.destroy()

    ttk.Button(buttons, text="RESTORE", command=ok).pack(side="left")
    ttk.Button(buttons, text="CANCEL", command=win.destroy).pack(
        side="left", padx=(6, 0))
    tree.bind("<Double-1>", ok)
    win.bind("<Return>", ok)
    win.bind("<Escape>", lambda e: win.destroy())
    place_near(win)
    win.deiconify()
    tree.focus_set()
    win.grab_set()
    win.wait_window()
    return answer.get("keys")


AROUND_AUTO = "auto (by distance)"     # Shown around: the nearest body

# The flags every kind of item has (scl.FLAGS), as the menus name them.
FLAG_LABELS = (("private", "Private"), ("share", "Share to Discord"))
# A right-click menu's (turn on, turn off) for each flag. Words, not ticks:
# Windows draws a menu's tick over its text in our font and colours.
FLAG_ACTIONS = {"private": ("Make private", "Make not private"),
                "share": ("Share to Discord", "Stop sharing to Discord")}


def flag_menu(menu, recs, apply):
    """Private and Share to Discord in a right-click menu, for the items
    `recs` picked, by `apply(flag, on)`. Each way is offered when it would
    change one of them, so a mix offers both: Make private and Make not
    private, Share to Discord (the private ones and those from someone
    else left out) and Stop sharing. With nothing that can be shared, Share
    is there but greyed out."""
    for flag, _ in FLAG_LABELS:
        if flag == "share":
            ways = (any(not r.get("private") and not r.get("share")
                        and not r.get(discordshare.FROM) for r in recs),
                    any(r.get("share") for r in recs))
        else:
            ways = (any(not r.get(flag) for r in recs),
                    any(r.get(flag) for r in recs))
        for on, label in zip((True, False), FLAG_ACTIONS[flag]):
            if ways[not on]:
                menu.add_command(label=label, command=lambda f=flag, v=on:
                                 apply(f, v))
            elif on and flag == "share" and not ways[1]:
                menu.add_command(label=label, state="disabled")


def offer_unpost(nav, kind_keys, flag, on, parent):
    """After Stop sharing or Make private: offer to take what had been
    posted of those items off Discord too (SettingsTab.unpost)."""
    if (flag, on) in (("share", False), ("private", True)) and nav.settings:
        nav.settings.unpost(list(kind_keys), parent)


def flash_flagged(state, found, skipped, flag, on):
    """Say in the status line what a flag_menu click did."""
    n = found - skipped
    with state.lock:
        state.flash(f"{dict(FLAG_LABELS)[flag]} {'on' if on else 'off'}: "
                    f"{n} item" + ("s" if n != 1 else "")
                    + (f", {skipped} private or from others left out"
                       if skipped else ""))


def ask_waypoint(parent, w, names, around, no_sub, at=None):
    """Edit... for waypoint `w`: its name (typed, or one in `names`), SUB,
    QT, private, share to Discord, note and, if `around` is a list (deep space only), the body
    it is shown around, in a window by the screen point `at`. `no_sub` is the
    SUB list's "not said" (scl.NO_SUB). Returns the fields
    as scl.update_waypoint takes them, or None if cancelled (its x, CANCEL
    or Escape)."""
    win, pane = popup(parent, "Edit waypoint")
    answer = {}
    tk.Label(pane, text=f"{w['name']} ({w['planet']})", font=mono(), fg=DIM,
             bg=BG).grid(row=0, column=0, columnspan=2, sticky="w",
                         padx=10, pady=(10, 6))
    style = ttk.Style(pane)
    # Typed into, on the dark field: light text, as in ask_name.
    style.configure("Name.TCombobox", foreground=FG, insertcolor=FG)

    def label(row, text):
        tk.Label(pane, text=text, font=mono(), fg=DIM, bg=BG).grid(
            row=row, column=0, sticky="nw", padx=(10, 6), pady=3)

    label(1, "Name")
    name = tk.StringVar(value=w["name"])
    box = ttk.Combobox(pane, textvariable=name, values=names, width=32,
                       style="Name.TCombobox")
    box.grid(row=1, column=1, sticky="we", padx=(0, 10), pady=3)
    label(2, "SUB")
    sub = tk.StringVar(value=w.get("sub") or no_sub)
    ttk.Combobox(pane, textvariable=sub, state="readonly", width=32,
                 values=[no_sub] + subtypes.names()).grid(
        row=2, column=1, sticky="we", padx=(0, 10), pady=3)
    qt = tk.BooleanVar(value=bool(w.get("qt")))
    ttk.Checkbutton(pane, text="QT - can be jumped to", variable=qt).grid(
        row=3, column=1, sticky="w", pady=3)
    private = tk.BooleanVar(value=bool(w.get("private")))
    share = tk.BooleanVar(value=bool(w.get("share")))
    share_box = ttk.Checkbutton(pane, text="Share to Discord", variable=share)

    def private_ticked():
        """A private waypoint cannot be shared, nor one from someone
        else."""
        if private.get():
            share.set(False)
        share_box.state(["disabled" if private.get()
                         or w.get(discordshare.FROM) else "!disabled"])

    ttk.Checkbutton(pane, text="Private - never exported or shared",
                    variable=private, command=private_ticked).grid(
        row=4, column=1, sticky="w", pady=3)
    share_box.grid(row=5, column=1, sticky="w", pady=3)
    private_ticked()
    row = 6
    parent_var = None
    if around is not None:
        label(row, "Shown around")
        parent_var = tk.StringVar(value=w.get("parent", AROUND_AUTO))
        ttk.Combobox(pane, textvariable=parent_var, state="readonly", width=32,
                     values=[AROUND_AUTO, "none"] + list(around)).grid(
            row=row, column=1, sticky="we", padx=(0, 10), pady=3)
        row += 1
    label(row, "Note")
    note = text_box(pane, 4)
    note.config(width=34)
    set_text(note, w.get("note", ""))
    note.grid(row=row, column=1, sticky="we", padx=(0, 10), pady=3)
    buttons = tk.Frame(pane, bg=BG)
    buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", padx=10,
                 pady=10)

    def ok(_e=None):
        pick = parent_var.get() if parent_var is not None else AROUND_AUTO
        answer.update(name=name.get(),
                      sub="" if sub.get() == no_sub else sub.get(),
                      qt=qt.get(), note=note.get("1.0", "end"),
                      parent=None if pick == AROUND_AUTO else pick,
                      private=private.get(), share=share.get())
        win.destroy()

    ttk.Button(buttons, text="OK", command=ok).pack(side="left")
    ttk.Button(buttons, text="CANCEL", command=win.destroy).pack(
        side="left", padx=(6, 0))
    box.bind("<Return>", ok)
    win.bind("<Escape>", lambda e: win.destroy())
    place_near(win, at)
    win.deiconify()
    box.focus_set()
    box.select_range(0, "end")
    win.grab_set()
    win.wait_window()
    return answer or None


def ask_name(parent, title, heading, current, given, at=None,
             can_clear=True):
    """A building's or waypoint's name, from a box that can be typed in or
    picked from the names `given` before, under `heading` (what is being
    named), opened by the screen point `at` (the click) if given. Returns
    the name, "" to clear it (CLEAR, only if `can_clear`), or None if
    cancelled (its x, CANCEL or Escape)."""
    win, pane = popup(parent, title)
    answer = {"name": None}
    tk.Label(pane, text=heading, font=mono(), fg=DIM, bg=BG).pack(
        anchor="w", padx=10, pady=(10, 4))
    var = tk.StringVar(value=current)
    # Typed into, on the dark field: light text. TCombobox's dark text is
    # for the read-only boxes elsewhere.
    ttk.Style(pane).configure("Name.TCombobox", foreground=FG, insertcolor=FG)
    box = ttk.Combobox(pane, textvariable=var, values=given, width=28,
                       style="Name.TCombobox")
    box.pack(fill="x", padx=10)
    row = tk.Frame(pane, bg=BG)
    row.pack(anchor="e", padx=10, pady=10)

    def choose(value):
        answer["name"] = value
        win.destroy()

    ttk.Button(row, text="OK", command=lambda: choose(var.get().strip())).pack(
        side="left")
    if can_clear:
        ttk.Button(row, text="CLEAR", command=lambda: choose("")).pack(
            side="left", padx=(6, 0))
    ttk.Button(row, text="CANCEL", command=win.destroy).pack(
        side="left", padx=(6, 0))
    box.bind("<Return>", lambda e: choose(var.get().strip()))
    win.bind("<Escape>", lambda e: win.destroy())
    place_near(win, at)
    win.deiconify()
    box.focus_set()
    box.select_range(0, "end")
    win.grab_set()
    win.wait_window()
    return answer["name"]


# ----------------------------------------------------------------------------
# Report viewer
# ----------------------------------------------------------------------------


def place_title(rec, columns):
    """What to call an analysed place in one line, from its
    scl.report_columns: a body's name; a place in space's name and type, or
    its type and coordinates if unnamed."""
    name, kind, at = columns
    if rec.get("scope") != "space":
        return name
    return f"{name} ({kind})" if name else f"{kind}  [{at} Mm]"


class ReportViewer:
    def __init__(self, parent, state, scl, nav):
        self.state, self.scl, self.nav = state, scl, nav
        self.frame = tk.Frame(parent, bg=BG)
        self.seen, self.shown = None, None

        bar = tk.Frame(self.frame, bg=BG)
        bar.pack(side="top", fill="x", pady=(0, 6))
        tk.Label(bar, text="System", font=mono(), fg=DIM, bg=BG).pack(
            side="left", padx=(0, 6))
        self.sys_var = tk.StringVar(value="all")
        self.sys_pick = ttk.Combobox(bar, textvariable=self.sys_var, width=16,
                                     state="readonly", values=("all",))
        self.sys_pick.pack(side="left")
        self.sys_pick.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        # Import is in the Settings tab.

        # Short enough to leave the report its width: coordinates in Mm and
        # the date only; the report has both in full.
        columns = (("name", 170), ("type", 150), ("system", 70), ("kind", 80),
                   ("coordinates", 160), ("analysed", 100), ("private", 70),
                   ("discord", 70))
        self.tree = ttk.Treeview(self.frame, show="headings",
                                 selectmode="extended",
                                 columns=[n for n, _ in columns])
        for name, width in columns:
            self.tree.heading(name, text="COORDINATES (Mm)"
                              if name == "coordinates" else name.upper())
            set_column(self.tree, name, width, stretch=name == "name")
        sortable(self.tree, scl.sort_key)
        self.tree.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_selected())
        self.tree.bind("<Button-3>", self.popup)
        # Rebuilt on each right-click: the body menu, then Copy/Export.
        self.menu = PopMenu(self.tree, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground=PICK)

        right = tk.Frame(self.frame, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.report = text_box(right, 16, readonly=True)
        self.report.config(wrap="none")
        self.report.pack(fill="both", expand=True)
        tk.Label(right, text="Your notes (saved as you type)", font=mono(),
                 fg=DIM, bg=BG).pack(anchor="w", pady=(6, 0))
        self.notes = text_box(right, 8, readonly=True)
        self.notes.pack(fill="x")
        # The report the box holds the notes of, the save waiting for a
        # pause in typing, and the report whose notes have had their one
        # backup since it was picked.
        self.notes_key, self.notes_job, self.notes_backed = None, None, None
        self.notes.bind("<<Modified>>", self.notes_changed)
        self.notes.bind("<FocusOut>", lambda e: self.flush_notes())
        self.notes.bind("<Destroy>", lambda e: self.flush_notes())

    def selected(self):
        """The one selected place; None when none or several are."""
        sel = self.tree.selection()
        return sel[0] if len(sel) == 1 else None

    def chosen(self):
        """The selected (key, report) pairs, in list order."""
        _, bodies = snapshot(self.state)
        return [(k, bodies[k]) for k in self.tree.selection() if k in bodies]

    def title(self, key, rec):
        """place_title for one report, named from the waypoints now."""
        wps, _ = snapshot(self.state)
        return place_title(rec, self.scl.report_columns(key, rec, wps))

    def popup(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        self.menu.delete(0, "end")
        if len(self.tree.selection()) > 1:     # the menu's title
            self.menu.title = f"{len(self.tree.selection())} reports"
        key = self.selected()           # one body: its menu first
        _, bodies = snapshot(self.state)
        if (key is not None and (bodies.get(key) or {}).get("scope") != "space"
                and self.nav.body_menu(self.menu, key, "reports",
                                       (event.x_root, event.y_root))):
            self.menu.add_separator()
        self.menu.add_command(label="Copy", command=self.copy)
        self.menu.add_command(label="Export...", command=self.export)
        self.menu.add_command(label="Delete...", command=self.delete)
        self.menu.add_separator()
        flag_menu(self.menu, [r for _, r in self.chosen()], self.set_flag)
        self.menu.tk_popup(event.x_root, event.y_root)

    def set_flag(self, flag, on):
        """Private or Share to Discord on or off for the reports picked."""
        picked = self.tree.selection()
        found, skipped = self.scl.flag_saved(
            self.state, "report", picked, flag, on)
        flash_flagged(self.state, found, skipped, flag, on)
        self.refresh()
        offer_unpost(self.nav, [("report", k) for k in picked], flag, on,
                     self.frame)

    def to_share(self, title, verb):
        """([(key, report)], private ones left out) for Copy or Export, or
        None to stop, as WaypointEditor.to_share."""
        chosen = self.chosen()
        if len(chosen) == 1 and chosen[0][1].get("private"):
            if ask_buttons(title, f"'{self.title(*chosen[0])}' is a private "
                           f"report. {verb} it anyway?", self.frame,
                           ("OK", "Cancel")) != "OK":
                return None
            return chosen, 0
        kept = [(k, r) for k, r in chosen if not r.get("private")]
        return kept, len(chosen) - len(kept)

    def delete(self):
        """Delete the selected reports, once asked."""
        chosen = self.chosen()
        if not chosen:
            return
        names = "\n".join("  " + self.title(k, rec) for k, rec in chosen)
        what = ("this report" if len(chosen) == 1
                else f"these {len(chosen)} reports")
        if not ask_yes_no(
                "Delete reports",
                f"Delete {what}, with your notes?\n\n"
                f"{names}\n\nA planet or moon goes back to not analysed: off "
                "the star map until you ANALYSE it again. Waypoints stay.",
                parent=self.frame):
            return
        st, scl = self.state, self.scl
        with st.lock:
            gone = scl.delete_reports(st.bodies, [k for k, _ in chosen])
            scl.save_json(scl.BODIES_FILE, st.bodies)
            st.flash(f"deleted {len(gone)} report"
                     + ("s" if len(gone) != 1 else ""))
        self.refresh()
        self.show_selected()            # nothing selected now: notes cleared
        self.nav.refresh_tabs()

    def select(self, key):
        """Select a place's report and scroll to it, showing every system."""
        self.sys_var.set("all")
        self.refresh()
        if self.tree.exists(key):
            self.tree.selection_set(key)
            self.tree.see(key)
            self.show_selected()

    def lines_for(self, key):
        """The report lines for one place: live where you are now."""
        return self.scl.place_report_lines(self.state, key)

    def copy(self):
        got = self.to_share("Copy report", "Copy")
        if got is None:
            return
        chosen, left = got
        texts = [self.scl.report_text(self.title(k, rec), rec,
                                      self.lines_for(k) or [])
                 for k, rec in chosen]
        if not texts:
            if left:
                with self.state.lock:
                    self.state.flash("nothing copied: all private")
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append("\n\n".join(texts))
        with self.state.lock:
            self.state.flash(f"copied {len(texts)} report"
                             + ("s" if len(texts) > 1 else "")
                             + (f", {left} private left out" if left else ""))

    def export(self):
        got = self.to_share("Export report", "Export")
        if got is None:
            return
        chosen, left = got
        reports = dict(chosen)
        if not reports:
            if left:
                show_message("Export reports",
                             "Nothing to export: they are all private.",
                             parent=self.frame)
            return
        first = next(iter(reports))
        path = filedialog.asksaveasfilename(
            parent=self.frame, title="Export reports",
            defaultextension=".json", filetypes=[("Reports", "*.json")],
            initialfile=((reports[first].get("label") or first)
                         if len(reports) == 1 else "reports") + ".json")
        if not path:
            return
        try:
            self.scl.export_json(path, reports)
        except OSError as exc:
            show_message("Export reports", str(exc), parent=self.frame)
            return
        with self.state.lock:
            self.state.flash(f"exported {len(reports)} to "
                             f"{os.path.basename(path)}"
                             + (f", {left} private left out" if left else ""))

    def restore(self, parent):
        """Put back reports deleted earlier, from the backups of
        bodies.json, picked by name in ask_restore."""
        st, scl = self.state, self.scl
        wps, bodies = snapshot(st)
        found = scl.deleted_reports(bodies)
        if not found:
            show_message("Restore deleted reports",
                         "No deleted reports in the backups: the last "
                         f"{scl.BACKUP_KEEP} saves of bodies.json hold none "
                         "that is gone now.", parent=parent)
            return
        rows = []
        for key, rec, since in found:
            name, kind, _ = scl.report_columns(key, rec, wps)
            rows.append((key, name or "(no name)", kind,
                         rec.get("system", "?"), rec.get("analysed", ""),
                         since))
        keys = ask_restore(parent, "reports", "bodies.json",
                           (("name", 190), ("type", 165), ("system", 70),
                            ("analysed", 165), ("gone since", 165)), rows)
        if not keys:
            return
        by_key = {k: rec for k, rec, _ in found}
        with st.lock:
            back = scl.restore_reports(st.bodies,
                                       [(k, by_key[k]) for k in keys])
            if back:
                scl.save_json(scl.BODIES_FILE, st.bodies)
            st.flash(f"restored {len(back)} report"
                     + ("s" if len(back) != 1 else ""))
        self.refresh()
        self.nav.refresh_tabs()

    def import_file(self, parent=None):
        """Import reports from a shared file, its dialogs over `parent`
        (the tab it is started from; the Settings tab)."""
        parent = parent or self.frame
        path = filedialog.askopenfilename(
            parent=parent, title="Import reports",
            filetypes=[("Reports", "*.json"), ("All files", "*.*")])
        if not path:
            return
        st, scl = self.state, self.scl
        try:
            good, bad = scl.read_report_file(path)
        except (OSError, ValueError) as exc:
            show_message("Import reports", str(exc), parent=parent)
            return
        now = time.strftime("%Y-%m-%d %H:%M")
        added, replaced, declined = [], [], []
        always = None                    # the answer ticked "apply to all"
        for key, rec in good:
            rec = scl.prepare_report_import(rec, now)
            _, bodies = snapshot(st)
            same, nearby = scl.report_conflicts(key, rec, bodies)
            replace = None
            if same is not None or nearby:
                can_keep = same is None
                action = always if always != "yes" or can_keep else None
                if action is None:
                    action, to_all = ask_conflict(
                        parent,
                        scl.report_conflict_text(key, rec, same, nearby),
                        can_keep)
                    if to_all:
                        always = action
                if action == "no":
                    declined.append(key)
                    continue
                if action == "replace":
                    replace = key if same is not None else nearby[0][1]
            with st.lock:
                try:
                    scl.import_report(st.bodies, key, rec, replace)
                except ValueError as exc:
                    bad.append((f"'{key}'", [str(exc)]))
                    continue
            (replaced if replace else added).append(key)
        with st.lock:
            if added or replaced:
                scl.save_json(scl.BODIES_FILE, st.bodies)
            st.flash(f"imported {len(added) + len(replaced)} reports")
        self.refresh()
        show_message("Import reports", scl.import_summary(
            os.path.basename(path), added, replaced, declined, bad),
            parent=parent)

    def refresh(self):
        wps, bodies = snapshot(self.state)
        systems = sorted({v.get("system", "?") for v in bodies.values()})
        if self.sys_var.get() not in systems:
            self.sys_var.set("all")
        pick = self.sys_var.get()
        # Names come from the waypoints: a rename there shows here.
        cols = {k: self.scl.report_columns(k, v, wps) for k, v in
                bodies.items() if pick == "all" or v.get("system", "?") == pick}
        seen = (tuple((k, v.get("analysed"), v.get("label"), v.get("private"),
                       v.get("share")) for k, v in bodies.items()),
                pick, tuple(cols.items()))
        if seen != self.seen:
            self.seen = seen
            self.sys_pick.config(values=["all"] + systems)
            chosen = self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            # By system, bodies first; places in space by type, then name.
            for key in sorted(cols, key=lambda k: (
                    bodies[k].get("system", ""),
                    bodies[k].get("scope") == "space",
                    cols[k][1] if bodies[k].get("scope") == "space" else "",
                    cols[k][0].lower(), k)):
                rec, (name, kind, at) = bodies[key], cols[key]
                self.tree.insert("", "end", iid=key, values=(
                    name, kind, rec.get("system", "?"), rec.get("kind", "?"),
                    at, rec.get("analysed", "")[:10],
                    "yes" if rec.get("private") else "no",
                    "yes" if rec.get("share") else "no"))
            sort_rows(self.tree)
            chosen = [k for k in chosen if self.tree.exists(k)]
            if chosen:
                self.tree.selection_set(chosen)
        self.show_report()          # every tick: the sun figures are live

    def show_report(self):
        many = len(self.tree.selection())
        lines = self.lines_for(self.selected())
        if lines is None:
            lines = [f"{many} places selected" if many > 1
                     else "select a place on the left"]
        set_text(self.report, "\n".join(lines))

    def show_selected(self):
        """The selected report's notes in the box: typed into with one
        selected, closed with none or several. Notes still waiting to be
        saved go to the report they were typed for first."""
        self.flush_notes()
        key = self.selected()
        _, bodies = snapshot(self.state)
        self.notes_key, self.notes_backed = key, None
        self.notes.config(state="normal" if key in bodies else "disabled")
        set_text(self.notes, (bodies.get(key) or {}).get("notes", ""))
        self.show_report()

    NOTES_SAVE_MS = 1000        # saved this long after typing stops

    def notes_changed(self, _e=None):
        """Typed into (or filled by show_selected): save after a pause."""
        if not self.notes.edit_modified():
            return
        self.notes.edit_modified(False)     # or the next change is not told
        if self.notes_job is not None:
            self.frame.after_cancel(self.notes_job)
        self.notes_job = self.frame.after(self.NOTES_SAVE_MS, self.save_notes)

    def flush_notes(self):
        """Save now any notes still waiting for the pause."""
        if self.notes_job is not None:
            try:
                self.frame.after_cancel(self.notes_job)
            except tk.TclError:             # closing: save all the same
                pass
            self.save_notes()

    def save_notes(self):
        """Save the box into its report's notes, if they changed. Only the
        first save since the report was picked keeps a backup: see
        scl.save_json."""
        self.notes_job = None
        key, st, scl = self.notes_key, self.state, self.scl
        try:
            text = self.notes.get("1.0", "end")
        except tk.TclError:                 # the window is already gone
            return
        with st.lock:
            rec = st.bodies.get(key)
            if rec is None or rec.get("notes", "") == text.strip():
                return                      # deleted, or nothing new
            scl.set_report_notes(st.bodies, key, text)
            scl.save_json(scl.BODIES_FILE, st.bodies,
                          backup=self.notes_backed != key)
            self.notes_backed = key
            wps = list(st.waypoints)
        st_title = place_title(rec, scl.report_columns(key, rec, wps))
        with st.lock:
            st.flash(f"notes saved on {st_title}")
