# Focus Tracker

Windows app that times your focus sessions per project, against a plan, and shows where your time goes.

- **Corner icon** (bottom-right, semi-transparent): big minutes number inside a progress ring.
  While focusing, the ring fills toward your session plan (default 20 min, changeable):
  red below 50%, orange up to the plan, green with "GREAT!" once the plan is passed.
  Paused, it shows today's minutes and progress toward the daily goal.
- **Hotkey** toggles start/pause. Set any key or combination in the main window (the Copilot key works; it is suppressed so Windows will not open Copilot).
- **Main window** (launch the exe again, or right-click the icon > Open window): Home (live timer, project, plan, hotkey, stat cards, and the session list where you can add, edit - project, start, end, plan - or delete sessions), Daily, Projects (add / rename / delete), Charts, Settings (daily goal, idle auto-pause, start with Windows).
- **Import / export CSV** from the top bar. Import skips sessions that already exist, so you can safely re-import a backup.
- Closing the window keeps the icon and hotkey running; quit from Settings or the icon's right-click menu.
- Data: SQLite at `%APPDATA%\FocusTracker\focus.db` (project, start, end, plan per session). Crash-safe via a 30 s heartbeat. CSV export included.

## Run / build

```
pip install -r requirements.txt
python app.py
build.bat            # creates dist\FocusTracker.exe (no console window)
# installer: compile installer.iss with Inno Setup -> installer\FocusTracker-Setup.exe
```

Windows 10/11, Python 3.10+.
