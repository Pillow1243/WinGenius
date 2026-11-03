; Inno Setup 6 script — produces dist\WinGenius-Setup.exe
#define AppName "WinGenius"
#define AppVersion "6.0.0"
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

[Languages]
; Inno Setup has no built-in Persian translation. Use the stock English wizard
; (the app itself is Persian-first once it starts); if you ship a Farsi .isl in
; compiler:Languages\, add it here as:
;   Name: "farsi"; MessagesFile: "compiler:Languages\Farsi.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

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
