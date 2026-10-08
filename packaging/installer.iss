; Inno Setup script: SAR Manifest ground station installer (spec 4.5).
; Build dist\SARManifest first (packaging\ground_station.spec), then compile this with
; Inno Setup 6:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
;
; Put srilanka.pmtiles next to this .iss file to ship the offline map in the installer.

#define AppName "SAR Manifest"
#define AppVersion "0.1.0"
#define Port "8000"

[Setup]
AppId={{6E5C1A0B-2C7F-4C3E-9C57-0B8E2A4F9D11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=University of Moratuwa - SAR UAV FYP
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir=..\dist
OutputBaseFilename=SARManifest-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
; Admin is needed for the firewall rule.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName} ground station

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\SARManifest\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "srilanka.pmtiles"; DestDir: "{commonappdata}\SARManifest\maps"; Flags: external skipifsourcedoesntexist

[Dirs]
Name: "{commonappdata}\SARManifest\maps"; Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\SARManifest.exe"
Name: "{group}\{#AppName} (server only)"; Filename: "{app}\SARManifest.exe"; Parameters: "--headless"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\SARManifest.exe"; Tasks: desktopicon

[Run]
; Phones reach the laptop on the private SAR-NET LAN only.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SAR Manifest"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SAR Manifest"" dir=in action=allow protocol=TCP localport={#Port} profile=private"; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""SAR Manifest mDNS"" dir=in action=allow protocol=UDP localport=5353 profile=private"; Flags: runhidden
Filename: "{app}\SARManifest.exe"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SAR Manifest"""; Flags: runhidden; RunOnceId: "fwrule"
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""SAR Manifest mDNS"""; Flags: runhidden; RunOnceId: "fwrule-mdns"
