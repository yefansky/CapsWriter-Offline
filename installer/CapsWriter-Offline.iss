; 仅当前用户安装，不需要管理员权限；升级由同一个 AppId 接管。
#ifndef AppVersion
  #error AppVersion must be supplied with /DAppVersion=
#endif

#ifndef SourceDir
  #define SourceDir "..\dist\CapsWriter-Offline"
#endif

#ifndef OutputDir
  #define OutputDir "..\release"
#endif

[Setup]
AppId={{29D5F582-EDDC-4B62-96FA-C2EF65C2497B}
AppName=CapsWriter Offline
AppVersion={#AppVersion}
AppPublisher=CapsWriter Offline
DefaultDirName={localappdata}\Programs\CapsWriter-Offline
DefaultGroupName=CapsWriter Offline
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=CapsWriter-Offline-Setup
SetupIconFile=..\assets\icon.ico
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\start_manager.exe

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\CapsWriter Offline"; Filename: "{app}\start_manager.exe"; Parameters: "--restart"
Name: "{autodesktop}\CapsWriter Offline"; Filename: "{app}\start_manager.exe"; Parameters: "--restart"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "其他选项:"; Flags: checkedonce

[Run]
Filename: "{app}\start_manager.exe"; Parameters: "--restart"; Description: "启动 CapsWriter Offline"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SaveStringToFile(ExpandConstant('{app}\installation.json'), '{"installed": true}' + #13#10, False);
end;
