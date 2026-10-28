[Setup]
AppName=Focus Tracker
AppVersion=1.0.0
AppPublisher=mnoorchenar
DefaultDirName={autopf}\Focus Tracker
DefaultGroupName=Focus Tracker
PrivilegesRequired=lowest
OutputDir=installer
OutputBaseFilename=FocusTracker-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\FocusTracker.exe
Compression=lzma2
SolidCompression=yes
DisableProgramGroupPage=yes
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "autostart"; Description: "Start Focus Tracker when Windows starts"

[Files]
Source: "dist\FocusTracker.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Focus Tracker"; Filename: "{app}\FocusTracker.exe"
Name: "{autodesktop}\Focus Tracker"; Filename: "{app}\FocusTracker.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "FocusTracker"; ValueData: """{app}\FocusTracker.exe"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\FocusTracker.exe"; Description: "Launch Focus Tracker"; Flags: nowait postinstall skipifsilent
