#ifndef AppVersion
#define AppVersion "0.0.0"
#endif

#ifndef SourceDir
#define SourceDir "..\dist\release-candidate\ChannelPress"
#endif

#ifndef WebView2Bootstrapper
#define WebView2Bootstrapper "..\.build\windows\WebView2Bootstrapper.exe"
#endif

[Setup]
AppId={{A3D921D3-84CB-4D2B-82C2-2D723CD0E71A}
AppName=Channel Press
AppVersion={#AppVersion}
AppPublisher=AkhnasFurqan-Dev
DefaultDirName={localappdata}\Programs\Channel Press
DefaultGroupName=Channel Press
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=ChannelPressSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ChannelPress.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebView2Bootstrapper}"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\Channel Press"; Filename: "{app}\ChannelPress.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\Channel Press"; Filename: "{app}\ChannelPress.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\ChannelPress.exe"; Description: "Launch Channel Press"; Flags: nowait postinstall skipifsilent

[Code]
const
  AppUninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{A3D921D3-84CB-4D2B-82C2-2D723CD0E71A}_is1';

function WebView2InstalledIn(const Root: String): Boolean;
var
  FindRec: TFindRec;
  RuntimePath: String;
begin
  Result := False;
  if FindFirst(ExpandConstant(Root + '\*'), FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          RuntimePath := ExpandConstant(Root + '\' + FindRec.Name + '\msedgewebview2.exe');
          if FileExists(RuntimePath) then
          begin
            Result := True;
            exit;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function WebView2Installed(): Boolean;
begin
  Result := WebView2InstalledIn('{pf32}\Microsoft\EdgeWebView\Application') or
    WebView2InstalledIn('{pf64}\Microsoft\EdgeWebView\Application') or
    WebView2InstalledIn('{localappdata}\Microsoft\EdgeWebView\Application');
end;

function InitializeSetup(): Boolean;
var
  ExistingVersion: String;
begin
  Result := True;
  if RegQueryStringValue(HKCU, AppUninstallKey, 'DisplayVersion', ExistingVersion) then
  begin
    if not WizardSilent then
      Result :=
        MsgBox(
          'An existing Channel Press installation was found (version ' + ExistingVersion + ').' + #13#10 + #13#10 +
          'This setup will update or repair application files. Your personal data, Telegram sessions, settings and editions are stored separately and will not be removed. Continue?',
          mbConfirmation,
          MB_YESNO
        ) = IDYES;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if WebView2Installed() then
    exit;

  ExtractTemporaryFile('WebView2Bootstrapper.exe');
  if not Exec(
    ExpandConstant('{tmp}\WebView2Bootstrapper.exe'),
    '/silent /install',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) or (ResultCode <> 0) then
  begin
    Result := 'Microsoft Edge WebView2 Runtime could not be installed. Check the internet connection and retry the setup.';
    exit;
  end;

  if not WebView2Installed() then
    Result := 'WebView2 Runtime was not detected after installation. Channel Press setup cannot continue.';
end;
