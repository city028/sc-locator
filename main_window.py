"""The main window: HUD switch, star map, waypoint editor and report viewer.

A normal window with a taskbar entry, next to the overlay and sharing its Tk
root and its State. It never imports sc_locator: the running module is handed
in as `scl`, because a second import of that file would carry its own file
paths and its own copy of the data, and could save over the real files.

The handlers here stay thin: the edits are sc_locator's helpers, the map
geometry is starmap's.
"""
import ctypes
import math
import os
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

import heightmap
import starmap
import subtypes
import survey
import traces

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
# Lakes: light blue water and a darker blue shore line, as on a paper map.
WATER, SHORE = (169, 212, 239), (42, 122, 184)
# Buildings: tan with a dark outline, as on a large-scale paper map.
BUILDING, BUILDING_EDGE = "#F2C49B", "#3A2A1E"

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
    style.configure("TNotebook", background=BG)
    style.configure("TNotebook.Tab", background=PANEL, foreground=DIM,
                    padding=(14, 4))
    style.map("TNotebook.Tab", background=[("selected", BG)],
              foreground=[("selected", ACCENT)])
    style.configure("Treeview", background=PANEL, foreground=FG,
                    fieldbackground=PANEL, font=mono(), rowheight=ROW_PX)
    style.map("Treeview", background=[("selected", "#1F3A52")])
    style.configure("Treeview.Heading", background=BG, foreground=DIM)
    style.configure("TButton", background=PANEL, foreground=ACCENT)
    style.configure("TCheckbutton", background=BG, foreground=ACCENT)
    style.configure("TCombobox", foreground=BG)
    fit_to_screen(win, style)

    bar = tk.Frame(win, bg=BG)
    bar.pack(fill="x", padx=8, pady=(6, 0))
    hud_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(bar, text="HUD overlay", variable=hud_var,
                    command=lambda: set_hud(hud_var.get())).pack(side="left")
    # One switch for both maps: each keeps you in view while it is on.
    follow = tk.BooleanVar(value=True)
    ttk.Checkbutton(bar, text="follow me", variable=follow,
                    command=lambda: [tab.recentre() for tab in tabs[:2]]).pack(
        side="left", padx=(12, 0))
    status = tk.Label(bar, text="", font=mono(), fg=DIM, bg=BG, anchor="e")
    status.pack(side="right", fill="x", expand=True)

    nb = ttk.Notebook(win)
    nb.pack(fill="both", expand=True, padx=8, pady=8)
    nav = Navigator(nb, state, scl)
    editor = WaypointEditor(nb, state, scl, nav)
    star = StarMap(nb, state, scl, nav, follow)
    height = HeightMapTab(nb, state, scl, follow, nav)
    reports = ReportViewer(nb, state, scl, nav)
    surveys = SurveysTab(nb, state, scl, nav)
    nav.star, nav.height, nav.editor, nav.reports = star, height, editor, reports
    nav.surveys = surveys
    settings = SettingsTab(nb, state, scl, nav, editor)
    tabs = [star, height, editor, reports, surveys, settings]
    for tab, title in zip(tabs, ("Star map", "Height map", "Waypoints",
                                 "Reports", "Surveys", "Settings")):
        nb.add(tab.frame, text=title)

    # Dragged to another screen: refit when it lands, not a second later.
    win.bind("<Configure>", lambda e: e.widget is win
             and fit_to_screen(win, style))

    def tick():
        fit_to_screen(win, style)   # the Windows scale changed under it
        for tab in tabs:
            tab.refresh()
        with state.lock:
            fresh = time.time() - state.note_time < 4
            status.config(text=state.note if fresh else "")
        win.after(1000, tick)

    tick()
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


