; Inno Setup script — MC3 Music Manager (web-view edition)
;
; Build order:
;   1) python -m PyInstaller packaging/mc3.spec --noconfirm --distpath build_out/dist --workpath build_out/work
;   2) ISCC.exe packaging\mc3.iss        (Inno Setup 6)
;   -> build_out\installer\MC3_Music_Manager_Setup.exe
;
; IMPORTANT — install location: the app keeps its WORKSPACE (the extracted game
; data) NEXT TO the .exe, and that grows to ~20 GB. So we install per-user
; (no admin, writable) and leave the directory page ON so the user can pick a
; roomy drive. Program Files would be wrong: not user-writable.

#define AppName        "MC3 Music Manager"
#define AppVersion     "0.3.0"
#define AppPublisher   "MC3 Music Manager"
#define AppExeName     "MC3 Music Manager.exe"
#define SourceDir      "..\build_out\dist\MC3 Music Manager"

[Setup]
AppId={{8E5A1C43-6D2B-4F19-9C7A-2B7E4D9A1F63}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableDirPage=no
DisableProgramGroupPage=yes
; Per-user install -> no admin prompt, and the install dir stays writable so the
; app can create its workspace/options.json beside the exe.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\build_out\installer
OutputBaseFilename=MC3_Music_Manager_Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=mc3.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 0 esta CERTO: o Inno ja soma sozinho o tamanho de [Files] na checagem de disco,
; e este valor e o espaco EXTRA alem disso. O que a checagem nao ve e o workspace
; de ~20 GB que o app cria depois — por isso o aviso esta no WelcomeLabel2.
ExtraDiskSpaceRequired=0
UninstallDisplayIcon={app}\{#AppExeName}
AppComments=Troca a trilha sonora do Midnight Club 3: DUB Edition Remix. Requer a sua propria copia do jogo.
; Third-party notices (FFmpeg LGPL/GPL, PS2 tools) shown before install — the app
; redistributes FFmpeg, so the notice has to be in front of the user.
LicenseFile=..\LICENSES.md

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
brazilianportuguese.WelcomeLabel2=Isto vai instalar o {#AppName} no seu computador.%n%nATENCAO: o app extrai o seu jogo para uma pasta de trabalho AO LADO do programa, que chega a ~20 GB. Escolha um drive com espaco livre.%n%nVoce precisa da sua propria copia (ISO) do Midnight Club 3: DUB Edition Remix — ela NAO acompanha o programa.
english.WelcomeLabel2=This will install {#AppName} on your computer.%n%nNOTE: the app extracts your game into a workspace folder NEXT TO the program, which grows to ~20 GB. Pick a drive with free space.%n%nYou need your own copy (ISO) of Midnight Club 3: DUB Edition Remix — it is NOT included.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole PyInstaller onedir output (exe + _internal with frontend/ and tools/).
Source: "{#SourceDir}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
; Docs are handy next to the app.
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
; Third-party notices (FFmpeg LGPL/GPL, PS2 tools, pywebview...) — required.
Source: "..\LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only our own runtime file. The workspace (ASSETS/, STREAMS/, Arquivos da ISO/,
; backups/, iso/) is the USER'S DATA and is deliberately left behind — the
; uninstaller must never wipe someone's game files or backups.
Type: files; Name: "{app}\options.json"
