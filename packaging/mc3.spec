# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — MC3 Music Manager (web-view edition).

onedir, NOT onefile, on purpose:
  * tools/ is ~115 MB (ffmpeg alone is 84 MB) — onefile would re-extract all of it
    to a temp dir on EVERY launch (slow) and antivirus hates that pattern.
  * the app invokes tools/*.exe as subprocesses; onedir keeps them on disk.
  * the workspace (game data, ~19 GB) lives NEXT TO the exe — core._app_root()
    resolves that from sys.executable, which only makes sense for onedir.

Build:  python -m PyInstaller packaging/mc3.spec --noconfirm
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

PROJ = Path(SPECPATH).resolve().parent          # noqa: F821 - SPECPATH is injected
TOOLS_BUNDLE = PROJ / "build_out" / "tools_bundle"

# Guarda de build: o bundle e montado por packaging/make_tools_bundle.py e ja
# divergiu uma vez — o ffprobe.exe entrou em tools/ e nunca chegou aqui, entao o
# app instalado ficou sem leitura de tags e o auto-preenchimento caiu em silencio
# para adivinhacao pelo nome. Falhar o build e melhor do que enviar isso.
sys.path.insert(0, str(PROJ / "packaging"))
from make_tools_bundle import check as _check_bundle   # noqa: E402

if not _check_bundle():
    raise SystemExit(
        "tools_bundle incompleto. Rode antes:  python packaging/make_tools_bundle.py")

# Guarda de DADOS DO USUARIO. O COLLECT la embaixo, com --noconfirm, roda
# "Removing dir <dist>/MC3 Music Manager" antes de gravar o build novo. So que o
# app monta o workspace AO LADO do .exe (core._app_root) -- entao quem abriu o
# executavel direto de dentro do build e preparou o jogo tem ali ~10 GB de
# extracao, as musicas adicionadas e os BACKUPS. Rebuild = tudo apagado, sem
# pergunta. Aconteceu de verdade em 2026-09: 12 faixas adicionadas pelo app
# empacotado quase foram junto num rebuild de rotina.
# Esta checagem roda antes do COLLECT, entao recusar aqui nao apaga nada.
_APP_DIR = Path(DISTPATH) / "MC3 Music Manager"   # noqa: F821 - DISTPATH is injected
_USER_DATA = ("backups", "STREAMS", "ASSETS", "Arquivos da ISO",
              "ASSETS.DAT", "STREAMS.DAT", "mcstrings02.json", "ISO")
_found = [name for name in _USER_DATA if (_APP_DIR / name).exists()]
if _found:
    raise SystemExit(
        "\nRECUSADO: ha dados do usuario na pasta que este build APAGARIA:\n"
        f"    {_APP_DIR}\n"
        f"    encontrado: {', '.join(_found)}\n"
        "O PyInstaller remove essa pasta inteira antes de gravar o build novo.\n"
        "Mova o workspace para fora dela, ou gere em outro lugar com\n"
        "    --distpath <outra pasta>\n"
    )

# pywebview pulls in platform backends + .NET glue dynamically; collect_all keeps
# the EdgeChromium (WebView2) backend from being tree-shaken away.
wv_datas, wv_binaries, wv_hidden = collect_all("webview")

a = Analysis(
    [str(PROJ / "main.py")],
    pathex=[str(PROJ)],
    binaries=wv_binaries,
    datas=[
        (str(PROJ / "frontend"), "frontend"),      # UI + locales (served by Python)
        (str(TOOLS_BUNDLE), "tools"),              # PS2 tools + ffmpeg (exe only)
    ] + wv_datas,
    hiddenimports=wv_hidden + [
        "webview.platforms.edgechromium",
        "webview.platforms.winforms",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MC3 Music Manager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX on ffmpeg/tools = antivirus false positives
    console=False,      # GUI app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJ / "packaging" / "mc3.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MC3 Music Manager",
)
