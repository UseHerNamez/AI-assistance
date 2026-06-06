#define MyAppName "Assistance"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "Assistance"
#define MyAppURL "https://github.com/"
#define MyAppLauncher "launch_assistance.vbs"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Assistance
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist\online
OutputBaseFilename=Assistance-Setup
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
Name: "llm"; Description: "Install smart local LLM (large download, optional)"; GroupDescription: "Optional:"
Name: "startup"; Description: "Start Assistance when Windows starts (recommended)"; GroupDescription: "Optional:"; Flags: checkedonce

[Files]
Source: "dist\.build\online-stage\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\{#MyAppLauncher}"""; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\{#MyAppLauncher}"""; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\scripts\setup_runtime.ps1"" -InstallDir ""{app}"" {code:GetSetupFlags}"; StatusMsg: "Downloading and configuring Assistance (this may take a few minutes)..."; Flags: waituntilterminated runhidden
Filename: "{sys}\wscript.exe"; Parameters: """{app}\{#MyAppLauncher}"""; Description: "Launch {#MyAppName} now"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\runtime"

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""Remove-Item -LiteralPath ([Environment]::GetFolderPath('Startup') + '\Assistance.lnk') -Force -ErrorAction SilentlyContinue"""; Flags: runhidden

[Code]
function BoolToSwitch(Selected: Boolean; Name: String): String;
begin
  if Selected then
    Result := Name
  else
    Result := '';
end;

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

function InitializeSetup(): Boolean;
begin
  Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { Icons are created after setup_runtime finishes; runtime python appears during [Run]. }
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  NeedsRestart := False;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := 'Assistance will be installed to:' + NewLine + Space + MemoDirInfo + NewLine + NewLine +
    'During setup, the installer will automatically:' + NewLine + Space +
    '- Download a private Python runtime (no Python install needed)' + NewLine + Space +
    '- Install required libraries' + NewLine + Space +
    '- Download the speech recognition model (~40 MB)' + NewLine + NewLine +
    MemoTasksInfo;
end;
