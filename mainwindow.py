"""Main window: live control, editable session list, daily focus, projects, charts and settings."""
import csv
import time
import tkinter as tk
from collections import defaultdict
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk

from db import MIN_SESSION_S

RED, ORANGE, GREEN, GRAY = "#ff453a", "#ff9f0a", "#34c759", "#8e8e93"
ACCENT = "#2f7dd1"
BG, CARD, TEXT, MUTED, LINE = "#f2f2f7", "#ffffff", "#1c1c1e", "#6e6e73", "#e1e1e6"
DISC, TRACK = "#1c1c1e", "#3a3a3c"
RANGES = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "All time": None}
SHOW = {"Today": 1, "Last 7 days": 7, "Last 30 days": 30, "All time": None}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")
CSV_COLUMNS = ["start", "end", "minutes", "plan_minutes", "plan_met", "project"]


def plan_color(elapsed, plan_s):
    """Red below half of the plan, orange up to the plan, green once it is reached."""
    frac = elapsed / plan_s
    return RED if frac < 0.5 else ORANGE if frac < 1 else GREEN


def split_hours(sessions):
    """Yield (hour_start, seconds, project), cutting every session at hour boundaries."""
    for _, project, start, end, _plan in sessions:
        cur = datetime.fromtimestamp(start)
        stop = datetime.fromtimestamp(end)
        while cur < stop:
            hour = cur.replace(minute=0, second=0, microsecond=0)
            seg = min(stop, hour + timedelta(hours=1))
            yield hour, (seg - cur).total_seconds(), project
            cur = seg


def fmt_hm(seconds):
    m = int(round(seconds / 60))
    return f"{m // 60}h {m % 60:02d}m"


