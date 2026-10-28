"""Main window: live control, daily focus, projects, sessions, charts and settings."""
import csv
import time
import tkinter as tk
from collections import defaultdict
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

RED, ORANGE, GREEN, GRAY = "#ff453a", "#ff9f0a", "#34c759", "#8e8e93"
ACCENT = "#2f7dd1"
RANGES = {"Last 7 days": 7, "Last 30 days": 30, "Last 90 days": 90, "All time": None}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


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


def make_tree(parent, cols, widths):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
    for c, w in zip(cols, widths):
        tree.heading(c, text=c)
        tree.column(c, width=w, anchor="w")
    scroll = ttk.Scrollbar(frame, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    return frame, tree


def fill(tree, rows):
    """Replace the rows but keep selection and scroll position."""
    sel, top = set(tree.selection()), tree.yview()[0]
    tree.delete(*tree.get_children())
    for iid, values in rows:
        tree.insert("", "end", iid=iid, values=values)
    keep = [i for i in sel if tree.exists(i)]
    if keep:
        tree.selection_set(keep)
    tree.yview_moveto(top)


class MainWindow:
    def __init__(self, app):
        self.app = app
        self.win = win = app.root
        win.title("Focus Tracker")
        win.geometry("1100x780")
        win.minsize(900, 640)

        bar = ttk.Frame(win, padding=8)
        bar.pack(fill="x")
        ttk.Label(bar, text="Range:").pack(side="left")
        self.range_var = tk.StringVar(value="Last 30 days")
        box = ttk.Combobox(bar, textvariable=self.range_var, values=list(RANGES), state="readonly", width=14)
        box.pack(side="left", padx=(4, 8))
        box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(bar, text="Export CSV", command=self.export_csv).pack(side="left")
        self.summary = ttk.Label(bar, font=("Segoe UI", 10, "bold"))
        self.summary.pack(side="right")

        self.tabs = ttk.Notebook(win)
        self.tabs.pack(fill="both", expand=True)
        self._build_home()
        self._build_daily()
        self._build_projects()
        self._build_sessions()
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

    # ---- widgets ----
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

    def _build_home(self):
        f = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(f, text="Home")
        self.home = f
        self.status = ttk.Label(f, font=("Segoe UI Semibold", 16))
        self.status.pack(anchor="w")
        self.clock = ttk.Label(f, font=("Segoe UI Semibold", 44))
        self.clock.pack(anchor="w")
        self.bar = tk.Canvas(f, height=14, highlightthickness=0, bg="#d1d1d6")
        self.bar.pack(fill="x", pady=(2, 12))

        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Project").pack(side="left")
        self.project_var = tk.StringVar(value=self.app.project)
        self.project_box = ttk.Combobox(row, textvariable=self.project_var, width=28)
        self.project_box.pack(side="left", padx=(6, 16))
        for ev in ("<<ComboboxSelected>>", "<Return>", "<FocusOut>"):
            self.project_box.bind(ev, self._project_chosen)
        ttk.Label(row, text="Plan (min)").pack(side="left")
        self.plan_spin, self.plan_var = self._spin(
            row, "plan_min", 20, 1, 240, after=self.app.set_plan, width=5)
        self.plan_spin.pack(side="left", padx=(6, 16))
        self.btn = ttk.Button(row, text="Start", width=12, command=self.app.toggle)
        self.btn.pack(side="left")

        row = ttk.Frame(f)
        row.pack(fill="x", pady=(14, 0))
        ttk.Label(row, text="Start / pause hotkey:").pack(side="left")
        ttk.Label(row, textvariable=self.app.hotkey_var, font=("Segoe UI Semibold", 10)).pack(side="left", padx=8)
        ttk.Button(row, text="Change hotkey...", command=self.app.capture_hotkey).pack(side="left")
        ttk.Label(row, text="   Colors: red < 50% of plan, orange up to the plan, green when reached.",
                  foreground=GRAY).pack(side="left")

        ttk.Label(f, text="Today", font=("Segoe UI Semibold", 11)).pack(anchor="w", pady=(18, 4))
        self.today_label = ttk.Label(f)
        self.today_label.pack(anchor="w")
        frame, self.today_tree = make_tree(f, ("Start", "End", "Duration", "Plan", "Project"),
                                           (90, 90, 100, 90, 300))
        frame.pack(fill="both", expand=True, pady=(6, 0))

    def _build_daily(self):
        f = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(f, text="Daily")
        frame, self.daily_tree = make_tree(
            f, ("Date", "Day", "Focus", "Sessions", "Plans met", "Projects"), (100, 60, 90, 80, 90, 600))
        frame.pack(fill="both", expand=True)

    def _build_projects(self):
        f = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(f, text="Projects")
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(0, 6))
        ttk.Button(btns, text="Add...", command=self.add_project).pack(side="left")
        ttk.Button(btns, text="Rename...", command=self.rename_project).pack(side="left", padx=6)
        ttk.Button(btns, text="Delete", command=self.delete_project).pack(side="left")
        frame, self.proj_tree = make_tree(f, ("Project", "Total", "Sessions", "Last used"), (300, 100, 90, 120))
        frame.pack(fill="both", expand=True)

    def _build_sessions(self):
        f = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(f, text="Sessions")
        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(0, 6))
        ttk.Button(btns, text="Delete selected", command=self.delete_session).pack(side="left")
        frame, self.sess_tree = make_tree(
            f, ("Date", "Start", "End", "Duration", "Plan", "Project"), (100, 80, 80, 90, 80, 300))
        frame.pack(fill="both", expand=True)

    def _build_charts(self):
        f = ttk.Frame(self.tabs)
        self.tabs.add(f, text="Charts")
        self.charts_tab = f
        self.fig = Figure(figsize=(10, 7), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=f)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_settings(self):
        f = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(f, text="Settings")
        grid = ttk.Frame(f)
        grid.pack(anchor="w")
        ttk.Label(grid, text="Daily goal (hours)").grid(row=0, column=0, sticky="w", pady=4)
        sp, _ = self._spin(grid, "goal_hours", 4, 0.5, 24, cast=float, increment=0.5, after=lambda v: self.refresh())
        sp.grid(row=0, column=1, padx=10)
        ttk.Label(grid, text="Auto-pause after idle (minutes, 0 = never)").grid(row=1, column=0, sticky="w", pady=4)
        sp, _ = self._spin(grid, "idle_minutes", 5, 0, 240)
        sp.grid(row=1, column=1, padx=10)
        ttk.Checkbutton(f, text="Start with Windows", variable=self.app.autostart_var,
                        command=self.app.toggle_autostart).pack(anchor="w", pady=8)
        ttk.Label(f, text=f"Data file: {self.app.db_path}", foreground=GRAY).pack(anchor="w", pady=4)
        ttk.Label(f, text="Closing this window keeps the corner icon and hotkey running.",
                  foreground=GRAY).pack(anchor="w")
        ttk.Button(f, text="Quit Focus Tracker", command=self.app.quit).pack(anchor="w", pady=16)

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

    def _selected(self, tree):
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
        self.refresh()

    def delete_session(self):
        sid = self._selected(self.sess_tree)
        if not sid:
            return
        if self.app.session and int(sid) == self.app.session[0]:
            messagebox.showinfo("Delete session", "Pause the running session first.", parent=self.win)
            return
        if messagebox.askyesno("Delete session", "Delete the selected session?", parent=self.win):
            self.app.db.delete_session(int(sid))
            self.app.today_base = self.app.db.seconds_since(self.app.midnight(), int(time.time()))
            self.refresh()

    # ---- live status (every second) ----
    def _live(self):
        a = self.app
        if self.visible():
            if a.session:
                el = time.time() - a.session[1]
                plan_s = a.session[2] * 60
                color = plan_color(el, plan_s)
                self.status.config(text=f"Focusing on {a.project}")
                self.clock.config(text=f"{mmss(el)} / {mmss(plan_s)}", foreground=color)
                frac = min(1.0, el / plan_s)
                self.btn.config(text="Pause")
            else:
                color, frac = GRAY, 0.0
                self.status.config(text="Paused (idle)" if a.idle_paused else "Paused")
                self.clock.config(text=f"00:00 / {mmss(a.plan_min * 60)}", foreground=GRAY)
                self.btn.config(text="Start")
            w = self.bar.winfo_width()
            self.bar.delete("all")
            self.bar.create_rectangle(0, 0, w * frac, 14, fill=color, width=0)
            self.bar.create_line(w / 2, 0, w / 2, 14, fill="white")
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

        self._refresh_summary(info, today)
        self._refresh_home(sessions, info, today, now)
        self._refresh_daily(info, first, today)
        self._refresh_projects(sessions)
        self._refresh_sessions(sessions, since.timestamp())
        if self.tabs.select() == str(self.charts_tab):
            self._draw_charts(info, [s for s in segs if s[0] >= since], first, today)

    def _refresh_summary(self, info, today):
        d = today if info[today]["secs"] else today - timedelta(days=1)
        streak = 0
        while info[d]["secs"] > 0:
            streak += 1
            d -= timedelta(days=1)
        week_start = today - timedelta(days=today.weekday())
        week = sum(v["secs"] for k, v in info.items() if k >= week_start)
        total = sum(v["secs"] for v in info.values())
        self.summary.config(text=f"Today {fmt_hm(info[today]['secs'])}   |   This week {fmt_hm(week)}"
                                 f"   |   Total {fmt_hm(total)}   |   Streak {streak} d")

    def _refresh_home(self, sessions, info, today, now):
        a = self.app
        projects = a.db.projects()
        self.project_box.config(values=projects)
        if self.win.focus_get() is not self.project_box:
            self.project_var.set(a.project)
        if self.win.focus_get() is not self.plan_spin:
            self.plan_var.set(str(a.plan_min))
        goal = float(a.db.get("goal_hours", 4)) * 3600
        t = info[today]
        self.today_label.config(
            text=f"{fmt_hm(t['secs'])} of {fmt_hm(goal)} goal   |   {t['n']} session(s), {t['met']} met the plan")
        midnight = datetime.combine(today, datetime.min.time()).timestamp()
        rows = []
        for sid, project, start, end, plan in reversed(sessions):
            if end < midnight:
                break
            s, e = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
            running = a.session is not None and sid == a.session[0]
            rows.append((str(sid), (s.strftime("%H:%M:%S"), "running" if running else e.strftime("%H:%M:%S"),
                                    fmt_hm(end - start), plan_text(start, end, plan), project)))
        fill(self.today_tree, rows)

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

    def _refresh_sessions(self, sessions, since):
        rows = []
        for sid, project, start, end, plan in reversed(sessions):
            if end < since:
                continue
            s, e = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
            rows.append((str(sid), (s.strftime("%Y-%m-%d"), s.strftime("%H:%M:%S"), e.strftime("%H:%M:%S"),
                                    fmt_hm(end - start), plan_text(start, end, plan), project)))
        fill(self.sess_tree, rows)

    def _draw_charts(self, info, segs, first, today):
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

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".csv",
                                            initialfile="focus_sessions.csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "start", "end", "minutes", "plan_minutes", "plan_met", "project"])
            for _, project, start, end, plan in self.app.db.sessions(int(time.time())):
                s, e = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
                w.writerow([s.date(), s.strftime("%H:%M:%S"), e.strftime("%H:%M:%S"),
                            round((end - start) / 60, 1), plan or "", int(plan_met(start, end, plan)), project])
