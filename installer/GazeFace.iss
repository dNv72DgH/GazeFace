; GazeFace installer (Inno Setup 6) - full / standard edition, chosen on
; a wizard page. Built by build_installer.ps1, which passes AppVersion and
; DistDir. The installer itself never creates certificates, touches
; certificate stores or signs anything; the full edition runs
; custom\full_edition.ps1 elevated after
; the install, and whatever it does is up to that script.
; User-written parts: custom\texts.iss (option descriptions, finish
; notes) and custom\full_edition.ps1.

#ifndef AppVersion
  #error AppVersion is not defined. Use build_installer.ps1.
#endif
#ifndef DistDir
  #error DistDir is not defined. Use build_installer.ps1.
#endif
#ifndef DriverPkg
  #error DriverPkg is not defined. Use build_installer.ps1.
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
en.AfterInstall=When the installation is finished:
ja.AfterInstall=インストール完了後:
en.LaunchApp=Start GazeFace
ja.LaunchApp=GazeFace を起動する
en.StopAsk=GazeFace is running. Close it to continue?
ja.StopAsk=GazeFace が実行中です。終了して続けますか？
en.StopFailed=Please close GazeFace, then run this again.
ja.StopFailed=GazeFace を終了してから、もう一度実行してください。
en.DeleteSettings=Also delete your GazeFace settings (profiles, gestures, calibration, ...)?%n%nIf you keep them, they are used again when you reinstall GazeFace.
ja.DeleteSettings=GazeFace の設定（プロファイル、ジェスチャー、キャリブレーションなど）も削除しますか？%n%n残しておくと、もう一度インストールしたときにそのまま使えます。
en.EditionTitle=Choose an edition
ja.EditionTitle=インストールする版の選択
en.EditionSub=Select which edition of GazeFace to install.
ja.EditionSub=インストールする GazeFace の版を選んでください。
en.FullName=Full edition
ja.FullName=完全版
en.StandardName=Standard edition
ja.StandardName=通常版
en.StartupTitle=Starting with Windows
ja.StartupTitle=起動の設定
en.StartupSub=Choose when Windows starts GazeFace.
ja.StartupSub=Windows が GazeFace を起動するタイミングを選んでください。
en.LogonName=Start the lite edition on the sign-in screen
ja.LogonName=サインイン画面で軽量版を起動する
en.AfterSigninName=Start after sign-in
ja.AfterSigninName=サインイン後に起動する
en.RunningFull=Setting up the full edition...
ja.RunningFull=完全版のセットアップを実行しています...
en.FullFailed=The full edition setup script failed (code %1).%n%nGazeFace has been installed, but full edition features may not work.
ja.FullFailed=完全版のセットアップ スクリプトが失敗しました（コード %1）。%n%nGazeFace はインストールされましたが、完全版の機能が動かない可能性があります。

; FullDesc / StandardDesc / LogonDesc / AfterSigninDesc / DoneFull / DoneStandard
#include "custom\texts.iss"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Chosen before the install (checked every time); started when the finish
; page appears (CurPageChanged), so no checkbox on that page.
Name: "launchapp"; Description: "{cm:LaunchApp}"; GroupDescription: "{cm:AfterInstall}"

[Files]
Source: "{#DistDir}\GazeFace\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Updater: an elevated scheduled task the app can start without a UAC
; prompt, which the standard edition could not operate (updater\*.ps1).
Source: "updater\gazeface_update.ps1"; DestDir: "{app}\updater"; Flags: ignoreversion
Source: "updater\register_task.ps1"; DestDir: "{app}\updater"; Flags: ignoreversion
; Virtual mouse driver: inf + dll + UNSIGNED catalog (full_edition.ps1
; signs it with the per-PC certificate, then runs gfmouse_setup.ps1).
; Installed for both editions so the uninstaller always has -Remove.
Source: "{#DriverPkg}\*"; DestDir: "{app}\driver"; Flags: ignoreversion
Source: "driver\gfmouse_setup.ps1"; DestDir: "{app}\driver"; Flags: ignoreversion
; "Start after sign-in": run as the original (non-elevated) user, so it
; has to be readable by that user - {app}, not the elevated user's {tmp}.
Source: "setup\at_user_config.ps1"; DestDir: "{app}\setup"; Flags: ignoreversion
; Full edition only: run elevated from CurStepChanged(ssPostInstall).
Source: "custom\full_edition.ps1"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: IsFullEdition

[Icons]
Name: "{group}\GazeFace"; Filename: "{app}\GazeFace.exe"
Name: "{autodesktop}\GazeFace"; Filename: "{app}\GazeFace.exe"; Tasks: desktopicon

