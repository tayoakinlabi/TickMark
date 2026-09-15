; Inno Setup script for the Tickmark Windows installer (NF 2).
;
; Compile with:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
;
; Run packaging\build.py first — it produces dist\tickmark.exe, which this wraps.
;
; Two decisions worth stating, because both are about trust rather than taste:
;
; 1. **Per-user install by default** (PrivilegesRequiredOverridesAllowed).
;    Installing to Program Files needs an elevation prompt, and an unsigned
;    installer asking for administrator rights is exactly the shape of the thing
;    users should refuse. Tickmark is a read-only CLI; it has no business being
;    elevated. Users who want a machine-wide install can still choose it.
;
; 2. **No auto-update, no telemetry, no network calls of any kind.** The product
;    claim is that nothing leaves the machine. An updater would quietly make that
;    false, and "except for update checks" is the kind of footnote that destroys
;    the claim it qualifies.

#define AppName "Tickmark"
; Passed in by build.py as /DAppVersion=<version>, read from the package itself.
; It was hardcoded once, and the 0.2.0 build quietly produced an installer named
; tickmark-0.1.0-setup.exe wrapping 0.2.0 binaries — a mislabelled release is
; worse than a missing one, because nothing about it looks wrong until someone
; tries to work out which version broke them.
;
; The fallback only exists so this file still compiles when run by hand. It is
; deliberately not a real version number: seeing 0.0.0-unknown in a filename is
; meant to be obviously wrong.
#ifndef AppVersion
  #define AppVersion "0.0.0-unknown"
#endif
#define AppPublisher "Tickmark contributors"
#define AppURL "https://github.com/tayoakinlabi/tickmark"
#define AppExeName "tickmark.exe"

[Setup]
AppId={{B4E1F2A7-6C3D-4A19-9E52-7D0C8F3A1B64}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=tickmark-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Tickmark is a command-line tool; the installer has no reason to be elevated.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "addtopath"; Description: "Add Tickmark to PATH (so 'tickmark' works in any terminal)"; GroupDescription: "Command line:"

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";          DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\LICENSE";            DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Registry]
; Appended to the *user's* PATH, matching the per-user install default. The
; check in NeedsAddPath stops a repeated install from growing PATH each time,
; which is a classic installer bug nobody notices until PATH hits its limit.
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function NeedsAddPath(Param: string): Boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  { Semicolons on both sides so 'C:\Tickmark2' is not mistaken for 'C:\Tickmark'. }
  Result := Pos(';' + Param + ';', ';' + OrigPath + ';') = 0;
end;
