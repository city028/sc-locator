; The installer around PyInstaller's dist\sc-locator folder. Build with
; build.ps1, which passes the version from updater.py.
; Per-user: no admin prompt, and an update can run without one too.

#ifndef AppVersion
  #error Build with build.ps1, which sets AppVersion
#endif

#define AppName "SC Locator"
#define AppExe "sc-locator.exe"
#define AppMutexName "Local\sc-locator-overlay"

[Setup]
; Never change AppId: it is what makes a new version an upgrade of the old one.
AppId={{6E2B7C14-3F1A-4D58-9C0E-7A41D2F5B863}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Citygamer
AppPublisherURL=https://github.com/city028/sc-locator
DefaultDirName={localappdata}\Programs\SC Locator
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
AppMutex={#AppMutexName}
CloseApplications=no
OutputDir=..\dist
OutputBaseFilename=sc-locator-setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[InstallDelete]
; Files a previous version shipped and this one does not. Your data is not
; here: it is in %LocalAppData%\SC Locator, which nothing in this file touches.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\sc-locator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Without skipifsilent, so an update starts the new version again too.
Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Description: "Start {#AppName}"; Flags: nowait postinstall

[Code]
// An update is started by the running program, which then saves and closes.
// Give it time to go, or AppMutex would find it still running and give up.
function InitializeSetup(): Boolean;
var
  Waited: Integer;
begin
  Waited := 0;
  if WizardSilent then
    while CheckForMutexes('{#AppMutexName}') and (Waited < 30000) do
    begin
      Sleep(250);
      Waited := Waited + 250;
    end;
  Result := True;
end;
