; Inno Setup script for AutoApply (Windows). Created by Abhiram (Challa Abhiram). MIT license.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
[Setup]
AppId=com.abhiram.autoapply
AppName=AutoApply
AppVersion={#AppVersion}
AppPublisher=Abhiram (Challa Abhiram)
AppPublisherURL=https://github.com/Abhi-420bytes/autoapply-agent
AppCopyright=Created by Abhiram (Challa Abhiram) - MIT license
DefaultDirName={localappdata}\Programs\AutoApply
DefaultGroupName=AutoApply
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename=AutoApply-windows-setup
SetupIconFile=icons\AutoApply.ico
UninstallDisplayIcon={app}\AutoApply.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start AutoApply when I sign in (keeps the agent working)"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\AutoApply"; Filename: "{app}\AutoApply.exe"
Name: "{autodesktop}\AutoApply"; Filename: "{app}\AutoApply.exe"; Tasks: desktopicon
Name: "{userstartup}\AutoApply"; Filename: "{app}\AutoApply.exe"; Tasks: startup

[Run]
Filename: "{app}\AutoApply.exe"; Description: "Open AutoApply now"; Flags: nowait postinstall skipifsilent
