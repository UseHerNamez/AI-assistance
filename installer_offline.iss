#define MyAppName "Assistance"
#define MyAppVersion "0.1.1"
#define MyAppPublisher "Assistance"
#define MyAppLauncher "launch_assistance.vbs"

[Setup]
AppId={{B2C3D4E5-F6A7-8901-BCDE-F12345678901}
AppName={#MyAppName} (Offline)
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Assistance
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist\offline
OutputBaseFilename=Assistance-Offline-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "llm"; Description: "Install bundled smart local LLM (only if included in this package)"; GroupDescription: "Optional:"
Name: "startup"; Description: "Start Assistance when Windows starts (recommended)"; GroupDescription: "Optional:"; Flags: checkedonce

[Files]
Source: "dist\.build\offline-stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\{#MyAppLauncher}"""; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\{#MyAppLauncher}"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\setup_runtime_offline.ps1"" -InstallDir ""{app}"" {code:GetSetupFlags}"; StatusMsg: "Installing Assistance (offline, no downloads)..."; Flags: waituntilterminated runhidden
Filename: "{sys}\wscript.exe"; Parameters: """{app}\{#MyAppLauncher}"""; Description: "Launch {#MyAppName} now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Remove-Item -LiteralPath ([Environment]::GetFolderPath('Startup') + '\Assistance.lnk') -Force -ErrorAction SilentlyContinue"""; Flags: runhidden

[Code]
function GetSetupFlags(Param: String): String;
var
  Flags: String;
begin
  Flags := '-Silent';
  if WizardIsTaskSelected('llm') then
    Flags := Flags + ' -IncludeLLM';
  if not WizardIsTaskSelected('startup') then
    Flags := Flags + ' -SkipStartup';
  Result := Flags;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'Offline install — no internet required.' + NewLine + NewLine +
    'Assistance will be installed to:' + NewLine + Space + MemoDirInfo + NewLine + NewLine +
    'Everything is bundled: Python, libraries, and speech model.' + NewLine + NewLine +
    MemoTasksInfo;
end;
