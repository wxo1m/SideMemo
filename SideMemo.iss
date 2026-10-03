[Setup]
AppName=SideMemo
AppVersion=1.0.5
AppVerName=SideMemo 1.0.5
AppPublisher=SideMemo
AppPublisherURL=https://github.com/yuldaewoorim/SideMemo
AppSupportURL=https://github.com/yuldaewoorim/SideMemo
AppUpdatesURL=https://github.com/yuldaewoorim/SideMemo/releases
VersionInfoCompany=SideMemo
VersionInfoDescription=SideMemo Setup
VersionInfoProductName=SideMemo
VersionInfoProductVersion=1.0.5
VersionInfoVersion=1.0.5.0
DefaultDirName={autopf}\SideMemo
DefaultGroupName=SideMemo
OutputDir=installer
OutputBaseFilename=SideMemo-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=app.ico
UninstallDisplayIcon={app}\SideMemo.exe
CloseApplications=force
CloseApplicationsFilter=SideMemo.exe

[Tasks]
Name: "desktopicon"; Description: "바탕 화면 바로가기 만들기"; GroupDescription: "추가 바로가기:"

[Files]
; PyInstaller one-folder build: preserve the EXE and its _internal runtime folder.
Source: "dist\SideMemo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{app}\_internal\icudt*.dll"

[Icons]
Name: "{group}\SideMemo"; Filename: "{app}\SideMemo.exe"
Name: "{autodesktop}\SideMemo"; Filename: "{app}\SideMemo.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\SideMemo.exe"; Description: "SideMemo 실행"; Flags: nowait postinstall skipifsilent