def details_window(parent, title, text, at=None):
    """Show details: `text` in a window of its own by the pointer (`at`),
    the same for a waypoint, a body or a building. CLOSE or Escape."""
    win = tk.Toplevel(parent, bg=BG)
    win.withdraw()                  # placed before it is seen
    win.title(title)
    win.transient(parent.winfo_toplevel())
    lines = text.splitlines() or [""]
    box = text_box(win, min(30, len(lines) + 1), readonly=True)
    box.config(width=max(40, min(90, max(len(ln) for ln in lines) + 2)),
               wrap="none")
    set_text(box, text)
    box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
    ttk.Button(win, text="CLOSE", command=win.destroy).pack(pady=(0, 10))
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
    win = tk.Toplevel(parent, bg=BG)
    win.withdraw()                  # placed before it is seen
    win.title(title)
    win.transient(parent.winfo_toplevel())
    answer = {"got": None}
    lines = text.splitlines() or [""]
    box = text_box(win, min(30, len(lines)), readonly=True)
    box.config(width=max(40, min(90, max(len(ln) for ln in lines) + 2)),
               wrap="word")
    set_text(box, text)
    box.pack(fill="both", expand=True, padx=10, pady=(10, 6))
    row = tk.Frame(win, bg=BG)
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
        self.surveys = None

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

    def body_system(self, body):
        with self.state.lock:
            rec = self.state.bodies.get(body) or {}
        return self.scl.BODY_SYSTEM.get(body) or rec.get("system")

    def body_to_star(self, body):
        self.nb.select(self.star.frame)
        self.star.show(self.body_system(body), body)

    def body_to_height(self, body):
        self.nb.select(self.height.frame)
        self.height.body_var.set(body)
        self.height.open(body)

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
            t, traces.building_facts(xys, offset))
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

    def rename_building(self, name, label, at=None):
        with self.state.lock:
            given = self.scl.building_labels(self.state.traces)
        new = ask_name(self.nb, "Building name", name, label or "", given, at)
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
        # The ground only: not deep space, nor a station up in orbit.
        if (here != "height" and w.get("frame", "planet") == "planet"
                and not self.scl.in_orbit(w, self.state.cfg)):
            add(label="Show on height map", command=lambda: self.to_height(wid))
        if here != "list":
            add(label="Show in Waypoints", command=lambda: self.to_list(wid))
        add(label="Rename...", command=lambda: self.rename_waypoint(wid, at))
        add(label="Edit...", command=lambda: self.edit_waypoint(wid, at))
        add(label="Delete...", command=lambda: self.delete_waypoint(wid))
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
        add = menu.add_command
        add(label="Show details", command=lambda: self.building_details(name,
                                                                        at))
        add(label="Rename...", command=lambda: self.rename_building(name,
                                                                    label, at))
        return True


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
        self.frame = tk.Frame(parent, bg=BG)
        self.system, self.body = starmap.SYSTEMS[0], None
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

        side = tk.Frame(self.frame, bg=BG)
        side.pack(side="left", fill="y", padx=(0, 8))
        tk.Label(side, text="System", font=mono(), fg=DIM, bg=BG).pack(anchor="w")
        self.system_var = tk.StringVar(value=self.system)
        pick = ttk.Combobox(side, textvariable=self.system_var, state="readonly",
                            values=starmap.SYSTEMS, width=14)
        pick.pack(anchor="w", pady=(0, 8))
        pick.bind("<<ComboboxSelected>>",
                  lambda e: self.show(self.system_var.get(), None))
        # Survey markers would crowd the map: shown only when asked for, and
        # their switch only in a body's view, the system view has none.
        self.type_boxes = {}
        self.types = type_switches(side, ("poi",), self.draw, self.type_boxes)
        self.type_boxes["survey"].pack_forget()
        self.back = ttk.Button(side, text="< back to system",
                               command=lambda: self.show(self.system, None))
        self.back.pack(anchor="w", fill="x", pady=(8, 12))
        # One framed box, title, hint and list together, so it is plain which
        # list the words are about.
        self.box = box = tk.LabelFrame(side, font=mono(),
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=6, pady=6)
        box.pack(anchor="w", fill="both", expand=True)
        self.unplaced_hint = tk.Label(box, font=mono(9), fg=DIM, bg=BG,
                                      justify="left", anchor="w")
        self.unplaced_hint.pack(anchor="w", fill="x", pady=(0, 6))
        self.unplaced = tk.Listbox(box, font=mono(), bg=PANEL, fg=FG, width=22,
                                   height=12, relief="flat",
                                   selectbackground="#1F3A52")
        self.unplaced.pack(fill="both", expand=True)
        self.unplaced.bind("<<ListboxSelect>>", self.open_unplaced)
        self.unplaced.bind("<Button-3>", self.unplaced_menu)

        self.canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.canvas.bind("<Button-3>", self.context_menu)
        # Right-click on a pin or a body: the menus every tab shares.
        self.menu = tk.Menu(self.canvas, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground="#1d4f3a")
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
        # The system view's counterpart, in the same place.
        self.reset_btn = ttk.Button(self.canvas, text="default view",
                                    command=lambda: self.show(self.system,
                                                              None))
        self.reset_btn.place(relx=1.0, x=-10, y=10, anchor="ne")
        self.canvas.tag_bind("pin", "<Enter>",
                             lambda e: self.canvas.config(cursor="hand2"))
        self.canvas.tag_bind("pin", "<Leave>",
                             lambda e: self.canvas.config(cursor=""))

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
            self.reset_btn.place(relx=1.0, x=-10, y=10, anchor="ne")
            survey.pack_forget()
        else:
            self.reset_btn.place_forget()
            self.om_bar.place(relx=1.0, x=-10, y=10, anchor="ne")
            survey.pack(anchor="w", after=self.type_boxes["poi"])
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

    def open_unplaced(self, _event):
        sel = self.unplaced.curselection()
        if sel:
            self.show(self.system, self.unplaced.get(sel[0]))

    def on_press(self, e):
        self.press, self.dragged = (e.x, e.y), False

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
                self.nav.to_list(wid)
            for tag in self.canvas.gettags("current"):
                if tag.startswith("body:"):
                    self.show(self.system, tag[5:])
                    break
        self.press = None

    def context_menu(self, e):
        """Right-click: the waypoint menu on a pin, the body menu on a planet
        or moon -- or, in a body's own view, anywhere else on it."""
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
        self.zoom = max(0.2, min(200.0, self.zoom * 1.15 ** (e.delta / 120)))
        self.draw()

    def refresh(self):
        """Redraw only when the data changed: a save from the overlay, a
        rename, a new analysis."""
        wps, bodies = snapshot(self.state)
        me = self.where_am_i()
        if self.follow.get():
            self.follow_me(me)
        with self.state.lock:
            cells = heightmap.cell_count(self.state.heightmap, self.body)
        seen = (tuple((w["id"], w["name"], w["planet"]) for w in wps),
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
        c.delete("all")
        self.pins.clear()
        w, h = c.winfo_width(), c.winfo_height()
        if w < 50 or h < 50:
            return
        wps, bodies = snapshot(self.state)
        wps = of_types(wps, self.types)
        self.subs = {wp["id"]: wp.get("sub", "") for wp in wps}
        self.names = {wp["id"]: wp["name"] for wp in wps}
        self.me = self.where_am_i()
        objs = starmap.system_objects(bodies, wps, self.system)
        if self.body is None:
            self.fill_box(" Bodies not analysed yet ", objs["unplaced"],
                          "These have waypoints but\nno place on the map "
                          "until\nyou ANALYSE them.\nClick one to open it.",
                          "None in this system.")
            self.back.state(["disabled"])
            self.draw_system(objs, wps, w, h)
        else:
            around = starmap.surroundings(self.body, bodies, wps, self.system)
            self.fill_box(" Moons not analysed yet ", around["unplaced_moons"],
                          f"Moons of {self.body} with no\nplace yet: ANALYSE "
                          "them\nto show them here.\nClick one to open it.",
                          "None for this body.")
            self.back.state(["!disabled"])
            self.draw_body(wps, bodies, around, w, h)

    def fill_box(self, title, keys, hint, empty):
        """The side box: what is not on the view, and why."""
        self.box.config(text=title)
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
        any part of it opens the waypoint in the table."""
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
                                 anchor="w", fill=FG,
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

    def header(self, title, hint):
        c = self.canvas
        ids = (c.create_text(12, 10, text=title, anchor="nw", fill=ACCENT,
                             font=mono(16, True)),
               c.create_text(12, 10 + px(26), text=hint, anchor="nw",
                             fill=DIM, font=mono(9)))
        _, _, x1, y1 = c.bbox(*ids)
        self.backdrop("header", (0, 0, x1 + 8, y1 + 6), ids[0])

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
            c.create_line(*flat, fill=ORBIT, dash=(3, 4))

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
        self.header(self.system.upper(),
                    "drag to rotate  -  wheel to zoom  -  click a planet or "
                    "moon to open it, a pin to edit it")

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
                c.create_line(*flat, fill=ORBIT, dash=(3, 4))

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
        lines.append("drag to rotate  -  wheel to zoom")
        lines.append("click a pin to edit it, a moon to open it")
        self.header(f"{self.body.upper()}  ({where})", "\n".join(lines))
        if surveyed:
            self.legend(*self.mesh_tiles[1:], w, h)


# ----------------------------------------------------------------------------
# Height map
# ----------------------------------------------------------------------------


# What the height map can draw, bottom to top: relief, samples, height lines,
# survey pattern, water, railways, buildings; then the waypoints. Each as
# (switch, label, on as shipped).
HEIGHT_LAYERS = (("relief", "Filled relief", True),
                 ("samples", "Survey samples", False),
                 ("lines", "Height lines", True),
                 ("course", "Survey pattern", False),
                 ("lake", "Lakes, seas, rivers", True),
                 ("rail", "Railways", True),
                 ("building", "Buildings", True))
HEIGHT_SWITCHES = HEIGHT_LAYERS + (
    ("poi", "POI waypoints", True),
    ("survey", "Survey waypoints", False),
    ("poi_height", "POI heights", False))


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

    def __init__(self, parent, state, scl, follow, nav):
        self.state, self.scl, self.follow, self.nav = state, scl, follow, nav
        self.frame = tk.Frame(parent, bg=BG)
        self.body, self.centre = None, None
        self.offset = (0.0, 0.0)    # (east m, north m) dragged from you
        self.mpp = 100.0
        self.press, self.seen, self.image = None, None, None
        self.water = None       # the lakes' image, held as self.image is
        self.held = None    # as the star map's: set by Go to
        self.contours = None    # (key, frame, (half_w, half_h), lines): draw
        self.relief = None      # (key, frame, (half_w, half_h), relief_grid)
        self.job, self.settle = None, None      # see schedule
        self.building_items = {}    # outline item -> (trace name, label)
        self.pin_items = {}         # a waypoint's drawn items -> its id
        self.goto_ids = []          # the waypoint ids of Go to's lines

        side = tk.Frame(self.frame, bg=BG)
        side.pack(side="left", fill="y", padx=(0, 8))
        tk.Label(side, text="Body", font=mono(), fg=DIM, bg=BG).pack(anchor="w")
        self.body_var = tk.StringVar()
        self.pick = ttk.Combobox(side, textvariable=self.body_var, width=14,
                                 state="readonly")
        self.pick.pack(anchor="w", pady=(0, 8))
        self.pick.bind("<<ComboboxSelected>>",
                       lambda e: self.open(self.body_var.get()))
        tk.Label(side, text="Go to", font=mono(), fg=DIM, bg=BG).pack(anchor="w")
        self.goto_var = tk.StringVar()
        self.goto = ttk.Combobox(side, textvariable=self.goto_var, width=14,
                                 state="readonly")
        self.goto.pack(anchor="w", pady=(0, 8))
        self.goto.bind("<<ComboboxSelected>>", lambda e: self.go_to())
        self.info = tk.Label(side, font=mono(), fg=FG, bg=BG, justify="left",
                             anchor="w")
        self.info.pack(anchor="w", fill="x")
        self.legend = tk.Canvas(side, width=170, height=34, bg=BG,
                                highlightthickness=0)
        self.legend.pack(anchor="w", pady=(8, 4))
        self.legend_key = None      # what the legend's bar was drawn for
        on = height_switch_starts(state.cfg)
        self.show = {}
        for name, text, _ in HEIGHT_LAYERS:
            self.show[name] = tk.BooleanVar(value=on[name])
            ttk.Checkbutton(side, text=text, variable=self.show[name],
                            command=self.draw).pack(anchor="w")
        self.types = type_switches(side, [t for t in TYPE_LABELS if on[t]],
                                   self.draw)
        self.show["poi_height"] = tk.BooleanVar(value=on["poi_height"])
        ttk.Checkbutton(side, text="POI heights",
                        variable=self.show["poi_height"],
                        command=self.draw).pack(anchor="w")
        tk.Label(side, font=mono(9), fg=DIM, bg=BG, justify="left",
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
        # Right-click on a waypoint, a building or the ground: the menus
        # every tab shares.
        self.canvas.bind("<Button-3>", self.context_menu)
        self.menu = tk.Menu(self.canvas, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground="#1d4f3a")

    def open(self, body):
        """Show `body`, around the busiest area surveyed on it."""
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

    # The switch that shows each kind of trace.
    TRACE_SWITCH = {"railway": "rail", "lake": "lake", "sea": "lake",
                    "waterway": "lake", "building": "building"}

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
        self.show[self.TRACE_SWITCH.get(t["kind"], "rail")].set(True)
        mid = tuple(sum(p[i] for p in pts) / len(pts) for i in range(3))
        with self.state.lock:
            self.held = (self.state.system, self.state.planet)
        self.offset = (0.0, 0.0)
        self.centre = heightmap.local_frame(mid)[0]
        self.draw()

    def set_switches(self, on):
        """Set switches by name ({name: on}, as HEIGHT_SWITCHES names them)
        from Settings, and redraw as a click on them would."""
        for name, value in on.items():
            (self.types if name in self.types else self.show)[name].set(value)
        self.draw()

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
        builds = [self.building_items[i] for i in under
                  if i in self.building_items]
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
        i = self.ZOOMS.index(self.mpp) + (-1 if e.delta > 0 else 1)
        self.mpp = self.ZOOMS[max(0, min(len(self.ZOOMS) - 1, i))]
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
        on_it = self.follow.get() and self.held is None and here
        plan = st.survey_saved
        return (self.body, heightmap.cell_count(st.heightmap, self.body),
                tuple(st.pos) if on_it else None,
                plan and (plan["origin"], plan["leg"], plan["joined"]),
                tuple((len(t["points"]), t.get("label")) for t in st.traces
                      if t["planet"] == self.body),
                tuple(st.pos) if here else None,
                tuple((w["id"], w["name"], w.get("type"), w.get("x"),
                       w.get("y"))
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
            lakes = [(list(t["points"]), t.get("level")) for t in st.traces
                     if t["planet"] == self.body and t["kind"] in traces.WATER
                     and len(t["points"]) >= traces.LEAST[t["kind"]]]
            # Contours and relief are worked out for twice the map each way
            # and kept until the survey, a lake or the zoom changes or you
            # pan off them. Only what is switched on is worked out.
            key = (self.body, self.seen[1], self.mpp,
                   sum(len(p) for p, _ in lakes))

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
            rivers = [(list(t["points"]), t["width_m"]) for t in st.traces
                      if t["planet"] == self.body
                      and t["kind"] == "waterway" and len(t["points"]) >= 2]
            # One walked before offset_m was kept takes today's setting.
            buildings = [(list(t["points"]), t.get(
                "offset_m", st.cfg["building_wall_offset_m"]), t.get("label"),
                t["name"]) for t in st.traces if t["planet"] == self.body
                and t["kind"] == "building"
                and len(t["points"]) >= traces.LEAST["building"]]
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
        lake_xys = [(xys, level) for xys, level in (
            (self.on_map(points, frame, radius), level)
            for points, level in lakes) if xys] if frame else []
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
            small = ImageTk.PhotoImage(Image.fromarray(heightmap.relief_image(
                self.relief[3], shift, w, h, self.mpp, block, lo, hi)))
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
                    img.put(heightmap.height_colour(z, lo, hi),
                            to=(x0, y0, x1, y1))
        self.image = img                # Tk drops an image nothing holds
        c.create_image(0, 0, image=img, anchor="nw")
        if frame:
            self.draw_grid(frame, radius, w, h)
            if self.show["lines"].get():
                self.draw_contours(frame, radius, interval, w, h)
            if plan and self.show["course"].get():
                self.draw_course(plan, frame, radius, w, h)
            if self.show["lake"].get():
                self.draw_lakes(lake_xys, rivers, frame, radius, w, h)
            if self.show["rail"].get():
                self.draw_rails(rails, frame, radius, w, h)
            if self.show["building"].get():
                self.draw_buildings(buildings, frame, radius, w, h)
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
                    # A spot height, as BM 1570 on a paper map.
                    z = elev.get(wid)
                    items.append(c.create_text(
                        sx + gap, sy, anchor="w", fill=FG, font=mono(9),
                        text=name if z is None else f"{name} {z:.0f}"))
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
        self.scale_bar(w, h)
        self.draw_legend(lo, hi, bool(near))
        self.info.config(text=f"{self.seen[1]:,} cells surveyed\n"
                              f"{len(near):,} in view\n"
                              f"{2 * half_w / 1000:,.1f} x "
                              f"{2 * half_h / 1000:,.1f} km\n"
                              f"{self.mpp:g} m a pixel\n"
                              + (f"contour interval {interval:g} m"
                                 if rec else ""))

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
        if not cells or not lake_xys:
            return cells
        xs, ys = np.array([c[0] for c in cells]), np.array([c[1] for c in cells])
        wet = np.zeros(len(cells), bool)
        for xys, _ in lake_xys:
            wet |= traces.inside(xs, ys, xys)
        return [c for c, w in zip(cells, wet.tolist()) if not w]

    def draw_lakes(self, lake_xys, rivers, frame, radius, w, h):
        """Lakes as flat water: every lake painted into one mask as its fan
        of triangles (traces.fan), so lakes that overlap (one surveyed in
        parts) merge, and the shore line is the mask's edge. Drawn as an image over the relief and height lines;
        the LAT/LON grid is raised back over it. Waterways go into the same
        mask as lines width_m wide to scale (never under 2 px), so a river
        running into a lake joins it."""
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
        for xys, level in lake_xys:
            px = [(w / 2 + x / self.mpp, h / 2 - y / self.mpp) for x, y in xys]
            xs, ys = [p[0] for p in px], [p[1] for p in px]
            if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
                continue
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
        rgba[wet] = (*WATER, 255)
        rgba[wet & ~inner] = (*SHORE, 255)
        self.water = ImageTk.PhotoImage(Image.fromarray(rgba, "RGBA"))
        c.create_image(0, 0, image=self.water, anchor="nw")
        c.tag_raise("grid")
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

    def draw_buildings(self, buildings, frame, radius, w, h):
        """Buildings walked round, squared off (traces.square): tan with a
        dark outline, as on a large-scale paper map. A building's name is
        printed on it where it fits inside the walls; where it does not, it
        shows by the pointer while the pointer is over the building (names
        printed past the walls piled up and buried the map). Right-click
        one for its menu (see context_menu)."""
        c = self.canvas
        self.building_items = {}
        for points, offset, label, name in buildings:
            xys = self.on_map(points, frame, radius)
            if not xys:
                continue
            px = [(w / 2 + x / self.mpp, h / 2 - y / self.mpp)
                  for x, y in traces.square(xys, offset)]
            xs, ys = [p[0] for p in px], [p[1] for p in px]
            if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
                continue
            outline = c.create_polygon(*[v for p in px for v in p],
                                       fill=BUILDING, outline=BUILDING_EDGE,
                                       width=1)
            self.building_items[outline] = (name, label)
            if not label:
                continue
            t = c.create_text(sum(xs) / len(xs), sum(ys) / len(ys),
                              text=label, fill=BUILDING_EDGE, font=mono(8))
            x0, y0, x1, y1 = c.bbox(t)
            if all(traces.inside_ring(x, y, px)
                   for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))):
                continue
            c.delete(t)
            c.tag_bind(outline, "<Enter>",
                       lambda e, label=label: self.show_name(e, label))
            c.tag_bind(outline, "<Leave>", lambda e: c.delete("name"))

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
               ("private", 70), ("system", 80), ("body", 110),
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
        tk.Entry(bar, textvariable=self.search_var, font=mono(), bg=PANEL,
                 fg=FG, insertbackground=FG, relief="flat", width=24).pack(
            side="left")
        self.search_var.trace_add("write", lambda *_: self.refresh())

        self.tree = ttk.Treeview(self.frame, show="headings",
                                 selectmode="extended",
                                 columns=[n for n, _ in self.COLUMNS])
        for name, width in self.COLUMNS:
            self.tree.heading(name, text=name.upper())
            set_column(self.tree, name, width, stretch=name == "note")
        # Rebuilt on each right-click: the waypoint menu, then Copy/Export.
        self.menu = tk.Menu(self.tree, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground="#1d4f3a")
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
        wid = self.selected()           # one waypoint: its menu first
        if wid is not None and self.nav.waypoint_menu(
                self.menu, wid, "list", (event.x_root, event.y_root)):
            self.menu.add_separator()
        self.menu.add_command(label="Copy", command=self.copy)
        self.menu.add_command(label="Export...", command=self.export)
        self.menu.tk_popup(event.x_root, event.y_root)

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
                       w.get("private"))
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
               ("started", 165), ("size", 235))

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
        scroll = ttk.Scrollbar(self.frame, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Button-3>", self.popup)
        self.menu = tk.Menu(self.tree, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground="#1d4f3a")

    # Rows are keyed "trace:<name>" or "map:<body>".

    def refresh(self):
        with self.state.lock:
            ts = [(t["name"], t["kind"], t.get("planet"), t.get("label", ""),
                   t.get("started"), len(t["points"]))
                  for t in self.state.traces]
            maps = {b: len(r["cells"]) for b, r in self.state.heightmap.items()}
        seen = (tuple(ts), tuple(sorted(maps.items())))
        if seen == self.seen:
            return
        self.seen = seen
        with self.state.lock:
            lengths = {t["name"]: traces.length(t) for t in self.state.traces}
        rows = [(f"map:{b}", (b or "", 0, ""), ("height map", "",
                 "height map", b, "", f"{n:,} cells"))
                for b, n in maps.items()]
        for name, kind, body, label, started, n in ts:
            when = (time.strftime("%Y-%m-%d %H:%M", time.localtime(started))
                    if started else "")
            rows.append((f"trace:{name}", (body or "", 1, kind, name), (
                name, label, kind, body or "?", when,
                f"{n:,} points, {self.scl.fmt_dist(lengths.get(name, 0))}")))
        chosen = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        # By body, its height map first, then its traces by kind and name.
        for iid, _, values in sorted(rows, key=lambda r: r[1]):
            self.tree.insert("", "end", iid=iid, values=values)
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
            self.menu.add_separator()
        self.menu.add_command(label="Delete...", command=self.delete)
        self.menu.tk_popup(*at)

    def delete(self):
        """Delete the selected traces and height maps, once asked."""
        sel = self.tree.selection()
        if not sel:
            return
        lines = []
        for iid in sel:
            name, label, kind, body, _, size = self.tree.item(iid, "values")
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
    fast = scl.TRACE_MAX_SPEED
    squares = " or ".join(f"{s:g}" for s in survey.PATTERNS.values() if s)
    misread = (f"A fix that climbed or dropped over {scl.TRACE_MAX_CLIMB:g} "
               "m/s, or moved faster than {} m/s, is taken for a misread.")
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
        ("Railway (SURVEY > trace a railway)",
         "Fly or ride along a track; only where it runs matters, not its "
         "height.\nRecords: railway-NNN in traces.json, a point every "
         f"{traces.STEP_M:g} m or more, drawn as the railroad symbol. "
         + misread.format(f"{fast['railway']:g}"), []),
        ("Lake (SURVEY > survey a lake)",
         "Start low over the water, then follow the shore all the way round; "
         "END LAKE.\nRecords: lake-NNN: its level (your camera's height at "
         "the first fix, less the setting below) and the route; all between "
         "the start and the route is water. "
         + misread.format(f"{fast['lake']:g}"),
         [("water_below_cam_m", "Water below camera (m)",
           (0, 0.5, 1, 2, 3, 5, 10))]),
        ("Sea (SURVEY > survey a sea)",
         "A lake too big to skim slowly: the same level (the lake's Water "
         "below camera) and the same water, flown faster.\nRecords: sea-NNN. "
         + misread.format(f"{fast['sea']:g}"), []),
        ("Waterway (SURVEY > survey a waterway)",
         "A river or canal: fly along its middle from one end to the other; "
         "END TRACE.\nRecords: waterway-NNN, drawn as water this wide, to "
         "scale. Each keeps the width it started with (width_m in "
         "traces.json). " + misread.format(f"{fast['waterway']:g}"),
         [("waterway_width_m", "Width of a new one (m)",
           (5, 10, 20, 30, 50, 100, 200))]),
        ("Building (SURVEY > survey a building)",
         "Walk round it on foot in first person, close along the walls; END "
         "BUILDING, then name it.\nRecords: building-NNN, a point every "
         f"{traces.BUILDING_STEP_M:g} m, drawn squared off and pulled in by "
         "the offset (kept as offset_m). Faster than "
         f"{fast['building']:g} m/s (over at most "
         f"{scl.TRACE_MAX_GAP['building']:g} s) is a misread; "
         f"{scl.TRACE_LOST_DROPS} in a row is LOST TRACK.",
         [("building_wall_offset_m", "Wall offset of a new one (m)",
           (0, 0.25, 0.5, 0.75, 1, 1.5))]),
    ]


class SettingsTab:
    """What is done to the program's data as a whole rather than to one
    waypoint, a tab per section: how the star map turns, what the height
    map's switches start as, importing a file of waypoints or reports
    someone shared, the SUB categories a waypoint can be given, and each
    kind of survey with the settings it has."""

    NO_ICON = "(plain pin)"
    SECTIONS = ("Star map", "Height map", "Waypoints", "Reports", "Survey",
                "Other")

    def __init__(self, parent, state, scl, nav, editor):
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
        tk.Label(page["Other"], text="Nothing to set here yet.", font=mono(),
                 fg=DIM, bg=BG).pack(anchor="nw", padx=8, pady=8)

        survey_page = scrolled(page["Survey"])
        self.restore_box(survey_page, "surveys",
                         "traces.json and heightmap.json",
                         lambda: nav.surveys.restore(self.frame),
                         row=0, column=0, columnspan=2)
        # Each kind of survey in a box, two to a row: what it does and
        # records, and what can be set for it (in use from its next start).
        for k, (title, text, settings) in enumerate(survey_kinds(scl)):
            box = tk.LabelFrame(survey_page, text=f" {title} ",
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

        box = tk.LabelFrame(page["Star map"], text=" Turning ", font=mono(),
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

        box = tk.LabelFrame(page["Height map"], text=" Switches ", font=mono(),
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

        box = tk.LabelFrame(page["Waypoints"], text=" Import ", font=mono(),
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

        box = tk.LabelFrame(page["Reports"], text=" Import ", font=mono(),
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

        box = tk.LabelFrame(page["Waypoints"], text=" SUB categories ",
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

    def restore_box(self, page, what, file, command, **grid):
        """The Restore box for deleted `what` kept in backups of `file`:
        packed in `page`, or gridded there with `grid` given. `command`
        runs on click (the tabs it calls on exist only by then)."""
        box = tk.LabelFrame(page, text=" Restore ", font=mono(), fg=ACCENT,
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

    def refresh(self):
        """The SUB list, redrawn when it or the waypoints' use of it changes."""
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
    (name, icon) or None if cancelled (closing it, X or Escape)."""
    win = tk.Toplevel(parent, bg=BG)
    win.withdraw()
    win.title(title)
    win.transient(parent.winfo_toplevel())
    answer = {}
    tk.Label(win, text="Name", font=mono(), fg=DIM, bg=BG).grid(
        row=0, column=0, sticky="w", padx=(10, 6), pady=(10, 3))
    name_var = tk.StringVar(value=name)
    entry = tk.Entry(win, textvariable=name_var, font=mono(), bg=PANEL, fg=FG,
                     insertbackground=FG, relief="flat", width=36)
    entry.grid(row=0, column=1, sticky="we", padx=(0, 10), pady=(10, 3))
    tk.Label(win, text="Icon", font=mono(), fg=DIM, bg=BG).grid(
        row=1, column=0, sticky="w", padx=(10, 6), pady=3)
    icon_var = tk.StringVar(value=icon or no_icon)
    pick = ttk.Combobox(win, textvariable=icon_var, state="readonly",
                        values=[no_icon] + list(icons), width=34)
    pick.grid(row=1, column=1, sticky="we", padx=(0, 10), pady=3)
    preview = tk.Canvas(win, width=56, height=56, bg=PANEL,
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
    row = tk.Frame(win, bg=BG)
    row.grid(row=3, column=0, columnspan=2, sticky="e", padx=10, pady=10)

    def ok(_e=None):
        answer["got"] = (name_var.get(), icon_var.get())
        win.destroy()

    ttk.Button(row, text="OK", command=ok).pack(side="left")
    ttk.Button(row, text="X", width=3, command=win.destroy).pack(
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
    win = tk.Toplevel(parent, bg=BG)
    win.withdraw()                  # placed before it is seen
    win.title("Import")
    win.transient(parent.winfo_toplevel())
    answer = {"action": "no"}
    to_all = tk.BooleanVar(value=False)
    body = text_box(win, min(30, text.count("\n") + 2))
    body.config(width=80, wrap="none")
    set_text(body, text)
    body.config(state="disabled")
    body.pack(fill="both", expand=True, padx=10, pady=(10, 6))
    ttk.Checkbutton(win, text="apply to all remaining conflicts",
                    variable=to_all).pack(anchor="w", padx=10)
    row = tk.Frame(win, bg=BG)
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


def ask_restore(parent, what, file, columns, rows):
    """Restore deleted `what` (reports, waypoints, surveys) found in the
    backups of `file`: `rows` as (key, *values for `columns`) in a list to
    pick from, by the pointer. Returns the keys picked, or None if
    cancelled (X, closing it or Escape)."""
    win = tk.Toplevel(parent, bg=BG)
    win.withdraw()                  # placed before it is seen
    win.title(f"Restore deleted {what}")
    win.transient(parent.winfo_toplevel())
    answer = {}
    tk.Label(win, font=mono(9), fg=DIM, bg=BG, justify="left",
             text=f"{what.capitalize()} found in the backups of {file} that "
                  "are gone now.\nPick one or more (Ctrl-click, "
                  "Shift-click) and RESTORE: each comes back as it was."
             ).pack(anchor="w", padx=10, pady=(10, 6))
    tree = ttk.Treeview(win, show="headings", selectmode="extended",
                        columns=[n for n, _ in columns],
                        height=min(15, max(3, len(rows))))
    for name, width in columns:
        tree.heading(name, text=name.upper())
        tree.column(name, width=px(width), stretch=name == "name")
    for key, *values in rows:
        tree.insert("", "end", iid=key, values=values)
    tree.pack(fill="both", expand=True, padx=10)
    buttons = tk.Frame(win, bg=BG)
    buttons.pack(anchor="e", padx=10, pady=10)

    def ok(_e=None):
        if tree.selection():
            answer["keys"] = list(tree.selection())
            win.destroy()

    ttk.Button(buttons, text="RESTORE", command=ok).pack(side="left")
    ttk.Button(buttons, text="X", width=3, command=win.destroy).pack(
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


def ask_waypoint(parent, w, names, around, no_sub, at=None):
    """Edit... for waypoint `w`: its name (typed, or one in `names`), SUB,
    QT, private, note and, if `around` is a list (deep space only), the body
    it is shown around, in a window by the screen point `at`. `no_sub` is the
    SUB list's "not said" (scl.NO_SUB). Returns the fields
    as scl.update_waypoint takes them, or None if cancelled (closing it, X
    or Escape)."""
    win = tk.Toplevel(parent, bg=BG)
    win.withdraw()                  # placed before it is seen
    win.title("Edit waypoint")
    win.transient(parent.winfo_toplevel())
    answer = {}
    tk.Label(win, text=f"{w['name']} ({w['planet']})", font=mono(), fg=DIM,
             bg=BG).grid(row=0, column=0, columnspan=2, sticky="w",
                         padx=10, pady=(10, 6))
    style = ttk.Style(win)
    # Typed into, on the dark field: light text, as in ask_name.
    style.configure("Name.TCombobox", foreground=FG, insertcolor=FG)

    def label(row, text):
        tk.Label(win, text=text, font=mono(), fg=DIM, bg=BG).grid(
            row=row, column=0, sticky="nw", padx=(10, 6), pady=3)

    label(1, "Name")
    name = tk.StringVar(value=w["name"])
    box = ttk.Combobox(win, textvariable=name, values=names, width=32,
                       style="Name.TCombobox")
    box.grid(row=1, column=1, sticky="we", padx=(0, 10), pady=3)
    label(2, "SUB")
    sub = tk.StringVar(value=w.get("sub") or no_sub)
    ttk.Combobox(win, textvariable=sub, state="readonly", width=32,
                 values=[no_sub] + subtypes.names()).grid(
        row=2, column=1, sticky="we", padx=(0, 10), pady=3)
    qt = tk.BooleanVar(value=bool(w.get("qt")))
    ttk.Checkbutton(win, text="QT - can be jumped to", variable=qt).grid(
        row=3, column=1, sticky="w", pady=3)
    private = tk.BooleanVar(value=bool(w.get("private")))
    ttk.Checkbutton(win, text="Private - never exported",
                    variable=private).grid(row=4, column=1, sticky="w", pady=3)
    row = 5
    parent_var = None
    if around is not None:
        label(row, "Shown around")
        parent_var = tk.StringVar(value=w.get("parent", AROUND_AUTO))
        ttk.Combobox(win, textvariable=parent_var, state="readonly", width=32,
                     values=[AROUND_AUTO, "none"] + list(around)).grid(
            row=row, column=1, sticky="we", padx=(0, 10), pady=3)
        row += 1
    label(row, "Note")
    note = text_box(win, 4)
    note.config(width=34)
    set_text(note, w.get("note", ""))
    note.grid(row=row, column=1, sticky="we", padx=(0, 10), pady=3)
    buttons = tk.Frame(win, bg=BG)
    buttons.grid(row=row + 1, column=0, columnspan=2, sticky="e", padx=10,
                 pady=10)

    def ok(_e=None):
        pick = parent_var.get() if parent_var is not None else AROUND_AUTO
        answer.update(name=name.get(),
                      sub="" if sub.get() == no_sub else sub.get(),
                      qt=qt.get(), note=note.get("1.0", "end"),
                      parent=None if pick == AROUND_AUTO else pick,
                      private=private.get())
        win.destroy()

    ttk.Button(buttons, text="OK", command=ok).pack(side="left")
    ttk.Button(buttons, text="X", width=3, command=win.destroy).pack(
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
    cancelled (closing the window, X or Escape)."""
    win = tk.Toplevel(parent, bg=BG)
    win.withdraw()                  # placed before it is seen
    win.title(title)
    win.transient(parent.winfo_toplevel())
    answer = {"name": None}
    tk.Label(win, text=heading, font=mono(), fg=DIM, bg=BG).pack(
        anchor="w", padx=10, pady=(10, 4))
    var = tk.StringVar(value=current)
    # Typed into, on the dark field: light text. TCombobox's dark text is
    # for the read-only boxes elsewhere.
    ttk.Style(win).configure("Name.TCombobox", foreground=FG, insertcolor=FG)
    box = ttk.Combobox(win, textvariable=var, values=given, width=28,
                       style="Name.TCombobox")
    box.pack(fill="x", padx=10)
    row = tk.Frame(win, bg=BG)
    row.pack(anchor="e", padx=10, pady=10)

    def choose(value):
        answer["name"] = value
        win.destroy()

    ttk.Button(row, text="OK", command=lambda: choose(var.get().strip())).pack(
        side="left")
    if can_clear:
        ttk.Button(row, text="CLEAR", command=lambda: choose("")).pack(
            side="left", padx=(6, 0))
    ttk.Button(row, text="X", width=3, command=win.destroy).pack(
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
                   ("coordinates", 160), ("analysed", 100))
        self.tree = ttk.Treeview(self.frame, show="headings",
                                 selectmode="extended",
                                 columns=[n for n, _ in columns])
        for name, width in columns:
            self.tree.heading(name, text="COORDINATES (Mm)"
                              if name == "coordinates" else name.upper())
            set_column(self.tree, name, width, stretch=name == "name")
        self.tree.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_selected())
        self.tree.bind("<Button-3>", self.popup)
        # Rebuilt on each right-click: the body menu, then Copy/Export.
        self.menu = tk.Menu(self.tree, tearoff=False, font=mono(), bg=PANEL,
                            fg=FG, activebackground="#1d4f3a")

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
        key = self.selected()           # one body: its menu first
        _, bodies = snapshot(self.state)
        if (key is not None and (bodies.get(key) or {}).get("scope") != "space"
                and self.nav.body_menu(self.menu, key, "reports",
                                       (event.x_root, event.y_root))):
            self.menu.add_separator()
        self.menu.add_command(label="Copy", command=self.copy)
        self.menu.add_command(label="Export...", command=self.export)
        self.menu.add_command(label="Delete...", command=self.delete)
        self.menu.tk_popup(event.x_root, event.y_root)

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
        texts = [self.scl.report_text(self.title(k, rec), rec,
                                      self.lines_for(k) or [])
                 for k, rec in self.chosen()]
        if not texts:
            return
        self.frame.clipboard_clear()
        self.frame.clipboard_append("\n\n".join(texts))
        with self.state.lock:
            self.state.flash(f"copied {len(texts)} report"
                             + ("s" if len(texts) > 1 else ""))

    def export(self):
        reports = dict(self.chosen())
        if not reports:
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
                             f"{os.path.basename(path)}")

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
        seen = (tuple((k, v.get("analysed"), v.get("label")) for k, v in
                      bodies.items()), pick, tuple(cols.items()))
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
                    at, rec.get("analysed", "")[:10]))
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