def mmss(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def plan_met(start, end, plan):
    return bool(plan) and end - start >= plan * 60


def plan_text(start, end, plan):
    if not plan:
        return ""
    return f"{plan}m  ✓" if plan_met(start, end, plan) else f"{plan}m"


def parse_dt(text):
    text = text.strip()
    for fmt in DT_FORMATS:
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            pass
    raise ValueError(f"Cannot read date/time '{text}' (use YYYY-MM-DD HH:MM[:SS])")


def fmt_dt(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def apply_style(win):
    st = ttk.Style(win)
    st.theme_use("clam")
    win.configure(bg=BG)
    base = ("Segoe UI", 10)
    st.configure(".", background=BG, foreground=TEXT, font=base, bordercolor=LINE, focuscolor=BG)
    st.configure("TFrame", background=BG)
    st.configure("Card.TFrame", background=CARD, relief="solid", borderwidth=1, bordercolor=LINE)
    st.configure("Plain.TFrame", background=CARD)
    st.configure("TLabel", background=BG)
    st.configure("Card.TLabel", background=CARD)
    st.configure("Muted.TLabel", background=BG, foreground=MUTED)
    st.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
    st.configure("Title.TLabel", background=CARD, font=("Segoe UI Semibold", 11))
    st.configure("Stat.TLabel", background=CARD, font=("Segoe UI Semibold", 18))
    st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
    st.configure("TNotebook.Tab", padding=(18, 9), font=("Segoe UI Semibold", 10), background=BG, foreground=MUTED)
    st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", ACCENT)])
    st.configure("Treeview", rowheight=30, background=CARD, fieldbackground=CARD, borderwidth=0, font=base)
    st.configure("Treeview.Heading", background="#eceff3", foreground=MUTED, relief="flat",
                 font=("Segoe UI Semibold", 9), padding=(8, 6))
    st.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
    st.configure("TButton", padding=(12, 6), background="#eef0f4", borderwidth=1, relief="flat")
    st.map("TButton", background=[("active", "#eaf2fb")])
    st.configure("Accent.TButton", background=ACCENT, foreground="white", padding=(12, 6))
    st.map("Accent.TButton", background=[("active", "#256bb8")])
    st.configure("Danger.TButton", foreground=RED)
    st.configure("TCheckbutton", background=BG)
    st.configure("TCombobox", padding=4, fieldbackground="white")
    st.map("TCombobox", fieldbackground=[("readonly", "white")], background=[("readonly", "#eef0f4")])
    st.configure("TSpinbox", padding=4, fieldbackground="white")


def make_tree(parent, cols, widths):
    frame = ttk.Frame(parent, style="Plain.TFrame")
    tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
    for c, w in zip(cols, widths):
        tree.heading(c, text=c)
        tree.column(c, width=w, anchor="w")
    tree.tag_configure("odd", background="#f7f8fa")
    tree.tag_configure("met", foreground="#1e8e3e")
    scroll = ttk.Scrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    return frame, tree


def fill(tree, rows):
    """Replace the rows (iid, values[, tags]) but keep selection and scroll position."""
    sel, top = set(tree.selection()), tree.yview()[0]
    tree.delete(*tree.get_children())
    for i, (iid, values, *extra) in enumerate(rows):
        tags = (("odd",) if i % 2 else ()) + tuple(extra[0] if extra else ())
        tree.insert("", "end", iid=iid, values=values, tags=tags)
    keep = [i for i in sel if tree.exists(i)]
    if keep:
        tree.selection_set(keep)
    tree.yview_moveto(top)


class SessionDialog(tk.Toplevel):
    """Add or edit one session: project, start, end, plan."""

    def __init__(self, parent, projects, session=None):
        super().__init__(parent)
        self.result = None
        self.title("Edit session" if session else "Add session")
        self.configure(bg=BG)
        self.transient(parent)
        self.resizable(False, False)
        card = ttk.Frame(self, style="Card.TFrame", padding=16)
        card.pack(padx=14, pady=14)

        _, project, start, end, plan = session or (None, "", int(time.time()) - 1200, int(time.time()), 20)
        self.vars = {
            "project": tk.StringVar(value=project),
            "start": tk.StringVar(value=fmt_dt(start)),
            "end": tk.StringVar(value=fmt_dt(end)),
            "plan": tk.StringVar(value=str(plan or "")),
        }
        rows = [("Project", "project"), ("Start", "start"), ("End", "end"), ("Plan (minutes)", "plan")]
        for r, (label, key) in enumerate(rows):
            ttk.Label(card, text=label, style="Card.TLabel").grid(row=r, column=0, sticky="w", pady=5, padx=(0, 14))
            if key == "project":
                w = ttk.Combobox(card, textvariable=self.vars[key], values=projects, width=30)
            else:
                w = ttk.Entry(card, textvariable=self.vars[key], width=32)
            w.grid(row=r, column=1, pady=5)
        ttk.Label(card, text="Format: YYYY-MM-DD HH:MM:SS", style="CardMuted.TLabel").grid(
            row=4, column=1, sticky="w")
        btns = ttk.Frame(card, style="Plain.TFrame")
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self.save).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.update_idletasks()
        self.geometry(f"+{parent.winfo_rootx() + 140}+{parent.winfo_rooty() + 120}")
        self.grab_set()
        self.wait_window()

    def save(self):
        try:
            project = self.vars["project"].get().strip()
            if not project:
                raise ValueError("Project name is required.")
            start, end = parse_dt(self.vars["start"].get()), parse_dt(self.vars["end"].get())
            if end - start < MIN_SESSION_S:
                raise ValueError("End must be later than start.")
            plan_text_ = self.vars["plan"].get().strip()
            plan = int(plan_text_) if plan_text_ else None
            if plan is not None and plan < 1:
                raise ValueError("Plan must be at least 1 minute (or empty).")
        except ValueError as e:
            messagebox.showerror("Session", str(e), parent=self)
            return
        self.result = (project, start, end, plan)
        self.destroy()


class MainWindow:
    def __init__(self, app):
        self.app = app
        self.win = win = app.root
        apply_style(win)
        win.title("Focus Tracker")
        h = min(980, int(win.winfo_screenheight() * 0.92))
        win.geometry(f"1180x{h}+40+10")
        win.minsize(980, 640)

        bar = ttk.Frame(win, padding=(16, 12, 16, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="Focus Tracker", font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Button(bar, text="Export CSV", command=self.export_csv).pack(side="right")
        ttk.Button(bar, text="Import CSV", command=self.import_csv).pack(side="right", padx=8)

        self.tabs = ttk.Notebook(win)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self._build_home()
        self._build_daily()
        self._build_projects()
        self._build_charts()
        self._build_settings()
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self.refresh())

        self.refresh()
        self._live()
        self._auto()

    def visible(self):
        return self.win.state() != "withdrawn"

    def _auto(self):
        self.refresh()
        self.win.after(30000, self._auto)

    # ---- small widget helpers ----
    def _spin(self, parent, key, default, lo, hi, cast=int, increment=1, after=None, width=6):
        db = self.app.db
        var = tk.StringVar(value=str(cast(float(db.get(key, default)))))

        def commit(*_):
            try:
                v = min(hi, max(lo, cast(float(var.get()))))
            except ValueError:
                v = cast(float(db.get(key, default)))
            var.set(str(v))
            db.set(key, v)
            if after:
                after(v)

        sp = ttk.Spinbox(parent, from_=lo, to=hi, increment=increment, textvariable=var,
                         width=width, command=commit)
        sp.bind("<Return>", commit)
        sp.bind("<FocusOut>", commit)
        return sp, var

    def _stat_card(self, parent, col, title):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 8))
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0))
        parent.columnconfigure(col, weight=1, uniform="stat")
        ttk.Label(card, text=title, style="CardMuted.TLabel").pack(anchor="w")
        value = ttk.Label(card, style="Stat.TLabel")
        value.pack(anchor="w")
        sub = ttk.Label(card, style="CardMuted.TLabel")
        sub.pack(anchor="w")
        return value, sub

    # ---- Home ----
    def _build_home(self):
        f = ttk.Frame(self.tabs, padding=(4, 14, 4, 4))
        self.tabs.add(f, text="  Home  ")

        top = ttk.Frame(f)
        top.pack(fill="x")
        top.columnconfigure(1, weight=1)

        hero = ttk.Frame(top, style="Card.TFrame", padding=14)
        hero.grid(row=0, column=0, sticky="ns")
        self.ring = c = tk.Canvas(hero, width=200, height=200, bg=CARD, highlightthickness=0)
        c.pack()
        c.create_oval(4, 4, 196, 196, fill=DISC, outline="")
        c.create_oval(20, 20, 180, 180, outline=TRACK, width=10)
        self.r_arc = c.create_arc(20, 20, 180, 180, start=90, extent=0, style="arc", outline=GRAY, width=10)
        self.r_time = c.create_text(100, 86, text="00:00", fill="white", font=("Segoe UI Semibold", 24))
        self.r_sub = c.create_text(100, 126, text="", fill=GRAY, font=("Segoe UI", 9))
        self.r_status = ttk.Label(hero, style="Title.TLabel")
        self.r_status.pack(pady=(8, 0))

        ctrl = ttk.Frame(top, style="Card.TFrame", padding=14)
        ctrl.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        grid = ttk.Frame(ctrl, style="Plain.TFrame")
        grid.pack(fill="x")
        ttk.Label(grid, text="Project", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
        self.project_var = tk.StringVar(value=self.app.project)
        self.project_box = ttk.Combobox(grid, textvariable=self.project_var, width=30, font=("Segoe UI", 11))
        self.project_box.grid(row=1, column=0, sticky="w", pady=(2, 8), padx=(0, 20))
        for ev in ("<<ComboboxSelected>>", "<Return>", "<FocusOut>"):
            self.project_box.bind(ev, self._project_chosen)
        ttk.Label(grid, text="Plan (minutes)", style="CardMuted.TLabel").grid(row=0, column=1, sticky="w")
        self.plan_spin, self.plan_var = self._spin(grid, "plan_min", 20, 1, 240, after=self.app.set_plan, width=6)
        self.plan_spin.grid(row=1, column=1, sticky="w", pady=(2, 8))

        self.btn = tk.Button(ctrl, text="Start", font=("Segoe UI Semibold", 13), fg="white", bg=GREEN,
                             activebackground="#2aa84f", activeforeground="white", relief="flat",
                             cursor="hand2", pady=4, command=self.app.toggle)
        self.btn.pack(fill="x", pady=(0, 8))

        hk = ttk.Frame(ctrl, style="Plain.TFrame")
        hk.pack(fill="x")
        ttk.Label(hk, text="Hotkey", style="CardMuted.TLabel").pack(side="left")
        ttk.Label(hk, textvariable=self.app.hotkey_var, style="Title.TLabel").pack(side="left", padx=10)
        ttk.Button(hk, text="Change...", command=self.app.capture_hotkey).pack(side="left")

        legend = ttk.Frame(ctrl, style="Plain.TFrame")
        legend.pack(fill="x", pady=(8, 0))
        for color, text in ((RED, "under half of the plan"), (ORANGE, "past half"), (GREEN, "plan reached")):
            tk.Label(legend, text="●", fg=color, bg=CARD, font=("Segoe UI", 12)).pack(side="left")
            ttk.Label(legend, text=text + "    ", style="CardMuted.TLabel").pack(side="left")

        stats = ttk.Frame(f)
        stats.pack(fill="x", pady=10)
        self.s_today = self._stat_card(stats, 0, "Today")
        self.s_week = self._stat_card(stats, 1, "This week")
        self.s_streak = self._stat_card(stats, 2, "Streak")
        self.s_plans = self._stat_card(stats, 3, "Plans met today")

        box = ttk.Frame(f, style="Card.TFrame", padding=12)
        box.pack(fill="both", expand=True)
        head = ttk.Frame(box, style="Plain.TFrame")
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="Sessions", style="Title.TLabel").pack(side="left")
        self.show_var = tk.StringVar(value="Today")
        show = ttk.Combobox(head, textvariable=self.show_var, values=list(SHOW), state="readonly", width=13)
        show.pack(side="left", padx=(14, 6))
        self.filter_var = tk.StringVar(value="All projects")
        self.filter_box = ttk.Combobox(head, textvariable=self.filter_var, state="readonly", width=22)
        self.filter_box.pack(side="left")
        for w in (show, self.filter_box):
            w.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(head, text="Delete", width=8, style="Danger.TButton", command=self.delete_session).pack(side="right")
        ttk.Button(head, text="Edit...", width=8, command=self.edit_session).pack(side="right", padx=6)
        ttk.Button(head, text="Add...", width=8, command=self.add_session).pack(side="right")
        frame, self.sess_tree = make_tree(
            box, ("Date", "Start", "End", "Duration", "Plan", "Project"), (110, 90, 90, 100, 90, 360))
        frame.pack(fill="both", expand=True)
        self.sess_tree.bind("<Double-1>", lambda e: self.edit_session())
        self.sess_tree.bind("<Delete>", lambda e: self.delete_session())

    # ---- other tabs ----
    def _build_daily(self):
        f = ttk.Frame(self.tabs, padding=(4, 14, 4, 4))
        self.tabs.add(f, text="  Daily  ")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Range", style="Muted.TLabel").pack(side="left")
        self.range_var = tk.StringVar(value="Last 30 days")
        box = ttk.Combobox(bar, textvariable=self.range_var, values=list(RANGES), state="readonly", width=14)
        box.pack(side="left", padx=8)
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        frame, self.daily_tree = make_tree(
            f, ("Date", "Day", "Focus", "Sessions", "Plans met", "Projects"), (110, 60, 90, 80, 90, 600))
        frame.pack(fill="both", expand=True)

    def _build_projects(self):
        f = ttk.Frame(self.tabs, padding=(4, 14, 4, 4))
        self.tabs.add(f, text="  Projects  ")
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(0, 8))
        ttk.Button(btns, text="Add...", command=self.add_project).pack(side="left")
        ttk.Button(btns, text="Rename...", command=self.rename_project).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete", style="Danger.TButton", command=self.delete_project).pack(side="left")
        frame, self.proj_tree = make_tree(f, ("Project", "Total", "Sessions", "Last used"), (320, 110, 90, 130))
        frame.pack(fill="both", expand=True)

    def _build_charts(self):
        f = ttk.Frame(self.tabs, padding=(4, 8, 4, 4))
        self.tabs.add(f, text="  Charts  ")
        self.charts_tab = f
        self.canvas = None  # matplotlib is loaded the first time Charts is opened

    def _ensure_charts(self):
        if self.canvas is None:
            import matplotlib
            matplotlib.use("TkAgg")
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            self.fig = Figure(figsize=(10, 7), dpi=100, facecolor=BG)
            self.canvas = FigureCanvasTkAgg(self.fig, master=self.charts_tab)
            self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_settings(self):
        f = ttk.Frame(self.tabs, padding=20)
        self.tabs.add(f, text="  Settings  ")
        grid = ttk.Frame(f)
        grid.pack(anchor="w")
        ttk.Label(grid, text="Daily goal (hours)").grid(row=0, column=0, sticky="w", pady=6)
        sp, _ = self._spin(grid, "goal_hours", 4, 0.5, 24, cast=float, increment=0.5, after=lambda v: self.refresh())
        sp.grid(row=0, column=1, padx=14)
        ttk.Label(grid, text="Auto-pause after idle (minutes, 0 = never)").grid(row=1, column=0, sticky="w", pady=6)
        sp, _ = self._spin(grid, "idle_minutes", 5, 0, 240)
        sp.grid(row=1, column=1, padx=14)
        ttk.Checkbutton(f, text="Start with Windows", variable=self.app.autostart_var,
                        command=self.app.toggle_autostart).pack(anchor="w", pady=(10, 2))
        ttk.Checkbutton(f, text="Show the corner icon (drag it anywhere; right-click > Reset icon position)",
                        variable=self.app.overlay_var,
                        command=self.app.apply_overlay_visibility).pack(anchor="w", pady=(2, 10))
        ttk.Label(f, text=f"Data file: {self.app.db_path}", style="Muted.TLabel").pack(anchor="w", pady=4)
        ttk.Label(f, text="Closing this window keeps Focus Tracker running in the system tray (next to the clock).",
                  style="Muted.TLabel").pack(anchor="w")
        ttk.Button(f, text="Quit Focus Tracker", style="Danger.TButton", command=self.app.quit).pack(anchor="w", pady=18)

    # ---- project / plan edits ----
    def _project_chosen(self, _=None):
        name = self.project_var.get().strip()
        if name and name != self.app.project:
            self.app.set_project(name)
            self.refresh()

    def add_project(self):
        name = simpledialog.askstring("Add project", "Project name:", parent=self.win)
        if name and name.strip():
            self.app.db.add_project(name.strip())
            self.refresh()

    @staticmethod
    def _selected(tree):
        sel = tree.selection()
        return sel[0] if sel else None

    def rename_project(self):
        old = self._selected(self.proj_tree)
        if not old:
            return
        new = simpledialog.askstring("Rename project", f"New name for '{old}':", initialvalue=old, parent=self.win)
        if new and new.strip() and new.strip() != old:
            new = new.strip()
            self.app.db.rename_project(old, new)
            if self.app.project == old:
                self.app.project = new
                self.app.db.set("project", new)
            self.refresh()

    def delete_project(self):
        name = self._selected(self.proj_tree)
        if not name:
            return
        n = self.app.db.con.execute("SELECT COUNT(*) FROM sessions WHERE project=?", (name,)).fetchone()[0]
        if not messagebox.askyesno("Delete project", f"Delete '{name}' and its {n} recorded session(s)?\n"
                                   "This cannot be undone.", parent=self.win):
            return
        if self.app.session and self.app.project == name:
            self.app.pause()
        self.app.db.delete_project(name)
        if self.app.project == name:
            self.app.project = ""
            self.app.db.set("project", "")
        self.app.refresh_today()
        self.refresh()

    # ---- session edits ----
    def _selected_session(self):
        sid = self._selected(self.sess_tree)
        if not sid:
            messagebox.showinfo("Sessions", "Select a session in the list first.", parent=self.win)
            return None
        if self.app.session and int(sid) == self.app.session[0]:
            messagebox.showinfo("Sessions", "Pause the running session first.", parent=self.win)
            return None
        return next(s for s in self.app.db.sessions(int(time.time())) if s[0] == int(sid))

    def add_session(self):
        dlg = SessionDialog(self.win, self.app.db.projects())
        if dlg.result:
            self.app.db.add_session(*dlg.result)
            self._changed()

    def edit_session(self):
        session = self._selected_session()
        if not session:
            return
        dlg = SessionDialog(self.win, self.app.db.projects(), session)
        if dlg.result:
            self.app.db.update_session(session[0], *dlg.result)
            self._changed()

    def delete_session(self):
        session = self._selected_session()
        if session and messagebox.askyesno("Delete session", "Delete the selected session?", parent=self.win):
            self.app.db.delete_session(session[0])
            self._changed()

    def _changed(self):
        self.app.refresh_today()
        self.refresh()

    # ---- import / export ----
    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv",
                                            initialfile="focus_sessions.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CSV_COLUMNS)
            for _, project, start, end, plan in self.app.db.sessions(int(time.time())):
                w.writerow([fmt_dt(start), fmt_dt(end), round((end - start) / 60, 1), plan or "",
                            int(plan_met(start, end, plan)), project])
        messagebox.showinfo("Export", f"Exported to\n{path}", parent=self.win)

    def import_csv(self):
        path = filedialog.askopenfilename(parent=self.win, filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        db, added, skipped, bad = self.app.db, 0, 0, []
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if not {"start", "end", "project"} <= set(reader.fieldnames or []):
                    raise ValueError("The file needs the columns: start, end, project "
                                     "(optional: plan_minutes). Use a file made by Export CSV.")
                for n, row in enumerate(reader, start=2):
                    try:
                        project = (row["project"] or "").strip()
                        start, end = parse_dt(row["start"]), parse_dt(row["end"])
                        plan = int(float(row["plan_minutes"])) if (row.get("plan_minutes") or "").strip() else None
                        if not project or end - start < MIN_SESSION_S:
                            raise ValueError("empty project or end before start")
                    except (ValueError, TypeError, KeyError) as e:
                        bad.append(f"line {n}: {e}")
                        continue
                    if db.has_session(project, start, end):
                        skipped += 1
                    else:
                        db.add_session(project, start, end, plan)
                        added += 1
        except (OSError, ValueError, csv.Error) as e:
            messagebox.showerror("Import", str(e), parent=self.win)
            return
        msg = f"Imported {added} session(s), skipped {skipped} duplicate(s)."
        if bad:
            msg += f"\n{len(bad)} row(s) ignored:\n" + "\n".join(bad[:8]) + ("\n..." if len(bad) > 8 else "")
        messagebox.showinfo("Import", msg, parent=self.win)
        self._changed()

    # ---- live status (every second) ----
    def _live(self):
        a = self.app
        if self.visible():
            c = self.ring
            if a.session:
                el = time.time() - a.session[1]
                plan_s = a.session[2] * 60
                color, frac = plan_color(el, plan_s), min(1.0, el / plan_s)
                c.itemconfig(self.r_time, text=mmss(el))
                c.itemconfig(self.r_sub, text=f"of {a.session[2]} min" if el < plan_s else "plan reached!", fill=color)
                self.r_status.config(text=f"Focusing on {a.project}")
                self.btn.config(text="Pause", bg=ORANGE, activebackground="#e08a00")
            else:
                color, frac = GRAY, 0.0
                c.itemconfig(self.r_time, text="00:00")
                c.itemconfig(self.r_sub, text=f"plan {a.plan_min} min", fill=GRAY)
                self.r_status.config(text="Paused (idle)" if a.idle_paused else "Ready")
                self.btn.config(text="Start", bg=GREEN, activebackground="#2aa84f")
            c.itemconfig(self.r_arc, outline=color, extent=-min(359.9, 360 * frac))
            c.itemconfig(self.r_time, fill="white")
        self.win.after(1000, self._live)

    # ---- data refresh ----
    def refresh(self):
        if not self.visible():
            return
        a = self.app
        now = int(time.time())
        sessions = a.db.sessions(now)
        today = date.today()
        days = RANGES[self.range_var.get()]
        first = today - timedelta(days=days - 1) if days else (
            datetime.fromtimestamp(sessions[0][2]).date() if sessions else today)
        since = datetime.combine(first, datetime.min.time())

        info = defaultdict(lambda: {"secs": 0.0, "n": 0, "met": 0, "proj": defaultdict(float)})
        for _, _project, start, end, plan in sessions:
            d = info[datetime.fromtimestamp(start).date()]
            d["n"] += 1
            d["met"] += plan_met(start, end, plan)
        segs = list(split_hours(sessions))
        for hour, secs, project in segs:
            d = info[hour.date()]
            d["secs"] += secs
            d["proj"][project] += secs

        self._refresh_home(sessions, info, today)
        self._refresh_daily(info, first, today)
        self._refresh_projects(sessions)
        if self.tabs.select() == str(self.charts_tab):
            self._draw_charts(info, [s for s in segs if s[0] >= since], first, today)

    def _refresh_home(self, sessions, info, today):
        a = self.app
        projects = a.db.projects()
        self.project_box.config(values=projects)
        self.filter_box.config(values=["All projects"] + projects)
        if self.filter_var.get() not in self.filter_box.cget("values"):
            self.filter_var.set("All projects")
        if self.win.focus_get() is not self.project_box:
            self.project_var.set(a.project)
        if self.win.focus_get() is not self.plan_spin:
            self.plan_var.set(str(a.plan_min))

        d = today if info[today]["secs"] else today - timedelta(days=1)
        streak = 0
        while info[d]["secs"] > 0:
            streak += 1
            d -= timedelta(days=1)
        week_start = today - timedelta(days=today.weekday())
        week = sum(v["secs"] for k, v in info.items() if k >= week_start)
        goal = float(a.db.get("goal_hours", 4)) * 3600
        t = info[today]
        self.s_today[0].config(text=fmt_hm(t["secs"]))
        self.s_today[1].config(text=f"{min(100, int(100 * t['secs'] / goal))}% of {fmt_hm(goal)} goal")
        self.s_week[0].config(text=fmt_hm(week))
        self.s_week[1].config(text=f"since {week_start.strftime('%b %d')}")
        self.s_streak[0].config(text=f"{streak} day{'s' if streak != 1 else ''}")
        self.s_streak[1].config(text="in a row")
        self.s_plans[0].config(text=f"{t['met']} / {t['n']}")
        self.s_plans[1].config(text="reached the plan")

        days = SHOW[self.show_var.get()]
        since = datetime.combine(today - timedelta(days=days - 1), datetime.min.time()).timestamp() if days else 0
        wanted = self.filter_var.get()
        rows = []
        for sid, project, start, end, plan in reversed(sessions):
            if end < since or (wanted != "All projects" and project != wanted):
                continue
            s, e = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
            running = a.session is not None and sid == a.session[0]
            rows.append((str(sid), (s.strftime("%Y-%m-%d"), s.strftime("%H:%M:%S"),
                                    "running" if running else e.strftime("%H:%M:%S"), fmt_hm(end - start),
                                    plan_text(start, end, plan), project),
                         ("met",) if plan_met(start, end, plan) else ()))
        fill(self.sess_tree, rows)

    def _refresh_daily(self, info, first, today):
        rows = []
        for i in range((today - first).days + 1):
            d = today - timedelta(days=i)
            v = info.get(d)
            if not v or not v["secs"]:
                continue
            projects = ",  ".join(f"{p} {fmt_hm(s)}" for p, s in sorted(v["proj"].items(), key=lambda kv: -kv[1]))
            rows.append((d.isoformat(), (d.isoformat(), WEEKDAYS[d.weekday()], fmt_hm(v["secs"]),
                                         v["n"], f"{v['met']}/{v['n']}", projects)))
        fill(self.daily_tree, rows)

    def _refresh_projects(self, sessions):
        total, count, last = defaultdict(float), defaultdict(int), {}
        for _, project, start, end, _ in sessions:
            total[project] += end - start
            count[project] += 1
            last[project] = max(last.get(project, 0), start)
        rows = []
        for p in self.app.db.projects():
            when = datetime.fromtimestamp(last[p]).strftime("%Y-%m-%d") if p in last else "-"
            rows.append((p, (p, fmt_hm(total[p]), count[p], when)))
        fill(self.proj_tree, rows)

    def _draw_charts(self, info, segs, first, today):
        import matplotlib.dates as mdates
        import numpy as np
        self._ensure_charts()
        per_project = defaultdict(float)
        per_week = defaultdict(float)
        heat = np.zeros((7, 24))
        for hour, secs, project in segs:
            per_project[project] += secs
            heat[hour.weekday(), hour.hour] += secs / 3600
            per_week[hour.date() - timedelta(days=hour.weekday())] += secs

        fig = self.fig
        fig.clear()
        goal = float(self.app.db.get("goal_hours", 4))
        ax1, ax2, ax3, ax4 = fig.subplots(2, 2).flat
        for ax in (ax1, ax2, ax3, ax4):
            ax.set_facecolor(CARD)

        days = [first + timedelta(days=i) for i in range((today - first).days + 1)][-365:]
        hours = [info[d]["secs"] / 3600 if d in info else 0 for d in days]
        ax1.bar(days, hours, color=[GREEN if h >= goal else ACCENT for h in hours], width=0.8)
        ax1.axhline(goal, color=RED, lw=1, ls="--", label=f"Goal {goal:g}h")
        ax1.set_title("Hours per day")
        ax1.set_ylabel("hours")
        ax1.legend(loc="upper left", frameon=False)
        loc = mdates.AutoDateLocator()
        ax1.xaxis.set_major_locator(loc)
        ax1.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))

        items = sorted(per_project.items(), key=lambda kv: kv[1])[-10:]
        ax2.barh([k for k, _ in items], [v / 3600 for _, v in items], color=ACCENT)
        ax2.set_title("Hours per project")
        ax2.set_xlabel("hours")
        for i, (_, v) in enumerate(items):
            ax2.text(v / 3600, i, f" {fmt_hm(v)}", va="center", fontsize=8)
        ax2.margins(x=0.25)

        im = ax3.imshow(heat, aspect="auto", cmap="Blues")
        ax3.set_title("When you focus (hours)")
        ax3.set_yticks(range(7), WEEKDAYS)
        ax3.set_xticks(range(0, 24, 3), [f"{h:02d}:00" for h in range(0, 24, 3)])
        fig.colorbar(im, ax=ax3, pad=0.02).set_label("hours")

        weeks = sorted(per_week)[-12:]
        ax4.bar([w.strftime("%b %d") for w in weeks], [per_week[w] / 3600 for w in weeks], color=ACCENT)
        ax4.set_title("Hours per week")
        ax4.set_ylabel("hours")
        ax4.tick_params(axis="x", rotation=45)

        for ax in (ax1, ax2, ax4):
            ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()
        self.canvas.draw_idle()