; Assistive-technology registration. Registering starts
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
; Scripts run in a visible console (no runhidden), install and uninstall alike.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\updater\register_task.ps1"""; Flags: waituntilterminated

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\updater\register_task.ps1"" -Remove"; Flags: waituntilterminated; RunOnceId: "RemoveUpdateTask"
; Virtual mouse device + driver package (no-op when never installed).
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""{app}\driver\gfmouse_setup.ps1"" -Remove"; Flags: waituntilterminated; RunOnceId: "RemoveDriver"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
const
  SubExe = 'gazeface_sub.exe';
  LocalCertName = 'CN=GazeFace Local Signing';
  { Left in the install folder when the full edition setup fails; GazeFace reads it and
    says so (src\updater.py), since a silent update shows no message. }
  FullFailedMark = 'full_edition_failed.txt';

{ ---- running GazeFace ---- }

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

{ ---- edition page / startup page ---- }

var
  FullRadio, StandardRadio: TNewRadioButton;
  StartupPage: TWizardPage;
  LogonCheck, AfterSigninCheck: TNewCheckBox;
  { The edition of the install being updated ('' when there is none),
    read before this install records its own. }
  PrevEdition: String;

function IsFullEdition: Boolean;
begin
  Result := FullRadio.Checked;
end;

{ Is 'gazeface' in RootKey's Configuration list (started with Windows)? }
function InConfiguration(RootKey: Integer): Boolean;
var
  V, Item: String;
  P: Integer;
begin
  Result := False;
  if not RegQueryStringValue(RootKey, '{#AccKey}', 'Configuration', V) then
    Exit;
  V := V + ',';
  while Length(V) > 0 do
  begin
    P := Pos(',', V);
    Item := Trim(Copy(V, 1, P - 1));
    Delete(V, 1, P);
    if CompareText(Item, 'gazeface') = 0 then
    begin
      Result := True;
      Exit;
    end;
  end;
end;

function EditionName: String;
begin
  if IsFullEdition then
    Result := 'full'
  else
    Result := 'standard';
end;

{ Clicking a description selects its option. }
procedure DescClick(Sender: TObject);
begin
  if TNewStaticText(Sender).Tag = 1 then
    FullRadio.Checked := True
  else
    StandardRadio.Checked := True;
end;

{ Clicking a description toggles its checkbox. }
procedure CheckDescClick(Sender: TObject);
begin
  if TNewStaticText(Sender).Tag = 1 then
    LogonCheck.Checked := not LogonCheck.Checked
  else
    AfterSigninCheck.Checked := not AfterSigninCheck.Checked;
end;

{ The wrapped description under an option at Top (none when Desc is
  empty). Returns the Top for the next option. }
function AddDesc(Page: TWizardPage; Top, Tag: Integer; const Desc: String;
  OnClick: TNotifyEvent): Integer;
var
  L: TNewStaticText;
begin
  Result := Top + ScaleY(12);
  if Desc = '' then
    Exit;
  L := TNewStaticText.Create(Page);
  L.Parent := Page.Surface;
  L.Tag := Tag;
  L.AutoSize := False;
  L.WordWrap := True;
  L.Left := ScaleX(20);
  L.Top := Top + ScaleY(2);
  L.Width := Page.SurfaceWidth - L.Left;
  L.Caption := Desc;
  L.AdjustHeight;
  L.OnClick := OnClick;
  Result := L.Top + L.Height + ScaleY(16);
end;

{ A radio button with a wrapped description under it. Returns the Top
  for the next option. }
function AddOption(Page: TWizardPage; Top, Tag: Integer;
  const Caption, Desc: String; var Radio: TNewRadioButton): Integer;
begin
  Radio := TNewRadioButton.Create(Page);
  Radio.Parent := Page.Surface;
  Radio.Caption := Caption;
  Radio.Font.Style := [fsBold];
  Radio.Left := 0;
  Radio.Top := Top;
  Radio.Width := Page.SurfaceWidth;
  Radio.Height := ScaleY(20);
  Result := AddDesc(Page, Radio.Top + Radio.Height, Tag, Desc, @DescClick);
end;

{ The same with a checkbox, checked. }
function AddCheck(Page: TWizardPage; Top, Tag: Integer;
  const Caption, Desc: String; var Check: TNewCheckBox): Integer;
begin
  Check := TNewCheckBox.Create(Page);
  Check.Parent := Page.Surface;
  Check.Caption := Caption;
  Check.Font.Style := [fsBold];
  Check.Left := 0;
  Check.Top := Top;
  Check.Width := Page.SurfaceWidth;
  Check.Height := ScaleY(20);
  Check.Checked := True;
  Result := AddDesc(Page, Check.Top + Check.Height, Tag, Desc, @CheckDescClick);
end;

procedure InitializeWizard;
var
  Page: TWizardPage;
  Top: Integer;
  Initial: String;
begin
  Page := CreateCustomPage(wpWelcome, CustomMessage('EditionTitle'),
                           CustomMessage('EditionSub'));
  Top := AddOption(Page, 0, 1, CustomMessage('FullName'),
                   CustomMessage('FullDesc'), FullRadio);
  AddOption(Page, Top, 2, CustomMessage('StandardName'),
            CustomMessage('StandardDesc'), StandardRadio);
  { Last install's choice, else the full edition; /EDITION=full|standard
    overrides it (silent). }
  PrevEdition := Lowercase(GetPreviousData('Edition', ''));
  Initial := PrevEdition;
  if Initial = '' then
    Initial := 'full';
  Initial := Lowercase(ExpandConstant('{param:EDITION|' + Initial + '}'));
  FullRadio.Checked := Initial = 'full';
  StandardRadio.Checked := not FullRadio.Checked;

  { Starting with Windows needs the signed GazeFace_UI.exe: full edition
    only (ShouldSkipPage). Checked on a new install; when a full edition
    is being updated, they start from what is set now (the user may have
    changed them in GazeFace since). }
  StartupPage := CreateCustomPage(Page.ID, CustomMessage('StartupTitle'),
                                  CustomMessage('StartupSub'));
  Top := AddCheck(StartupPage, 0, 1, CustomMessage('LogonName'),
                  CustomMessage('LogonDesc'), LogonCheck);
  AddCheck(StartupPage, Top, 2, CustomMessage('AfterSigninName'),
           CustomMessage('AfterSigninDesc'), AfterSigninCheck);
  if PrevEdition = 'full' then
  begin
    LogonCheck.Checked := InConfiguration(HKLM64);
    AfterSigninCheck.Checked := InConfiguration(HKCU);
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = StartupPage.ID) and not IsFullEdition;
end;

procedure RegisterPreviousData(PreviousDataKey: Integer);
begin
  SetPreviousData(PreviousDataKey, 'Edition', EditionName);
end;

{ ---- starting with Windows (same lists as src\at_startup.py) ---- }

{ Add (On) or drop 'gazeface' in the shared, comma-separated
  Configuration list, keeping every other AT in its order. HKLM = started
  on the sign-in / lock screen; HKCU = started after sign-in. }
procedure SetInConfiguration(RootKey: Integer; On: Boolean);
var
  V, Item, OutS: String;
  P: Integer;
begin
  if not RegQueryStringValue(RootKey, '{#AccKey}', 'Configuration', V) then
  begin
    if not On then
      Exit;
    V := '';
  end;
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
  if On then
  begin
    if OutS <> '' then
      OutS := OutS + ',';
    OutS := OutS + 'gazeface';
  end;
  RegWriteStringValue(RootKey, '{#AccKey}', 'Configuration', OutS);
end;

{ The startup page's choices; the standard edition turns both off (its
  GazeFace_UI.exe is unsigned, so Windows could not start it). HKCU must
  be the signed-in user's, not the elevated one's: at_user_config.ps1
  runs as the original user. }
procedure ApplyStartupOptions;
var
  Logon, After: Boolean;
  RC: Integer;
  Param: String;
begin
  Logon := IsFullEdition and LogonCheck.Checked;
  After := IsFullEdition and AfterSigninCheck.Checked;
  SetInConfiguration(HKLM64, Logon);
  if After then
    Param := '1'
  else
    Param := '0';
  if not ExecAsOriginalUser(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
       AddQuotes(ExpandConstant('{app}\setup\at_user_config.ps1')) + ' -On ' + Param,
       '', SW_SHOW, ewWaitUntilTerminated, RC) then
    Log('at_user_config.ps1 did not start: ' + SysErrorMessage(RC))
  else if RC <> 0 then
    Log('at_user_config.ps1 exit code ' + IntToStr(RC));
end;

{ ---- install ---- }

{ custom\full_edition.ps1, elevated (the installer runs as admin), in a
  visible console so the script can show progress or ask questions. On
  failure the script may leave its error text (UTF-8) in
  full_edition_error.txt next to itself; it is shown in the error box. }
procedure RunFullEditionScript;
var
  RC, I: Integer;
  Msg: String;
  Lines: TArrayOfString;
begin
  WizardForm.StatusLabel.Caption := CustomMessage('RunningFull');
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
              '-NoProfile -ExecutionPolicy Bypass -File ' +
              AddQuotes(ExpandConstant('{tmp}\full_edition.ps1')) +
              ' -AppDir ' + AddQuotes(ExpandConstant('{app}')) +
              ' -Language ' + ActiveLanguage,
              '', SW_SHOW, ewWaitUntilTerminated, RC) then
    Log('full_edition.ps1 did not start: ' + SysErrorMessage(RC))
  else if RC = 0 then
  begin
    DeleteFile(ExpandConstant('{app}\' + FullFailedMark));
    Exit;
  end
  else
    Log('full_edition.ps1 exit code ' + IntToStr(RC));
  Msg := FmtMessage(CustomMessage('FullFailed'), [IntToStr(RC)]);
  if LoadStringsFromFile(ExpandConstant('{tmp}\full_edition_error.txt'), Lines) then
  begin
    Msg := Msg + #13#10;
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      Log('full_edition.ps1: ' + Lines[I]);
      Msg := Msg + #13#10 + Lines[I];
    end;
  end;
  SetArrayLength(Lines, 1);
  Lines[0] := Msg;
  SaveStringsToUTF8File(ExpandConstant('{app}\' + FullFailedMark), Lines, False);
  SuppressibleMsgBox(Msg, mbError, MB_OK, IDOK);
end;

{ The per-PC signing certificate the full edition put in Root and
  TrustedPublisher (its private key was deleted right after signing).
  Removed on uninstall and when the standard edition is installed, so the
  PC's trust settings do not outlive the full edition. }
procedure RemoveLocalCertificate;
var
  RC: Integer;
begin
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
       'foreach ($s in ''Root'', ''TrustedPublisher'') { ' +
       'Get-ChildItem (''Cert:\LocalMachine\'' + $s) | ' +
       'Where-Object Subject -eq ''' + LocalCertName + ''' | Remove-Item }"',
       '', SW_HIDE, ewWaitUntilTerminated, RC) then
    Log('certificate removal did not start: ' + SysErrorMessage(RC))
  else if RC <> 0 then
    Log('certificate removal exit code ' + IntToStr(RC));
end;

{ The virtual mouse driver, when a full edition is replaced by the
  standard one (which does not use it). }
procedure RemoveDriver;
var
  RC: Integer;
begin
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
       '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
       AddQuotes(ExpandConstant('{app}\driver\gfmouse_setup.ps1')) + ' -Remove',
       '', SW_HIDE, ewWaitUntilTerminated, RC) then
    Log('driver removal did not start: ' + SysErrorMessage(RC));
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { The chosen language becomes the app's first-run language
      (install_defaults.json, read by src\paths.py).
      ASCII only, so no BOM is written. }
    SaveStringToFile(ExpandConstant('{app}\install_defaults.json'),
                     '{"language": "' + ActiveLanguage + '"}', False);
    if IsFullEdition then
      RunFullEditionScript
    else
    begin
      DeleteFile(ExpandConstant('{app}\' + FullFailedMark));
      RemoveLocalCertificate;
      if PrevEdition = 'full' then
        RemoveDriver;
    end;
    { A silent update of the same edition (GazeFace's own updater) leaves
      the startup settings as the user last set them. It also runs as
      SYSTEM, whose HKCU is not the user's. }
    if not (WizardSilent and (PrevEdition = EditionName)) then
      ApplyStartupOptions;
  end;
end;

{ Task "launchapp": as the original user (elevated, GazeFace would behave
  differently). Not in silent installs. }
procedure LaunchApp;
var
  RC: Integer;
begin
  if WizardSilent or not WizardIsTaskSelected('launchapp') then
    Exit;
  if not ExecAsOriginalUser(ExpandConstant('{app}\GazeFace.exe'), '',
                            ExpandConstant('{app}'), SW_SHOWNORMAL,
                            ewNoWait, RC) then
    Log('GazeFace.exe did not start: ' + SysErrorMessage(RC));
end;

var
  LaunchDefaulted: Boolean;

{ Tasks page: "start GazeFace" is checked every time, not restored from
  the last install (other tasks keep their previous state); once only, so
  going Back keeps what the user chose. Finish page: start GazeFace
  (before Finish is clicked), then the note from custom\texts.iss; empty
  keeps Inno Setup's own text. }
procedure CurPageChanged(CurPageID: Integer);
var
  Note: String;
begin
  if (CurPageID = wpSelectTasks) and not LaunchDefaulted then
  begin
    WizardSelectTasks('launchapp');
    LaunchDefaulted := True;
  end;
  if CurPageID = wpFinished then
  begin
    LaunchApp;
    if IsFullEdition then
      Note := CustomMessage('DoneFull')
    else
      Note := CustomMessage('DoneStandard');
    if Note = '' then
      Exit;
    WizardForm.FinishedLabel.Caption := Note;
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

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    SetInConfiguration(HKLM64, False);
    SetInConfiguration(HKCU, False);
    RemoveLocalCertificate;
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
