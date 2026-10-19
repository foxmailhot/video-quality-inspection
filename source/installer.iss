#ifndef AppVersion
  #define AppVersion "2.1.0"
#endif
#ifndef ProductName
  #define ProductName "FrameScope"
#endif
#ifndef ProductGuid
  #define ProductGuid "C2800AF4-884D-4F88-811D-F36EE130C299"
#endif
#ifndef RegistryPath
  #define RegistryPath "Software\FrameScope\Installer"
#endif
#ifndef PayloadDir
  #define PayloadDir ".."
#endif
#ifndef InstallerOutput
  #define InstallerOutput "..\.."
#endif
#ifndef SkipEnvironmentCheck
  #define SkipEnvironmentCheck "0"
#endif

[Setup]
AppId={{{#ProductGuid}}
AppName={#ProductName}
AppVersion={#AppVersion}
AppVerName={#ProductName} {#AppVersion}
AppPublisher=FrameScope
DefaultDirName={localappdata}\Programs\{#ProductName}
DefaultGroupName={#ProductName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
UsePreviousAppDir=yes
UsePreviousTasks=yes
DisableDirPage=auto
DisableWelcomePage=no
UninstallDisplayIcon={app}\FrameScope.exe
CloseApplications=yes
CloseApplicationsFilter=FrameScope.exe
RestartApplications=no
SetupLogging=yes
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
OutputDir={#InstallerOutput}
OutputBaseFilename=FrameScope-{#AppVersion}-Setup-x64
VersionInfoVersion={#AppVersion}

[Languages]
Name: "chinesesimp"; MessagesFile: "Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#ProductName}"; Filename: "{app}\FrameScope.exe"
Name: "{autodesktop}\{#ProductName}"; Filename: "{app}\FrameScope.exe"; Tasks: desktopicon
Name: "{group}\使用说明"; Filename: "{app}\使用说明.md"

[Registry]
Root: HKCU; Subkey: "{#RegistryPath}"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{#RegistryPath}"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"

[Run]
Filename: "{app}\FrameScope.exe"; Description: "启动 FrameScope"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\runtime-check.json"
Type: files; Name: "{app}\installed-version.txt"

[Code]
var
  InstalledVersion: String;
  ModeText: String;

function VersionCode(Value: String): Integer;
var
  I, P, Part: Integer;
begin
  Result := 0;
  for I := 1 to 3 do begin
    P := Pos('.', Value);
    if P = 0 then begin Part := StrToIntDef(Value, 0); Value := ''; end
    else begin Part := StrToIntDef(Copy(Value, 1, P-1), 0); Delete(Value, 1, P); end;
    Result := Result * 1000 + Part;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if RegQueryStringValue(HKCU, '{#RegistryPath}', 'Version', InstalledVersion) then begin
    if VersionCode(InstalledVersion) > VersionCode('{#AppVersion}') then begin
      Log('Downgrade blocked: installed=' + InstalledVersion + ' package={#AppVersion}');
      if not WizardSilent then MsgBox('已安装更高版本 ' + InstalledVersion + '，本安装包不会覆盖更新的程序。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure InitializeWizard();
begin
  if InstalledVersion <> '' then begin
    if InstalledVersion = '{#AppVersion}' then ModeText := '修复 / 重新安装模式'
    else ModeText := '检测升级模式：' + InstalledVersion + ' → {#AppVersion}';
  end else if FileExists(ExpandConstant('{localappdata}\FrameScope\FrameScope\session.json')) then
    ModeText := '检测到已有使用记录：安装正式版并沿用原设置'
  else ModeText := '首次安装模式';
  Log('FrameScope install mode: ' + ModeText);
  WizardForm.WelcomeLabel2.Caption := ModeText + #13#10#13#10 +
    '本安装包包含程序、Python 运行时、Qt、FFmpeg 和 CUDA 分析运行库，无需另外安装 Python 或 CUDA 工具包。' + #13#10#13#10 +
    '安装或升级保留用户设置、背景图片、检测队列及报告。NVIDIA GPU 加速需要电脑已有兼容显卡驱动；不支持时程序会明确提示并使用 CPU。' + #13#10#13#10 +
    '升级时请先结束检测并关闭旧版程序。';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ExitCode: Integer;
  Report: AnsiString;
begin
  if (CurStep = ssPostInstall) and ('{#SkipEnvironmentCheck}' = '0') then begin
    WizardForm.StatusLabel.Caption := '正在检查内置运行环境及 NVIDIA GPU…';
    if Exec(ExpandConstant('{app}\FrameScope.exe'), '--check-environment "' + ExpandConstant('{app}\runtime-check.json') + '"', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then begin
      Log('Environment check exit code: ' + IntToStr(ExitCode));
      if LoadStringFromFile(ExpandConstant('{app}\runtime-check.json'), Report) then begin
        if Pos('"cuda": true', String(Report)) > 0 then
          WizardForm.FinishedLabel.Caption := '安装完成。内置运行环境及 NVIDIA CUDA 算力检查通过。' + #13#10 + '原有设置和检测记录已保留。'
        else WizardForm.FinishedLabel.Caption := '安装完成。当前环境未启用 NVIDIA CUDA，程序将使用 CPU。运行环境检查结果保存在安装目录 runtime-check.json。';
      end;
    end else Log('Unable to start environment check.');
  end;
  if CurStep = ssDone then
    SaveStringToFile(ExpandConstant('{app}\installed-version.txt'), '{#AppVersion}', False);
end;
