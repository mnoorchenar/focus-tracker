"""Focus Tracker: main window + tiny always-on-top corner icon + global hotkey to start/pause."""
import ctypes
import sys
import threading
import time
import tkinter as tk
import winreg
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import keyboard
from PIL import Image, ImageDraw, ImageTk

from db import DB, DB_PATH
from mainwindow import GRAY, GREEN, MainWindow, plan_color

DISC, TRACK, KEY, SIZE = "#1c1c1e", "#3a3a3c", "#010101", 92  # KEY = transparent corner color
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
HEARTBEAT_S = 30
ERROR_ALREADY_EXISTS = 183

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateMutexW.restype = wintypes.HANDLE
k32.CreateEventW.restype = wintypes.HANDLE
k32.SetEvent.argtypes = [wintypes.HANDLE]
k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds():
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return ((k32.GetTickCount() & 0xFFFFFFFF) - info.dwTime) / 1000


def work_area():
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return rect


def app_icon(size=64):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((1, 1, size - 2, size - 2), fill=DISC)
    pad, w = size * 0.16, max(2, size // 9)
    d.arc((pad, pad, size - pad, size - pad), -90, 200, fill=GREEN, width=w)
    return im


class App:
    def __init__(self, minimized=False):
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        self.db = DB()
        self.db_path = DB_PATH
        self.root = tk.Tk()
        self.icon = ImageTk.PhotoImage(app_icon())
        self.root.iconphoto(True, self.icon)
        self.root.protocol("WM_DELETE_WINDOW", self.root.withdraw)  # hide; icon + hotkey keep running

        self.project = self.db.get("project", "")
        self.plan_min = int(float(self.db.get("plan_min", 20)))
        self.hotkey = self.db.get("hotkey", "ctrl+alt+f")
        self.hotkey_var = tk.StringVar(value=self.hotkey)
        self.hotkey_handle = None
        self.session = None  # (id, start_ts, plan_min) while running
        self.idle_paused = False
        self.today_base = self.db.seconds_since(self.midnight(), int(time.time()))
        self.last_beat = 0
        self.autostart_var = tk.BooleanVar(value=self.autostart_enabled())

        self.build_overlay()
        self.main = MainWindow(self)
        if minimized:
            self.root.withdraw()
        self.register_hotkey()
        self.tick()

    @staticmethod
    def midnight():
        return int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    # ---- corner icon ----
    def build_overlay(self):
        ov = self.ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True, "-alpha", 0.6, "-transparentcolor", KEY)
        ov.configure(bg=KEY)
        self.canvas = c = tk.Canvas(ov, width=SIZE, height=SIZE, bg=KEY, highlightthickness=0)
        c.pack()
        pad, ring = 4, 7
        c.create_oval(pad, pad, SIZE - pad, SIZE - pad, fill=DISC, outline="")
        box = (pad + ring, pad + ring, SIZE - pad - ring, SIZE - pad - ring)
        c.create_oval(*box, outline=TRACK, width=5)
        self.arc = c.create_arc(*box, start=90, extent=0, style="arc", outline=GRAY, width=5)
        self.label = c.create_text(SIZE / 2, SIZE / 2 - 8, fill="white", font=("Segoe UI Semibold", 20), text="0")
        self.unit = c.create_text(SIZE / 2, SIZE / 2 + 18, fill=GRAY, font=("Segoe UI", 8), text="TODAY")
        c.bind("<Button-1>", lambda e: self.toggle())
        c.bind("<Button-3>", self.show_menu)
        ov.bind("<Enter>", lambda e: ov.attributes("-alpha", 0.95))
        ov.bind("<Leave>", lambda e: ov.attributes("-alpha", 0.6))
        ov.update_idletasks()
        r = work_area()
        ov.geometry(f"+{r.right - SIZE - 10}+{r.bottom - SIZE - 10}")
        self.menu = tk.Menu(ov, tearoff=0)

    def show_menu(self, event):
        m = self.menu
        m.delete(0, "end")
        m.add_command(label="Open window", command=self.show_window)
        m.add_command(label="Pause" if self.session else "Start", command=self.toggle)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        m.tk_popup(event.x_root, event.y_root)

    def render(self):
        now = time.time()
        if self.session:
            el, plan_s = now - self.session[1], self.session[2] * 60
            color, minutes, progress = plan_color(el, plan_s), int(el // 60), el / plan_s
            unit = f"/ {self.session[2]} MIN" if el < plan_s else "GREAT!"
        else:
            goal = float(self.db.get("goal_hours", 4)) * 3600
            color, minutes, progress = GRAY, int(self.today_base // 60), self.today_base / goal
            unit = "IDLE" if self.idle_paused else "TODAY"
        c = self.canvas
        c.itemconfig(self.arc, outline=color, extent=-min(359.9, 360 * progress))
        c.itemconfig(self.label, text=str(minutes), font=("Segoe UI Semibold", 20 if minutes < 1000 else 15))
        c.itemconfig(self.unit, text=unit, fill=color)

    # ---- window ----
    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)  # Windows may refuse focus to background apps
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        self.main.refresh()

    # ---- timing ----
    def toggle(self):
        if self.session:
            self.pause()
        else:
            self.start()

    def start(self):
        if not self.project:
            self.show_window()
            self.main.project_box.focus_set()
            messagebox.showinfo("Focus Tracker", "Choose or type a project first, then press Start.",
                                parent=self.root)
            return
        now = int(time.time())
        self.today_base = self.db.seconds_since(self.midnight(), now)
        self.session = (self.db.start(self.project, now, self.plan_min), now, self.plan_min)
        self.idle_paused = False
        self.last_beat = now
        self.render()

    def pause(self, end_ts=None):
        if not self.session:
            return
        sid, start, _ = self.session
        self.db.stop(sid, max(end_ts or int(time.time()), start))
        self.session = None
        self.today_base = self.db.seconds_since(self.midnight(), int(time.time()))
        self.render()

    def refresh_today(self):
        """Re-read today's total after sessions were edited, deleted or imported."""
        self.today_base = self.db.seconds_since(self.midnight(), int(time.time()))
        self.render()

    def set_project(self, name):
        running = self.session is not None
        if running:
            self.pause()
        self.project = name
        self.db.set("project", name)
        self.db.add_project(name)
        if running:
            self.start()

    def set_plan(self, minutes):
        """New plan applies to the next session and to the running one."""
        self.plan_min = int(minutes)
        self.db.set("plan_min", self.plan_min)
        if self.session:
            self.session = (self.session[0], self.session[1], self.plan_min)
            self.db.set_plan(self.session[0], self.plan_min)

    def tick(self):
        now = int(time.time())
        if self.session:
            limit = float(self.db.get("idle_minutes", 5)) * 60
            idle = idle_seconds()
            if limit and idle > limit:
                self.idle_paused = True
                self.pause(end_ts=int(now - idle))
            elif now - self.last_beat >= HEARTBEAT_S:
                self.db.heartbeat(self.session[0], now)
                self.last_beat = now
        if k32.WaitForSingleObject(OPEN_EVENT, 0) == 0:  # a second launch asked us to show the window
            self.show_window()
        self.render()
        self.root.after(1000, self.tick)

    # ---- hotkey ----
    def register_hotkey(self):
        if self.hotkey_handle is not None:
            keyboard.remove_hotkey(self.hotkey_handle)
            self.hotkey_handle = None
        try:
            # suppress=True keeps Windows from also acting on it (e.g. launching Copilot)
            self.hotkey_handle = keyboard.add_hotkey(
                self.hotkey, lambda: self.root.after(0, self.toggle), suppress=True)
        except (ValueError, KeyError) as e:
            messagebox.showerror("Hotkey", f"Cannot use hotkey '{self.hotkey}': {e}", parent=self.root)

    def capture_hotkey(self):
        if self.hotkey_handle is not None:
            keyboard.remove_hotkey(self.hotkey_handle)
            self.hotkey_handle = None
        win = tk.Toplevel(self.root)
        win.title("Set hotkey")
        win.attributes("-topmost", True)
        ttk.Label(win, text="Press the key or combination you want to use...", padding=24).pack()
        win.update_idletasks()
        win.geometry(f"+{self.root.winfo_screenwidth() // 2 - 180}+{self.root.winfo_screenheight() // 2 - 50}")

        def worker():
            try:
                combo = keyboard.read_hotkey(suppress=False)
            except Exception:
                combo = None
            self.root.after(0, done, combo)

        def done(combo):
            win.destroy()
            if combo:
                self.hotkey = combo
                self.hotkey_var.set(combo)
                self.db.set("hotkey", combo)
            self.register_hotkey()

        threading.Thread(target=worker, daemon=True).start()

    # ---- settings ----
    @staticmethod
    def autostart_enabled():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
                winreg.QueryValueEx(k, "FocusTracker")
                return True
        except OSError:
            return False

    def toggle_autostart(self):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if self.autostart_var.get():
                if getattr(sys, "frozen", False):
                    cmd = f'"{sys.executable}"'
                else:
                    cmd = f'"{Path(sys.executable).with_name("pythonw.exe")}" "{Path(__file__).resolve()}"'
                winreg.SetValueEx(k, "FocusTracker", 0, winreg.REG_SZ, cmd + " --minimized")
            else:
                winreg.DeleteValue(k, "FocusTracker")

    def quit(self):
        self.pause()
        keyboard.unhook_all()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def single_instance():
    """True if we are the first copy; otherwise ask the running copy to show its window."""
    global OPEN_EVENT
    k32.CreateMutexW(None, False, "FocusTrackerSingleInstance")
    exists = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    OPEN_EVENT = k32.CreateEventW(None, False, False, "FocusTrackerOpenWindow")
    if exists:
        k32.SetEvent(OPEN_EVENT)
    return not exists


if __name__ == "__main__":
    if single_instance():
        App(minimized="--minimized" in sys.argv).run()
