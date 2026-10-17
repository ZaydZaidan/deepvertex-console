; DeepVertex Marine Console — Windows installer (Inno Setup 6).
; - License page = End-User Terms (must "I accept" to continue).
; - Install directory page with a DEFAULT folder (user may change it).
; - Per-user install by default (no admin); admin mode available in the dialog.
; - Creates Start Menu entry + optional desktop shortcut + uninstaller.
;
; Build (from the repository root, after PyInstaller wrote dist\):
;   & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DMyAppVersion=1.0.5 ^
;       install\windows\deepvertex.iss
; CI generates install\windows\license_win.txt (UTF-8 with BOM) from
; docs/END-USER-TERMS.txt before compiling.

#define MyAppName "DeepVertex Marine Technology Console"
#define MyAppShort "DeepVertex Marine Console"
#define MyAppPublisher "DeepVertex Marine Technology"
#define MyAppURL "https://github.com/ZaydZaidan/deepvertex-console"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{8E3D1C4A-9B7F-4C62-A1E5-2F6C9D41B7A3}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppShort}
DefaultGroupName={#MyAppShort}
LicenseFile=license_win.txt
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir=..\..\dist
OutputBaseFilename=DeepVertexMarineConsole-Setup-{#MyAppVersion}
UninstallDisplayIcon={app}\DeepVertexMarineConsole.exe
UninstallDisplayName={#MyAppName}
SetupIconFile=..\..\gui_t1\ikon\deepvertex-app-icon.ico
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Dirs]
Name: "{userdocs}\\DeepVertex"

[Files]
Source: "..\..\dist\DeepVertexMarineConsole.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\DeepVMDebug.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\..\docs\END-USER-TERMS.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppShort}"; Filename: "{app}\DeepVertexMarineConsole.exe"
Name: "{group}\Troubleshooting (debug build)"; Filename: "{app}\DeepVMDebug.exe"
Name: "{group}\End-User Terms"; Filename: "{app}\END-USER-TERMS.txt"
Name: "{autodesktop}\{#MyAppShort}"; Filename: "{app}\DeepVertexMarineConsole.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\DeepVertexMarineConsole.exe"; Description: "Launch {#MyAppShort}"; Flags: nowait postinstall skipifsilent
