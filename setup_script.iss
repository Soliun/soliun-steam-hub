[Setup]
AppId={{8E4B2F91-7C3A-4D12-9B85-6A1F3E90C4D2}
AppName=Soliun Steam Hub
AppVersion=0.4.1
AppPublisher=Soliun
AppPublisherURL=https://github.com/soliun
DefaultDirName={autopf}\SoliunSteamHub
DefaultGroupName=Soliun Steam Hub
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=SoliunSteamHub_Setup_v0.4.1
SetupIconFile=icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\SoliunSteamHub\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Soliun Steam Hub"; Filename: "{app}\SoliunSteamHub.exe"; IconFilename: "{app}\SoliunSteamHub.exe"
Name: "{group}\Desinstalar Soliun Steam Hub"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Soliun Steam Hub"; Filename: "{app}\SoliunSteamHub.exe"; IconFilename: "{app}\SoliunSteamHub.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SoliunSteamHub.exe"; Description: "{cm:LaunchProgram,Soliun Steam Hub}"; Flags: nowait postinstall skipifsilent
