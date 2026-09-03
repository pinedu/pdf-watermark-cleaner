[Setup]
AppId={{9079B971-55D5-4C5C-A4D8-7201958F09B7}
AppName=PDF 水印清理工具
AppVersion=1.0.0
AppPublisher=Local Tools
DefaultDirName={autopf}\PDFWatermarkCleaner
DefaultGroupName=PDF 水印清理工具
UninstallDisplayIcon={app}\PDFWatermarkCleaner-Desktop.exe
OutputDir=..\outputs
OutputBaseFilename=PDFWatermarkCleaner-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "..\outputs\PDFWatermarkCleaner-Desktop.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\PDF 水印清理工具"; Filename: "{app}\PDFWatermarkCleaner-Desktop.exe"
Name: "{autodesktop}\PDF 水印清理工具"; Filename: "{app}\PDFWatermarkCleaner-Desktop.exe"; Tasks: desktopicon
Name: "{group}\卸载 PDF 水印清理工具"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\PDFWatermarkCleaner-Desktop.exe"; Description: "启动 PDF 水印清理工具"; Flags: nowait postinstall skipifsilent
