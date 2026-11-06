; Inno Setup 6 script — produces dist\WinGenius-Setup.exe
#define AppName "WinGenius"
#define AppVersion "7.1.0"
#define AppExe "WinGenius.exe"

[Setup]
AppId={{9B1F3E42-6C7A-4B8E-9E4D-2A77C1D0F3A1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Pillow1243
AppPublisherURL=https://github.com/Pillow1243/WinGenius
AppSupportURL=https://github.com/Pillow1243/WinGenius/issues
AppUpdatesURL=https://github.com/Pillow1243/WinGenius/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\dist
OutputBaseFilename=WinGenius-Setup-{#AppVersion}
LicenseFile=..\LICENSE
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
PrivilegesRequiredOverridesAllowed=dialog commandline
CloseApplications=yes
CloseApplicationsFilter=WinGenius.exe,WinGenius-onedir.exe
RestartApplications=yes

[Languages]
; Inno Setup has no built-in Persian translation. Use the stock English wizard
; (the app itself is Persian-first once it starts); if you ship a Farsi .isl in
; compiler:Languages\, add it here as:
;   Name: "farsi"; MessagesFile: "compiler:Languages\Farsi.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

; Uninstalling used to leave settings, the journal, the search index and vault
; snapshots behind in %APPDATA%\WinGenius.  Removing them is a choice, not a
; surprise: it is unchecked and only applies to the app's own data folder.
Name: "deletedata"; Description: "Also delete my WinGenius settings, history and index"; GroupDescription: "Cleanup:"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only with the explicit "deletedata" task above.
Type: filesandordirs; Name: "{userappdata}\{#AppName}\logs"; Tasks: deletedata
Type: filesandordirs; Name: "{userappdata}\{#AppName}\cache"; Tasks: deletedata
Type: filesandordirs; Name: "{userappdata}\{#AppName}\journal_snapshots"; Tasks: deletedata
Type: filesandordirs; Name: "{userappdata}\{#AppName}\trash"; Tasks: deletedata
Type: filesandordirs; Name: "{userappdata}\{#AppName}\updates"; Tasks: deletedata
; The files WinGenius creates directly in its data folder (settings, journal,
; queue, history, workflows, index).  Written as an explicit list so an unknown
; folder a user put there by hand is never touched.
Type: files; Name: "{userappdata}\{#AppName}\settings.ini"; Tasks: deletedata
Type: files; Name: "{userappdata}\{#AppName}\journal.sqlite3"; Tasks: deletedata
Type: files; Name: "{userappdata}\{#AppName}\history.json"; Tasks: deletedata
Type: files; Name: "{userappdata}\{#AppName}\queue.json"; Tasks: deletedata
Type: files; Name: "{userappdata}\{#AppName}\workflows.json"; Tasks: deletedata
Type: files; Name: "{userappdata}\{#AppName}\schedules.json"; Tasks: deletedata
Type: files; Name: "{userappdata}\{#AppName}\watch_rules.json"; Tasks: deletedata
Type: files; Name: "{userappdata}\{#AppName}\index.sqlite3"; Tasks: deletedata
Type: dirifempty; Name: "{userappdata}\{#AppName}"; Tasks: deletedata
