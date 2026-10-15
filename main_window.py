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
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

import heightmap
import starmap
import survey

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

SUN_PX, PLANET_PX, MOON_PX = 14, 9, 5
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
    def show_waypoint(name):
        """SHOW ON MAP in the table: open the star map on that waypoint."""
        nb.select(star.frame)
        star.show_waypoint(name)

    editor = WaypointEditor(nb, state, scl, show_waypoint)

    def pick_waypoint(name):
        """A pin clicked on the map: show that waypoint in the table."""
        nb.select(editor.frame)
        editor.select(name)

    star = StarMap(nb, state, scl, pick_waypoint, follow)
    tabs = [star, HeightMapTab(nb, state, scl, follow), editor,
            ReportViewer(nb, state, scl)]
    for tab, title in zip(tabs, ("Star map", "Height map", "Waypoints",
                                 "Reports")):
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

    def __init__(self, parent, state, scl, on_pick, follow):
        self.state, self.scl, self.on_pick = state, scl, on_pick
        self.follow = follow
        # canvas item -> waypoint name. Not a tag: names have spaces in them,
        # and a canvas tag cannot.
        self.pins = {}
        self.marked = None      # waypoint ringed by SHOW ON MAP
        self.backdrops, self.ramp = {}, None
        self.frame = tk.Frame(parent, bg=BG)
        self.system, self.body = starmap.SYSTEMS[0], None
        self.yaw, self.pitch, self.zoom = 0.0, math.radians(60), 1.0
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
        self.back = ttk.Button(side, text="< back to system",
                               command=lambda: self.show(self.system, None))
        self.back.pack(anchor="w", fill="x", pady=(0, 12))
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

        self.canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
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
        if body is None:
            self.om_bar.place_forget()
            self.reset_btn.place(relx=1.0, x=-10, y=10, anchor="ne")
        else:
            self.reset_btn.place_forget()
            self.om_bar.place(relx=1.0, x=-10, y=10, anchor="ne")
        self.pitch = math.radians(60 if body is None else 20)
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

    def show_waypoint(self, name):
        """Open the view a waypoint is on, turned so it faces you."""
        wps, bodies = snapshot(self.state)
        w = next((q for q in wps if q["name"] == name), None)
        where = starmap.waypoint_view(w, bodies) if w else None
        if where is None:
            with self.state.lock:
                self.state.flash(f"{name}: system unknown, not on the star map")
            return
        system, body, om = where
        self.show(system, body, name)
        if om is not None:
            self.view_om(*om)

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
        if self.follow.get() and self.held is None and self.om is not None:
            # Following: turned away from you, and still turning with you.
            self.turn = (self.turn[0] + dx * 0.01,
                         max(-math.pi, min(math.pi, self.turn[1] + dy * 0.01)))
        else:
            if self.dragged:
                self.om = None          # turned by hand: no longer held
            self.yaw += dx * 0.01
            self.pitch = max(-math.pi / 2,
                             min(math.pi / 2, self.pitch + dy * 0.01))
        self.press = (e.x, e.y)
        self.draw()

    def on_release(self, _e):
        if not self.dragged:
            current = self.canvas.find_withtag("current")
            name = self.pins.get(current[0]) if current else None
            if name is not None:
                self.on_pick(name)
            for tag in self.canvas.gettags("current"):
                if tag.startswith("body:"):
                    self.show(self.system, tag[5:])
                    break
        self.press = None

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
        seen = (tuple((w["name"], w["planet"]) for w in wps),
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
        self.me = self.where_am_i()
        objs = starmap.system_objects(bodies, wps, self.system)
        if self.body is None:
            self.fill_box(" Bodies not analysed yet ", objs["unplaced"],
                          "These have waypoints but\nno place on the map "
                          "until\nyou ANALYSE them.\nClick one to open it.",
                          "None in this system.")
            self.back.state(["disabled"])
            self.draw_system(objs, w, h)
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

    def pin(self, x, y, name, label=None):
        """A location marker standing on (x, y), with its name (or `label`)
        beside it, ringed if SHOW ON MAP picked it. Clicking any part of it
        opens the waypoint in the table."""
        c = self.canvas
        ids = []
        if name == self.marked:
            ids.append(c.create_oval(x - 15, y - 27, x + 15, y + 3,
                                     outline=ACCENT, width=2, tags="pin"))
        ids += [
            c.create_polygon(x, y, x - 5, y - 10, x + 5, y - 10, fill=PIN,
                             outline="", tags="pin"),
            c.create_oval(x - 6, y - 18, x + 6, y - 6, fill=PIN, outline="",
                          tags="pin"),
            c.create_oval(x - 2, y - 14, x + 2, y - 10, fill=BG, outline="",
                          tags="pin"),
            c.create_text(x + 9, y - 12, text=label or name, anchor="w",
                          fill=FG,
                          font=mono(9), tags=("pin", "label")),
        ]
        for item in ids:
            self.pins[item] = name

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

    def draw_system(self, objs, w, h):
        c, yaw, pitch = self.canvas, self.yaw, self.pitch
        cx, cy = w / 2, h / 2
        spots = [b["centre"] for b in objs["bodies"]] + \
            [p for _, p in objs["points"]]
        scale = starmap.fit_scale(spots, w, h) * self.zoom

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

        items = [(starmap.project(p, own, pitch, scale, cx, cy), "pin", n, n)
                 for n, p in pts]
        _, body, pos, _ = self.me
        if body == self.body:
            items.append((starmap.project(pos, own, pitch, scale, cx, cy),
                          "you", None, None))
        for kind, key, d, q in outer:
            far = self.scl.fmt_dist(math.sqrt(sum(v * v for v in d)))
            name = key.upper() if kind == "moon" else key
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


class HeightMapTab:
    """The surveyed ground around you, flat and at full detail: one 100 m
    cell to a pixel at the start, grey where nothing has been measured."""

    ZOOMS = (12.5, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0)   # metres a pixel
    RELIEF_BLOCK = 4        # px a side of the filled view's blocks
    SETTLE_MS = 250         # quiet this long after a drag or zoom: full draw

    def __init__(self, parent, state, scl, follow):
        self.state, self.scl, self.follow = state, scl, follow
        self.frame = tk.Frame(parent, bg=BG)
        self.body, self.centre = None, None
        self.offset = (0.0, 0.0)    # (east m, north m) dragged from you
        self.mpp = 100.0
        self.press, self.seen, self.image = None, None, None
        self.held = None    # as the star map's: set by Go to
        self.contours = None    # (key, frame, (half_w, half_h), lines): draw
        self.relief = None      # (key, frame, (half_w, half_h), relief_grid)
        self.job, self.settle = None, None      # see schedule

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
        # What to draw, bottom to top: relief, samples, height lines, survey
        # pattern, waypoints.
        self.show = {}
        for name, text, on in (("relief", "Filled relief", True),
                               ("samples", "Survey samples", False),
                               ("lines", "Height lines", True),
                               ("course", "Survey pattern", True),
                               ("waypoints", "Waypoints", True)):
            self.show[name] = tk.BooleanVar(value=on)
            ttk.Checkbutton(side, text=text, variable=self.show[name],
                            command=self.draw).pack(anchor="w")
        tk.Label(side, font=mono(9), fg=DIM, bg=BG, justify="left",
                 anchor="w",
                 text="\nSURVEY in the overlay\nrecords the ground "
                      "under you.\n\ngrey: not measured\nbrown: contour lines\n"
                      "number by a pin: its\nground height\n"
                      "dashes: survey course\n+ : you are here\n"
                      "drag to move\n"
                      "wheel to zoom").pack(anchor="w", fill="x")

        self.canvas = tk.Canvas(self.frame, bg=heightmap.GREY,
                                highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Map>", lambda e: self.draw())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)

    def open(self, body):
        """Show `body`, around the busiest area surveyed on it."""
        self.body, self.offset = body, (0.0, 0.0)
        self.goto_var.set("")
        self.goto_choices()
        with self.state.lock:
            self.centre = heightmap.centre_of(self.state.heightmap.get(body))
        self.draw()

    def goto_choices(self):
        """The open body's waypoints, for Go to."""
        names = [n for n, _ in starmap.body_points(snapshot(self.state)[0],
                                                   self.body)]
        if list(self.goto.cget("values")) != names:
            self.goto.config(values=names)

    def go_to(self):
        """Centre on the waypoint picked in Go to. Following you would pull
        the map straight back, so it waits until you move on."""
        name = self.goto_var.get()
        wps, _ = snapshot(self.state)
        p = dict(starmap.body_points(wps, self.body)).get(name)
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
        """What the map depends on, to redraw only when it changes. Lock held."""
        st = self.state
        if self.held is not None and moved_on(self.held,
                                              (st.system, st.planet)):
            self.held = None
        on_it = (self.follow.get() and self.held is None
                 and st.planet == self.body)
        plan = st.survey_saved
        return (self.body, heightmap.cell_count(st.heightmap, self.body),
                tuple(st.pos) if on_it and st.pos else None,
                plan and (plan["origin"], plan["leg"], plan["joined"]))

    def refresh(self):
        with self.state.lock:
            bodies = sorted(self.state.heightmap)
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
            # Contours and relief are worked out for twice the map each way
            # and kept until the survey or the zoom changes or you pan off
            # them. Only what is switched on is worked out.
            key = (self.body, self.seen[1], self.mpp)

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

        def spot(p):
            xy = heightmap.to_map(p, frame, radius)
            if xy and abs(xy[0]) <= half_w and abs(xy[1]) <= half_h:
                return w / 2 + xy[0] / self.mpp, h / 2 - xy[1] / self.mpp
            return None

        if frame:
            elev = {wp["name"]: wp.get("elev") for wp in wps
                    if wp["planet"] == self.body}
            for name, p in (starmap.body_points(wps, self.body)
                            if self.show["waypoints"].get() else ()):
                xy = spot(p)
                if xy:
                    sx, sy = xy
                    c.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill=PIN,
                                  outline=BG)
                    # A spot height, as BM 1570 on a paper map.
                    z = elev.get(name)
                    c.create_text(sx + 7, sy, anchor="w", fill=FG,
                                  font=mono(9),
                                  text=name if z is None else f"{name} {z:.0f}")
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
                c.create_line(*flat, fill=GRID_LINE)
            if edge:
                c.create_text(edge[0] + 3, edge[1] + (-2 if kind == "lat"
                                                      else 2),
                              anchor="sw" if kind == "lat" else "nw",
                              text=f"{deg:+.{places}f}", fill=FG,
                              font=mono(8))

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
                          fill=colour, width=2 if i == k else 1,
                          dash=(6, 4))

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
        """The height scale, low to high, for the cells in view."""
        lg = self.legend
        lg.delete("all")
        lg.configure(height=18 + mono(9).metrics("linespace"))
        if not any_cells:
            return
        for i in range(160):
            lg.create_line(i, 0, i, 14, fill=heightmap.height_colour(
                lo + (hi - lo) * i / 159, lo, hi))
        lg.create_text(0, 18, anchor="nw", fill=DIM, font=mono(9),
                       text=self.scl.fmt_dist(lo))
        lg.create_text(160, 18, anchor="ne", fill=DIM, font=mono(9),
                       text=self.scl.fmt_dist(hi))


