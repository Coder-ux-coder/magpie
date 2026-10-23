; Inno Setup script for MagpieSetup.exe (the "Windows app" workflow builds it):
;   iscc /DAppVersion=0.4.0 packaging\windows\Magpie.iss
; It installs dist\Magpie (made by magpie.spec) for the current user, without asking
; anything: no administrator rights, no pages to click through. Magpie starts as soon
; as it's installed. Running a newer MagpieSetup.exe over it (Magpie does this itself
; to update) keeps the notes and settings, which live in %LOCALAPPDATA%\Magpie.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{7EF48C55-C287-46B1-B896-A24C97968B57}
AppName=Magpie
AppVersion={#AppVersion}
AppVerName=Magpie {#AppVersion}
AppPublisher=Magpie contributors
AppPublisherURL=https://github.com/Coder-ux-coder/magpie
AppSupportURL=https://github.com/Coder-ux-coder/magpie/issues
SourceDir=..\..
OutputDir=dist
OutputBaseFilename=MagpieSetup
SetupIconFile=magpie.ico
UninstallDisplayIcon={app}\Magpie.exe
UninstallDisplayName=Magpie
DefaultDirName={localappdata}\Programs\Magpie
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
DisableFinishedPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=force
RestartApplications=no
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[InstallDelete]
; Files a previous version had and this one doesn't.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "dist\Magpie\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Magpie"; Filename: "{app}\Magpie.exe"; Comment: "Voice-first notes"
Name: "{userdesktop}\Magpie"; Filename: "{app}\Magpie.exe"; Comment: "Voice-first notes"

[Registry]
; Magpie adds itself here to start with Windows (and can be told not to, from its
; tray menu); uninstalling removes it.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "Magpie"; Flags: uninsdeletevalue

[Run]
; Started by hand: open Magpie. Updating itself (silently): back to the tray, quietly.
Filename: "{app}\Magpie.exe"; Flags: nowait; Check: not WizardSilent
Filename: "{app}\Magpie.exe"; Parameters: "--background"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM Magpie.exe"; Flags: runhidden; RunOnceId: "StopMagpie"
