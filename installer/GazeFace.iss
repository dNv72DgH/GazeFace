; GazeFace installer (Inno Setup 6) - standard edition (spec 2-A).
; Built by build_installer.ps1, which passes AppVersion and DistDir.
; See INSTALLER_SPEC.md. This installer never creates certificates,
; touches certificate stores or signs anything (spec chapter 1).

#ifndef AppVersion
  #error AppVersion is not defined. Use build_installer.ps1.
#endif
#ifndef DistDir
  #error DistDir is not defined. Use build_installer.ps1.
#endif

#define AccKey "SOFTWARE\Microsoft\Windows NT\CurrentVersion\Accessibility"
#define AtKey AccKey + "\ATs\gazeface"

[Setup]
AppId={{CADE7311-DD86-4EA4-AD72-4C7833CBBC9E}
AppName=GazeFace
AppVersion={#AppVersion}
AppVerName=GazeFace {#AppVersion}
AppPublisher=GazeFace
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\GazeFace
DisableDirPage=yes
DefaultGroupName=GazeFace
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SetupIconFile=..\assets\images\icon.ico
UninstallDisplayIcon={app}\GazeFace.exe
UninstallDisplayName=GazeFace
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
WizardSizePercent=120
CloseApplications=no
RestartApplications=no
ShowLanguageDialog=yes
LanguageDetectionMethod=none
OutputDir=Output
OutputBaseFilename=GazeFace-Setup-{#AppVersion}

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"

[CustomMessages]
en.LaunchApp=Start GazeFace
ja.LaunchApp=GazeFace を起動する
en.StopAsk=GazeFace is running. Close it to continue?
ja.StopAsk=GazeFace が実行中です。終了して続けますか？
en.StopFailed=Please close GazeFace, then run this again.
ja.StopFailed=GazeFace を終了してから、もう一度実行してください。
en.DeleteSettings=Also delete your GazeFace settings (profiles, gestures, calibration, ...)?%n%nIf you keep them, they are used again when you reinstall GazeFace.
ja.DeleteSettings=GazeFace の設定（プロファイル、ジェスチャー、キャリブレーションなど）も削除しますか？%n%n残しておくと、もう一度インストールしたときにそのまま使えます。
en.DoneStandard=GazeFace has been installed.%n%nLimits of the standard edition%nBecause of how Windows permissions work, the standard edition of GazeFace cannot operate windows that run as administrator, such as Task Manager, some Settings pages, app installers and User Account Control prompts.%nWhile such a window is in front, the pointer may stop moving or clicks may stop working. Update notices and installers can open on their own, so this can happen at unexpected moments. GazeFace also cannot be used on the sign-in screen.%nIf you cannot use a mouse or keyboard, please have help from family or a caregiver, or Windows Voice Access, ready for these situations.
ja.DoneStandard=GazeFace のインストールが完了しました。%n%n通常版の制限について%n通常版の GazeFace は、Windows の権限の仕組みのため、管理者として実行されているウィンドウを操作できません。たとえば、タスク マネージャー、一部の設定画面、アプリのインストーラー、「ユーザー アカウント制御」の確認画面などです。%nこうしたウィンドウが前面に出ると、その間はポインターが動かなくなったり、クリックが効かなくなったりします。アップデートの通知やインストーラーなどが自動で開く場合もあるため、予期しないタイミングで操作できなくなることがあります。サインイン画面でも GazeFace は使えません。%nマウスやキーボードを使えない方は、こうした場面に備えて、家族や介助者の方の手助けや、Windows の音声アクセスを用意しておいてください。

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#DistDir}\GazeFace\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Updater: an elevated scheduled task the app can start without a UAC
; prompt, which the standard edition could not operate (updater\*.ps1).
Source: "updater\gazeface_update.ps1"; DestDir: "{app}\updater"; Flags: ignoreversion
Source: "updater\register_task.ps1"; DestDir: "{app}\updater"; Flags: ignoreversion

[Icons]
Name: "{group}\GazeFace"; Filename: "{app}\GazeFace.exe"
Name: "{autodesktop}\GazeFace"; Filename: "{app}\GazeFace.exe"; Tasks: desktopicon

; Assistive-technology registration (spec 3-6). Registering starts
; nothing; the app's Startup settings decide. Same values as
; src\at_startup.py admin_script().
[Registry]
Root: HKLM64; Subkey: "{#AtKey}"; Flags: uninsdeletekey
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: string; ValueName: "ApplicationName"; ValueData: "GazeFace"
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: string; ValueName: "Description"; ValueData: "Face gesture and gaze mouse control"
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: string; ValueName: "ATExe"; ValueData: "GazeFace_UI.exe"
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: string; ValueName: "StartExe"; ValueData: "{app}\GazeFace_UI.exe"
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: string; ValueName: "StartParams"; ValueData: "--at"
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: string; ValueName: "Profile"; ValueData: "<HCIModel><Accommodation type=""severe dexterity"" /><Accommodation type=""mild dexterity"" /></HCIModel>"
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: string; ValueName: "SimpleProfile"; ValueData: "gazeface"
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: dword; ValueName: "TerminateOnDesktopSwitch"; ValueData: 0
Root: HKLM64; Subkey: "{#AtKey}"; ValueType: dword; ValueName: "CopySettingsToLockedDesktop"; ValueData: 0

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\updater\register_task.ps1"""; Flags: runhidden waituntilterminated
; runasoriginaluser: without it GazeFace would start elevated and behave
; differently.
Filename: "{app}\GazeFace.exe"; Description: "{cm:LaunchApp}"; Flags: postinstall nowait skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\updater\register_task.ps1"" -Remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveUpdateTask"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  SubExe = 'gazeface_sub.exe';

{ ---- running GazeFace (spec 3-3 / 4-1) ---- }

{ PIDs of GazeFace*.exe started from the app folder. Which: 0 = all,
  1 = the watchdog (GazeFace_Sub.exe) only, 2 = everything else. }
function RunningPids(Which: Integer): TArrayOfString;
var
  Locator, Service, Procs, P: Variant;
  I, N, Pid: Integer;
  Prefix, Path, Name: String;
begin
  SetArrayLength(Result, 0);
  Prefix := Lowercase(AddBackslash(ExpandConstant('{app}')));
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('.', 'root\CIMV2');
    Procs := Service.ExecQuery('SELECT ProcessId, Name, ExecutablePath ' +
      'FROM Win32_Process WHERE Name LIKE ''GazeFace%''');
    for I := 0 to Procs.Count - 1 do
    begin
      P := Procs.ItemIndex(I);
      if VarIsNull(P.ExecutablePath) then
        Continue;
      Path := P.ExecutablePath;
      Path := Lowercase(Path);
      Name := P.Name;
      Name := Lowercase(Name);
      if Pos(Prefix, Path) <> 1 then
        Continue;
      if (Which = 1) and (Name <> SubExe) then
        Continue;
      if (Which = 2) and (Name = SubExe) then
        Continue;
      N := GetArrayLength(Result);
      SetArrayLength(Result, N + 1);
      Pid := P.ProcessId;
      Result[N] := IntToStr(Pid);
    end;
  except
    Log('Process query failed: ' + GetExceptionMessage);
  end;
end;

procedure KillPids(Pids: TArrayOfString);
var
  I, RC: Integer;
begin
  for I := 0 to GetArrayLength(Pids) - 1 do
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /PID ' + Pids[I], '',
         SW_HIDE, ewWaitUntilTerminated, RC);
end;

{ Ask (unless silent), then stop: the watchdog FIRST, or it would restart
  the main app at once. True when nothing is left running. }
function StopRunning(Silent: Boolean): Boolean;
var
  I: Integer;
begin
  Result := True;
  if GetArrayLength(RunningPids(0)) = 0 then
    Exit;
  if not Silent then
    if MsgBox(CustomMessage('StopAsk'), mbConfirmation, MB_YESNO) <> IDYES then
    begin
      Result := False;
      Exit;
    end;
  KillPids(RunningPids(1));
  KillPids(RunningPids(2));
  for I := 1 to 20 do
  begin
    if GetArrayLength(RunningPids(0)) = 0 then
      Exit;
    Sleep(500);
  end;
  Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not StopRunning(WizardSilent) then
    Result := CustomMessage('StopFailed');
end;

{ ---- install ---- }

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { The chosen language becomes the app's first-run language (spec 5).
    ASCII only, so no BOM is written. }
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\install_defaults.json'),
                     '{"language": "' + ActiveLanguage + '"}', False);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
  begin
    WizardForm.FinishedLabel.Caption := CustomMessage('DoneStandard');
    WizardForm.FinishedLabel.AdjustHeight;
    WizardForm.RunList.Top := WizardForm.FinishedLabel.Top +
      WizardForm.FinishedLabel.Height + ScaleY(12);
  end;
end;

{ ---- uninstall ---- }

function InitializeUninstall(): Boolean;
begin
  Result := StopRunning(UninstallSilent);
  if (not Result) and (not UninstallSilent) then
    MsgBox(CustomMessage('StopFailed'), mbError, MB_OK);
end;

{ Drop 'gazeface' from the shared, comma-separated Configuration list,
  keeping every other AT in its order (spec 4-2). }
procedure RemoveFromConfiguration(RootKey: Integer);
var
  V, Item, OutS: String;
  P: Integer;
begin
  if not RegQueryStringValue(RootKey, '{#AccKey}', 'Configuration', V) then
    Exit;
  OutS := '';
  V := V + ',';
  while Length(V) > 0 do
  begin
    P := Pos(',', V);
    Item := Trim(Copy(V, 1, P - 1));
    Delete(V, 1, P);
    if (Item = '') or (CompareText(Item, 'gazeface') = 0) then
      Continue;
    if OutS <> '' then
      OutS := OutS + ',';
    OutS := OutS + Item;
  end;
  RegWriteStringValue(RootKey, '{#AccKey}', 'Configuration', OutS);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    RemoveFromConfiguration(HKLM64);
    RemoveFromConfiguration(HKCU);
  end;
  if CurUninstallStep = usPostUninstall then
    if not UninstallSilent then
      if MsgBox(CustomMessage('DeleteSettings'), mbConfirmation,
                MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(ExpandConstant('{localappdata}\GazeFace'), True, True, True);
        DelTree(ExpandConstant('{commonappdata}\GazeFace'), True, True, True);
      end;
end;
