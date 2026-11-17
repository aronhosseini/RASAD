#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{B7D1E3A2-5C44-4C6E-9F0A-52A1D0C7A001}
AppName=RASAD
AppVersion={#MyAppVersion}
AppPublisher=RASAD
DefaultDirName={autopf}\RASAD
DefaultGroupName=RASAD
OutputDir=..\installer_output
OutputBaseFilename=RASAD-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\RASAD.exe
WizardStyle=modern
SetupIconFile=..\assets\rasad.ico

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "..\dist\RASAD.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\RASAD"; Filename: "{app}\RASAD.exe"
Name: "{autodesktop}\RASAD"; Filename: "{app}\RASAD.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RASAD.exe"; Description: "Launch RASAD"; Flags: nowait postinstall skipifsilent
