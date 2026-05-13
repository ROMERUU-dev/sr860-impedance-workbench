#define MyAppName "SR860 Impedance Workbench"
#define MyAppVersion "0.1.1"
#define MyAppPublisher "ROMERUU-dev"
#define MyAppURL "https://github.com/ROMERUU-dev/sr860-impedance-workbench"
#define MyAppExeName "SR860_Impedance_Workbench.exe"

[Setup]
AppId={{7D2FBB25-3B7A-4A55-8F7D-8E6E7F130860}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=
PrivilegesRequired=lowest
OutputDir=dist_installer
OutputBaseFilename=SR860_Impedance_Workbench_Setup_v0.1.1
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\srs-1.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