# ----------------------------------------------------------------------------
# Waypoint editor
# ----------------------------------------------------------------------------


class WaypointEditor:
    COLUMNS = (("name", 220), ("system", 80), ("body", 110), ("around", 120),
               ("lat", 80), ("lon", 85), ("height", 280), ("saved", 170),
               ("note", 200))
    AUTO = "auto (by distance)"

    def __init__(self, parent, state, scl, on_show):
        self.state, self.scl, self.on_show = state, scl, on_show
        self.frame = tk.Frame(parent, bg=BG)
        self.seen, self.loaded = None, False

        bar = tk.Frame(self.frame, bg=BG)
        bar.pack(side="top", fill="x", pady=(0, 6))
        self.sys_var, self.body_var = tk.StringVar(value="all"),             tk.StringVar(value="all")
        picks = []
        for text, var in (("System", self.sys_var), ("Body", self.body_var)):
            tk.Label(bar, text=text, font=mono(), fg=DIM, bg=BG).pack(
                side="left", padx=(0 if not picks else 18, 6))
            picks.append(ttk.Combobox(bar, textvariable=var, width=16,
                                      state="readonly", values=("all",)))
            picks[-1].pack(side="left")
        self.sys_pick, self.body_pick = picks
        self.sys_pick.bind("<<ComboboxSelected>>", lambda e: self.pick_system())
        self.body_pick.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        self.tree = ttk.Treeview(self.frame, show="headings", selectmode="browse",
                                 columns=[n for n, _ in self.COLUMNS])
        for name, width in self.COLUMNS:
            self.tree.heading(name, text=name.upper())
            set_column(self.tree, name, width, stretch=name == "note")
        scroll = ttk.Scrollbar(self.frame, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        edit = tk.Frame(self.frame, bg=BG)
        edit.pack(side="bottom", fill="x", pady=(8, 0))
        scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        row = tk.Frame(edit, bg=BG)
        row.pack(fill="x")
        tk.Label(row, text="Name", font=mono(), fg=DIM, bg=BG).pack(side="left")
        self.name_var = tk.StringVar()
        name_entry = tk.Entry(row, textvariable=self.name_var, font=mono(),
                              bg=PANEL, fg=FG, insertbackground=FG,
                              relief="flat", width=32)
        name_entry.pack(side="left", padx=6)
        name_entry.bind("<Return>", lambda e: self.rename())
        ttk.Button(row, text="RENAME", command=self.rename).pack(side="left")
        ttk.Button(row, text="DELETE", command=self.delete).pack(side="left",
                                                                 padx=6)
        ttk.Button(row, text="SHOW ON MAP", command=self.show_on_map).pack(
            side="left")
        # Deep space only: which body the star map shows it around.
        tk.Label(row, text="Shown around", font=mono(), fg=DIM, bg=BG).pack(
            side="left", padx=(18, 6))
        self.parent_var = tk.StringVar()
        self.parent_pick = ttk.Combobox(row, textvariable=self.parent_var,
                                        state="disabled", width=22)
        self.parent_pick.pack(side="left")
        self.parent_pick.bind("<<ComboboxSelected>>",
                              lambda e: self.set_parent())
        tk.Label(edit, text="Note", font=mono(), fg=DIM, bg=BG).pack(anchor="w",
                                                                   pady=(6, 0))
        self.note = text_box(edit, 4)
        self.note.pack(fill="x")
        ttk.Button(edit, text="SAVE NOTE", command=self.save_note).pack(
            anchor="e", pady=(4, 0))

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def select(self, name):
        """Select a waypoint and scroll to it, clearing a filter that hides
        it."""
        self.refresh()
        if not self.tree.exists(name):
            self.sys_var.set("all")
            self.body_var.set("all")
            self.refresh()
        if self.tree.exists(name):
            self.tree.selection_set(name)
            self.tree.see(name)

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
        return (w["name"], w.get("system", "unknown"), w["planet"],
                around or "", lat, lon,
                self.scl.describe_height(w), w.get("saved", ""),
                note[0] if note else "")

    def refresh(self):
        wps, bodies = snapshot(self.state)
        placed = {sys: starmap.placed_bodies(bodies, sys)
                  for sys in starmap.SYSTEMS}
        shown, systems, bodies = self.scl.filter_waypoints(
            wps, self.sys_var.get(), self.body_var.get())
        for var, names in ((self.sys_var, systems), (self.body_var, bodies)):
            if var.get() not in names:
                var.set("all")
        seen = (tuple((w["name"], w["planet"], w.get("note"), w.get("parent"))
                      for w in wps),
                tuple((sys, tuple(p)) for sys, p in placed.items()),
                self.sys_var.get(), self.body_var.get())
        if seen == self.seen:
            return
        self.seen = seen
        self.sys_pick.config(values=["all"] + systems)
        self.body_pick.config(values=["all"] + bodies)
        chosen = self.selected()
        self.tree.delete(*self.tree.get_children())
        for w in sorted(shown, key=lambda q: (q.get("system", ""), q["planet"],
                                            q["name"].lower())):
            self.tree.insert("", "end", iid=w["name"],
                             values=self.row_values(w, placed))
        if chosen and self.tree.exists(chosen):
            self.tree.selection_set(chosen)
        else:
            self.on_select()

    def pick_system(self):
        """A new system: its bodies are the choices, starting from all."""
        self.body_var.set("all")
        self.refresh()

    def on_select(self, _event=None):
        name = self.selected()
        # A rebuild reselects the same row; reloading then would throw away a
        # note being typed when the overlay saves a waypoint.
        if name == self.loaded:
            return
        self.loaded = name
        wps, _ = snapshot(self.state)
        w = next((q for q in wps if q["name"] == name), None)
        self.name_var.set(name or "")
        set_text(self.note, (w or {}).get("note", ""))
        if w is None or w.get("frame") != "system":
            self.parent_var.set("")
            self.parent_pick.config(state="disabled", values=())
            return
        _, bodies = snapshot(self.state)
        self.parent_pick.config(state="readonly", values=[self.AUTO, "none"] +
                                sorted(starmap.placed_bodies(bodies,
                                                             w.get("system"))))
        self.parent_var.set(w.get("parent", self.AUTO))

    def set_parent(self):
        name, pick = self.selected(), self.parent_var.get()
        st, scl = self.state, self.scl
        with st.lock:
            if name is None:
                return
            try:
                scl.set_waypoint_parent(st.waypoints, name,
                                        None if pick == self.AUTO else pick)
            except ValueError as exc:
                st.flash(str(exc))
                return
            scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            st.flash(f"{name}: shown around {pick}")
        self.refresh()

    def show_on_map(self):
        name = self.selected()
        if name is None:
            with self.state.lock:
                self.state.flash("select a waypoint first")
            return
        self.on_show(name)

    def rename(self):
        old, new = self.selected(), self.name_var.get().strip()
        st, scl = self.state, self.scl
        with st.lock:
            if old is None:
                st.flash("select a waypoint first")
                return
            try:
                scl.rename_waypoint(st.waypoints, old, new)
            except ValueError as exc:
                st.flash(str(exc))
                return
            if st.target == old:         # the target follows its waypoint
                st.target = new
            scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            st.flash(f"renamed to {new}")
        self.refresh()
        if self.tree.exists(new):
            self.tree.selection_set(new)

    def delete(self):
        name = self.selected()
        if name is None or not messagebox.askyesno(
                "Delete waypoint", f"Delete '{name}'?", parent=self.frame):
            return
        st, scl = self.state, self.scl
        with st.lock:
            try:
                scl.delete_waypoint(st.waypoints, name)
            except ValueError as exc:
                st.flash(str(exc))
                return
            if st.target == name:
                st.target = None
            scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            st.flash(f"deleted {name}")
        self.refresh()

    def save_note(self):
        name = self.selected()
        st, scl = self.state, self.scl
        with st.lock:
            if name is None:
                st.flash("select a waypoint first")
                return
            try:
                scl.set_waypoint_note(st.waypoints, name,
                                      self.note.get("1.0", "end"))
            except ValueError as exc:
                st.flash(str(exc))
                return
            scl.save_json(scl.WAYPOINT_FILE, st.waypoints)
            st.flash(f"note saved on {name}")
        self.refresh()


# ----------------------------------------------------------------------------
# Report viewer
# ----------------------------------------------------------------------------


def place_title(key, rec):
    """What to call an analysed place in the list."""
    if rec.get("scope") == "space":
        return f"{rec.get('label') or 'place in space'}  [{key[6:]}]"
    return key


class ReportViewer:
    def __init__(self, parent, state, scl):
        self.state, self.scl = state, scl
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

        self.tree = ttk.Treeview(self.frame, show="headings", selectmode="browse",
                                 columns=("place", "system", "kind", "analysed"))
        for name, width in (("place", 300), ("system", 80), ("kind", 85),
                            ("analysed", 150)):
            self.tree.heading(name, text=name.upper())
            set_column(self.tree, name, width, stretch=name == "place")
        self.tree.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_selected())

        right = tk.Frame(self.frame, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.report = text_box(right, 16, readonly=True)
        self.report.config(wrap="none")
        self.report.pack(fill="both", expand=True)
        tk.Label(right, text="Your notes", font=mono(), fg=DIM, bg=BG).pack(
            anchor="w", pady=(6, 0))
        self.notes = text_box(right, 8)
        self.notes.pack(fill="x")
        ttk.Button(right, text="SAVE NOTES", command=self.save_notes).pack(
            anchor="e", pady=(4, 0))

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def refresh(self):
        _, bodies = snapshot(self.state)
        systems = sorted({v.get("system", "?") for v in bodies.values()})
        if self.sys_var.get() not in systems:
            self.sys_var.set("all")
        pick = self.sys_var.get()
        seen = (tuple((k, v.get("analysed"), v.get("label")) for k, v in
                      bodies.items()), pick)
        if seen != self.seen:
            self.seen = seen
            self.sys_pick.config(values=["all"] + systems)
            chosen = self.selected()
            self.tree.delete(*self.tree.get_children())
            shown = {k: v for k, v in bodies.items()
                     if pick == "all" or v.get("system", "?") == pick}
            for key, rec in sorted(shown.items(), key=lambda kv: (
                    kv[1].get("system", ""), kv[1].get("scope") == "space",
                    kv[0])):
                self.tree.insert("", "end", iid=key, values=(
                    place_title(key, rec), rec.get("system", "?"),
                    rec.get("kind", "?"), rec.get("analysed", "")))
            if chosen and self.tree.exists(chosen):
                self.tree.selection_set(chosen)
        self.show_report()          # every tick: the sun figures are live

    def show_report(self):
        key = self.selected()
        st, scl = self.state, self.scl
        with st.lock:
            rec = st.bodies.get(key)
            here = rec is not None and scl.current_place(st)[0] == key
        if rec is None:
            lines = ["select a place on the left"]
        elif here:
            lines = scl.body_report(st)      # live sun where you are now
        else:
            lines = scl.spin.report_lines(rec)
            if rec.get("station"):
                lines += scl.stations.report_lines(rec["station"],
                                                   scl.READOUT_COLS)
        set_text(self.report, "\n".join(lines))

    def show_selected(self):
        key = self.selected()
        _, bodies = snapshot(self.state)
        set_text(self.notes, (bodies.get(key) or {}).get("notes", ""))
        self.show_report()

    def save_notes(self):
        key = self.selected()
        st, scl = self.state, self.scl
        with st.lock:
            if key is None:
                st.flash("select a place first")
                return
            try:
                scl.set_report_notes(st.bodies, key,
                                     self.notes.get("1.0", "end"))
            except ValueError as exc:
                st.flash(str(exc))
                return
            scl.save_json(scl.BODIES_FILE, st.bodies)
            st.flash(f"notes saved on {key}")
