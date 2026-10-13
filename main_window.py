"""The main window: HUD switch, star map, waypoint editor and report viewer.

A normal window with a taskbar entry, next to the overlay and sharing its Tk
root and its State. It never imports sc_locator: the running module is handed
in as `scl`, because a second import of that file would carry its own file
paths and its own copy of the data, and could save over the real files.

The handlers here stay thin: the edits are sc_locator's helpers, the map
geometry is starmap's.
"""
import math
import time
import tkinter as tk
from tkinter import messagebox, ttk

import starmap

BG, PANEL, FG, DIM, ACCENT = "#05080c", "#0d141c", "#D8E2EE", "#8FA0B4", "#7CFFB2"
SUN = ("#B86A12", "#FFB347", "#FFE7B0")
PLANET = ("#1D4E73", "#3E8CC4", "#A8D8FF")
MOON = ("#4A5560", "#8A97A4", "#D0D8E0")
PIN, ORBIT, GRID = "#FF9F43", "#1B3348", "#2D5A80"
MONO = ("Consolas", 10)

SUN_PX, PLANET_PX, MOON_PX = 14, 9, 5


def snapshot(state):
    """Copies of the waypoints and analyses, so drawing never holds the lock."""
    with state.lock:
        return ([dict(w) for w in state.waypoints],
                {k: dict(v) for k, v in state.bodies.items()})


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
    style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL)
    style.configure("TNotebook", background=BG)
    style.configure("TNotebook.Tab", background=PANEL, foreground=DIM,
                    padding=(14, 4))
    style.map("TNotebook.Tab", background=[("selected", BG)],
              foreground=[("selected", ACCENT)])
    style.configure("Treeview", background=PANEL, foreground=FG,
                    fieldbackground=PANEL, font=MONO, rowheight=20)
    style.map("Treeview", background=[("selected", "#1F3A52")])
    style.configure("Treeview.Heading", background=BG, foreground=DIM)
    style.configure("TButton", background=PANEL, foreground=ACCENT)
    style.configure("TCheckbutton", background=BG, foreground=ACCENT)
    style.configure("TCombobox", foreground=BG)

    bar = tk.Frame(win, bg=BG)
    bar.pack(fill="x", padx=8, pady=(6, 0))
    hud_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(bar, text="HUD overlay", variable=hud_var,
                    command=lambda: set_hud(hud_var.get())).pack(side="left")
    status = tk.Label(bar, text="", font=MONO, fg=DIM, bg=BG, anchor="e")
    status.pack(side="right", fill="x", expand=True)

    nb = ttk.Notebook(win)
    nb.pack(fill="both", expand=True, padx=8, pady=8)
    editor = WaypointEditor(nb, state, scl)

    def pick_waypoint(name):
        """A pin clicked on the map: show that waypoint in the table."""
        nb.select(editor.frame)
        editor.select(name)

    tabs = [StarMap(nb, state, scl, pick_waypoint), editor,
            ReportViewer(nb, state, scl)]
    for tab, title in zip(tabs, ("Star map", "Waypoints", "Reports")):
        nb.add(tab.frame, text=title)

    def tick():
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
    t = tk.Text(parent, height=height, font=MONO, bg=PANEL, fg=FG,
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

    def __init__(self, parent, state, scl, on_pick):
        self.state, self.scl, self.on_pick = state, scl, on_pick
        # canvas item -> waypoint name. Not a tag: names have spaces in them,
        # and a canvas tag cannot.
        self.pins = {}
        self.frame = tk.Frame(parent, bg=BG)
        self.system, self.body = starmap.SYSTEMS[0], None
        self.yaw, self.pitch, self.zoom = 0.0, math.radians(60), 1.0
        self.press, self.dragged, self.seen = None, False, None
        self.ticks = 0
        self.om = None          # (lat, lon) of the marker the view sits above

        side = tk.Frame(self.frame, bg=BG)
        side.pack(side="left", fill="y", padx=(0, 8))
        tk.Label(side, text="System", font=MONO, fg=DIM, bg=BG).pack(anchor="w")
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
        self.box = box = tk.LabelFrame(side, font=MONO,
                            fg=ACCENT, bg=BG, bd=2, relief="groove",
                            padx=6, pady=6)
        box.pack(anchor="w", fill="both", expand=True)
        self.unplaced_hint = tk.Label(box, font=("Consolas", 9), fg=DIM, bg=BG,
                                      justify="left", anchor="w")
        self.unplaced_hint.pack(anchor="w", fill="x", pady=(0, 6))
        self.unplaced = tk.Listbox(box, font=MONO, bg=PANEL, fg=FG, width=22,
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
        tk.Label(self.om_bar, text="view from", font=("Consolas", 9), fg=DIM,
                 bg=BG).pack(side="left", padx=(0, 4))
        for name, lat, lon in scl.OM_POINTS:
            ttk.Button(self.om_bar, text=name, width=5,
                       command=lambda la=lat, lo=lon: self.view_om(la, lo)).pack(
                side="left", padx=1)
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

    def show(self, system, body):
        self.system, self.body, self.zoom, self.yaw = system, body, 1.0, 0.0
        self.om = None
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
        self.om = (lat, lon)
        self.draw()

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
            self.om = None              # turned by hand: no longer held
        self.yaw += dx * 0.01
        self.pitch = max(-math.pi / 2, min(math.pi / 2, self.pitch + dy * 0.01))
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
        seen = (tuple((w["name"], w["planet"]) for w in wps),
                tuple((k, tuple(v.get("centre") or ())) for k, v in bodies.items()))
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
        in steps fine enough not to show as bands at full window size."""
        steps = max(3, min(24, int(r / 6)))
        for i in range(steps):
            k = 1 - i / steps                    # 1 at the rim, toward 0
            rr, off = r * k, r * (1 - k) * 0.4
            col = blend(colours, i / max(1, steps - 1))
            self.canvas.create_oval(x - off - rr, y - off - rr, x - off + rr,
                                    y - off + rr, fill=col, outline="",
                                    tags=tags)

    def pin(self, x, y, name, label=None):
        """A location marker standing on (x, y), with its name (or `label`)
        beside it. Clicking any part of it opens the waypoint in the table."""
        c = self.canvas
        ids = [
            c.create_polygon(x, y, x - 5, y - 10, x + 5, y - 10, fill=PIN,
                             outline="", tags="pin"),
            c.create_oval(x - 6, y - 18, x + 6, y - 6, fill=PIN, outline="",
                          tags="pin"),
            c.create_oval(x - 2, y - 14, x + 2, y - 10, fill=BG, outline="",
                          tags="pin"),
            c.create_text(x + 9, y - 12, text=label or name, anchor="w",
                          fill=FG,
                          font=("Consolas", 9), tags=("pin", "label")),
        ]
        for item in ids:
            self.pins[item] = name

    def header(self, title, hint):
        self.canvas.create_text(12, 10, text=title, anchor="nw", fill=ACCENT,
                                font=("Consolas", 16, "bold"))
        self.canvas.create_text(12, 36, text=hint, anchor="nw", fill=DIM,
                                font=("Consolas", 9))

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
                              tags=tags, font=("Consolas", 9, "bold"))
            else:
                self.pin(sx, sy, what)
        for it in items:
            if it[1] == "body":
                self.hand_cursor("body:" + it[2]["key"])
        self.header(self.system.upper(),
                    "drag to rotate  -  wheel to zoom  -  click a planet or "
                    "moon to open it, a pin to edit it")

    def draw_body(self, wps, bodies, around, w, h):
        # The body's own frame turns with it; its moons and stations do not.
        # Turning the globe to where it is now keeps the two in step.
        spin = starmap.spin_angle(bodies.get(self.body), time.time())
        if self.om is not None:
            self.yaw, self.pitch = starmap.view_from(*self.om, spin)
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
                          font=("Consolas", 9, "bold"))

        def draw_item(item):
            (sx, sy, _), kind, key, label = item
            if kind == "pin":
                self.pin(sx, sy, key, label)
                return
            tags = ("body:" + key,)
            self.sphere(sx, sy, MOON_PX + 2, MOON, tags)
            c.create_text(sx, sy - MOON_PX - 6, text=label, anchor="s",
                          fill=PLANET[2], tags=tags + ("label",),
                          font=("Consolas", 9, "bold"))
            self.hand_cursor(tags[0])

        for spot, sign in poles:
            if spot[2] > 0:
                draw_pole(spot, sign)
        for item in behind:
            draw_item(item)
        is_moon = any(self.body in m for m in starmap.MOONS.values())
        self.sphere(cx, cy, r_px, MOON if is_moon else PLANET)
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
        if spin:
            facts.append("turned as it is now")
        # Short lines: the marker buttons sit at the top right of the canvas.
        lines = ["  -  ".join(facts)]
        if outer:
            lines.append("beyond the globe: distances not to scale")
        lines.append("drag to rotate  -  wheel to zoom")
        lines.append("click a pin to edit it, a moon to open it")
        self.header(f"{self.body.upper()}  ({where})", "\n".join(lines))


# ----------------------------------------------------------------------------
# Waypoint editor
# ----------------------------------------------------------------------------


class WaypointEditor:
    COLUMNS = (("name", 220), ("system", 80), ("body", 110), ("around", 120),
               ("lat", 80), ("lon", 85), ("height", 280), ("saved", 170),
               ("note", 200))
    AUTO = "auto (by distance)"

    def __init__(self, parent, state, scl):
        self.state, self.scl = state, scl
        self.frame = tk.Frame(parent, bg=BG)
        self.seen, self.loaded = None, False

        self.tree = ttk.Treeview(self.frame, show="headings", selectmode="browse",
                                 columns=[n for n, _ in self.COLUMNS])
        for name, width in self.COLUMNS:
            self.tree.heading(name, text=name.upper())
            self.tree.column(name, width=width, stretch=name == "note")
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
        tk.Label(row, text="Name", font=MONO, fg=DIM, bg=BG).pack(side="left")
        self.name_var = tk.StringVar()
        name_entry = tk.Entry(row, textvariable=self.name_var, font=MONO,
                              bg=PANEL, fg=FG, insertbackground=FG,
                              relief="flat", width=32)
        name_entry.pack(side="left", padx=6)
        name_entry.bind("<Return>", lambda e: self.rename())
        ttk.Button(row, text="RENAME", command=self.rename).pack(side="left")
        ttk.Button(row, text="DELETE", command=self.delete).pack(side="left",
                                                                 padx=6)
        # Deep space only: which body the star map shows it around.
        tk.Label(row, text="Shown around", font=MONO, fg=DIM, bg=BG).pack(
            side="left", padx=(18, 6))
        self.parent_var = tk.StringVar()
        self.parent_pick = ttk.Combobox(row, textvariable=self.parent_var,
                                        state="disabled", width=22)
        self.parent_pick.pack(side="left")
        self.parent_pick.bind("<<ComboboxSelected>>",
                              lambda e: self.set_parent())
        tk.Label(edit, text="Note", font=MONO, fg=DIM, bg=BG).pack(anchor="w",
                                                                   pady=(6, 0))
        self.note = text_box(edit, 4)
        self.note.pack(fill="x")
        ttk.Button(edit, text="SAVE NOTE", command=self.save_note).pack(
            anchor="e", pady=(4, 0))

    def selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def select(self, name):
        """Select a waypoint and scroll to it."""
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
        seen = (tuple((w["name"], w["planet"], w.get("note"), w.get("parent"))
                      for w in wps),
                tuple((sys, tuple(p)) for sys, p in placed.items()))
        if seen == self.seen:
            return
        self.seen = seen
        chosen = self.selected()
        self.tree.delete(*self.tree.get_children())
        for w in sorted(wps, key=lambda q: (q.get("system", ""), q["planet"],
                                            q["name"].lower())):
            self.tree.insert("", "end", iid=w["name"],
                             values=self.row_values(w, placed))
        if chosen and self.tree.exists(chosen):
            self.tree.selection_set(chosen)
        else:
            self.on_select()

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

        self.tree = ttk.Treeview(self.frame, show="headings", selectmode="browse",
                                 columns=("place", "system", "kind", "analysed"))
        for name, width in (("place", 300), ("system", 80), ("kind", 85),
                            ("analysed", 150)):
            self.tree.heading(name, text=name.upper())
            self.tree.column(name, width=width, stretch=name == "place")
        self.tree.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_selected())

        right = tk.Frame(self.frame, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.report = text_box(right, 16, readonly=True)
        self.report.config(wrap="none")
        self.report.pack(fill="both", expand=True)
        tk.Label(right, text="Your notes", font=MONO, fg=DIM, bg=BG).pack(
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
        seen = tuple((k, v.get("analysed"), v.get("label")) for k, v in
                     bodies.items())
        if seen != self.seen:
            self.seen = seen
            chosen = self.selected()
            self.tree.delete(*self.tree.get_children())
            for key, rec in sorted(bodies.items(), key=lambda kv: (
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
