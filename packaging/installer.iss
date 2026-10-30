; 映画チェッカー の Inno Setup の設計図 (MV-4)。packaging/build.ps1 から版数を /DAppVersion=... で渡す。
; moviAI の installer.iss を写した物。直接触らなくてよい。

#ifndef AppVersion
  #error "AppVersion を /DAppVersion=x.y.z で渡してください (build.ps1 が渡します)"
#endif

[Setup]
; AppId を固定することで「同じアプリ」と判定され、上書き更新になる。変えない。
AppId={{A3F2C7D1-6B4E-4F0A-9C21-5E8D7B6A4C10}
AppName=映画チェッカー
AppVersion={#AppVersion}
AppVerName=映画チェッカー {#AppVersion}
DefaultDirName={localappdata}\Programs\MovieChecker
DefaultGroupName=映画チェッカー
; 管理者権限なしでユーザー領域に入れる (個人の PC が前提)
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=映画チェッカー-setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
DisableProgramGroupPage=yes
UninstallDisplayName=映画チェッカー
SetupIconFile=..\app\movie_icon.ico
; 利用者データ (%APPDATA%\映画チェッカー、Windows の資格情報) は触らない。アンインストールでも残す

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Files]
Source: "..\dist\映画チェッカー\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\映画チェッカー"; Filename: "{app}\映画チェッカー.exe"
Name: "{autodesktop}\映画チェッカー"; Filename: "{app}\映画チェッカー.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "デスクトップにショートカットを作る"; GroupDescription: "追加:"

[Run]
Filename: "{app}\映画チェッカー.exe"; Description: "映画チェッカー を起動する"; Flags: nowait postinstall skipifsilent
