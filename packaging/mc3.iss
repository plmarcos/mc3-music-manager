; Inno Setup script — MC3 Music Manager (web-view edition)
;
; Build order:
;   1) python packaging/make_tools_bundle.py
;   2) python -m PyInstaller packaging/mc3.spec --noconfirm --distpath build_out/dist --workpath build_out/work
;   3) ISCC.exe packaging\mc3.iss        (Inno Setup 6.3+)
;   -> build_out\installer\MC3_Music_Manager_Setup.exe
;
; IMPORTANT — install location: the app keeps its WORKSPACE (the extracted game
; data) NEXT TO the .exe, and that grows to ~20 GB. So we install per-user
; (no admin, writable) and leave the directory page ON so the user can pick a
; roomy drive. Program Files would be wrong: not user-writable.
;
; Codificação: este arquivo e o LICENSES.md são UTF-8 SEM BOM. O Inno Setup 6.3+
; lê assim o .iss, as mensagens e o LicenseFile (conferido no whatsnew do 6.7.3 e
; testado compilando um .iss com "ação — ガレージ" com e sem BOM).

; A versão TEM de bater com backend/api.py (APP_VERSION): o relatório de erro usa
; esse número para saber qual build a pessoa tem. Um teste confere as duas.
#define AppName        "MC3 Music Manager"
#define AppVersion     "0.4.0"
#define AppPublisher   "MC3 Music Manager"
#define AppExeName     "MC3 Music Manager.exe"
#define SourceDir      "..\build_out\dist\MC3 Music Manager"

[Setup]
AppId={{8E5A1C43-6D2B-4F19-9C7A-2B7E4D9A1F63}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
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
AppComments=Troca a trilha sonora do Midnight Club 3: DUB Edition Remix. Requer a sua própria cópia do jogo.
; Third-party notices (FFmpeg LGPL/GPL, PS2 tools) shown before install — the app
; redistributes FFmpeg, so the notice has to be in front of the user.
LicenseFile=..\LICENSES.md

; Os mesmos 7 idiomas do app: pt-BR + os 6 da tabela de textos do próprio jogo.
; O Inno escolhe sozinho pelo idioma do Windows (LanguageDetectionMethod=uilanguage).
[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

; O aviso dos ~20 GB e da ISO própria em TODOS os idiomas. Antes o espanhol estava
; listado sem ele e caía na mensagem padrão do Inno, sem nenhum dos dois avisos.
[Messages]
brazilianportuguese.WelcomeLabel2=Isto vai instalar o {#AppName} no seu computador.%n%nATENÇÃO: o app extrai o seu jogo para uma pasta de trabalho AO LADO do programa, que chega a ~20 GB. Escolha um drive com espaço livre.%n%nVocê precisa da sua própria cópia (ISO) do Midnight Club 3: DUB Edition Remix — ela NÃO acompanha o programa.
english.WelcomeLabel2=This will install {#AppName} on your computer.%n%nNOTE: the app extracts your game into a workspace folder NEXT TO the program, which grows to ~20 GB. Pick a drive with free space.%n%nYou need your own copy (ISO) of Midnight Club 3: DUB Edition Remix — it is NOT included.
spanish.WelcomeLabel2=Se instalará {#AppName} en tu equipo.%n%nATENCIÓN: la aplicación extrae tu juego a una carpeta de trabajo JUNTO al programa, que llega a ocupar ~20 GB. Elige una unidad con espacio libre.%n%nNecesitas tu propia copia (ISO) de Midnight Club 3: DUB Edition Remix — NO se incluye con el programa.
french.WelcomeLabel2={#AppName} va être installé sur votre ordinateur.%n%nATTENTION : l’application extrait votre jeu dans un dossier de travail À CÔTÉ du programme, qui atteint ~20 Go. Choisissez un lecteur avec de l’espace libre.%n%nVous avez besoin de votre propre copie (ISO) de Midnight Club 3: DUB Edition Remix — elle N’EST PAS fournie.
german.WelcomeLabel2={#AppName} wird jetzt auf Ihrem Computer installiert.%n%nACHTUNG: Die App entpackt Ihr Spiel in einen Arbeitsordner NEBEN dem Programm, der auf ~20 GB anwächst. Wählen Sie ein Laufwerk mit freiem Speicherplatz.%n%nSie brauchen Ihre eigene Kopie (ISO) von Midnight Club 3: DUB Edition Remix — sie ist NICHT enthalten.
italian.WelcomeLabel2={#AppName} verrà installato sul tuo computer.%n%nATTENZIONE: l’app estrae il gioco in una cartella di lavoro ACCANTO al programma, che arriva a ~20 GB. Scegli un disco con spazio libero.%n%nServe la tua copia (ISO) di Midnight Club 3: DUB Edition Remix — NON è inclusa.
japanese.WelcomeLabel2={#AppName} をこのコンピューターにインストールします。%n%n注意: このアプリは、ゲームをプログラムと同じ場所にある作業フォルダーへ展開します。容量は約 20 GB になります。空き容量のあるドライブを選んでください。%n%nMidnight Club 3: DUB Edition Remix のご自身の ISO が必要です（本プログラムには含まれていません）。

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
