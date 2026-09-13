"""Monta build_out/tools_bundle — o que o mc3.spec empacota como `tools/`.

Por que existe: o spec NAO empacota `tools/` direto, e sim este bundle (as CLIs
PS2 precisam virar .exe, porque um app congelado nao tem interpretador Python —
veja core._tool_command). Montar isso na mao ja divergiu uma vez: o ffprobe.exe
entrou em tools/ e nunca chegou ao bundle, entao o app instalado ficou sem leitura
de tags e o auto-preenchimento caiu, em silencio, para adivinhacao pelo nome.

Uso:
    python packaging/make_tools_bundle.py
    python packaging/make_tools_bundle.py --check    (so confere, nao escreve)

Depois:
    python -m PyInstaller packaging/mc3.spec --noconfirm \
        --distpath build_out/dist --workpath build_out/work
    ISCC.exe packaging/mc3.iss
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
TOOLS = PROJ / "tools"
TOOLS_EXE = PROJ / "build_out" / "tools_exe"
BUNDLE = PROJ / "build_out" / "tools_bundle"

# CLIs PS2 que existem como .py no fonte e PRECISAM ir como .exe para o pacote.
PS2_CLIS = ("dave", "hash_build", "strtbl")

# O que o app procura em runtime, por nome exato. Se algo daqui faltar no bundle,
# a funcionalidade correspondente morre em silencio no app instalado.
REQUIRED = (
    "MC3_PS2_Streams.lst",
    "dave.exe",
    "hash_build.exe",
    "strtbl.exe",
    "wav to rsm/ffmpeg.exe",
    "wav to rsm/ffprobe.exe",   # leitura de tags (core.find_ffprobe)
    "wav to rsm/rstm_build.exe",
)


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  + {dst.relative_to(BUNDLE)}  ({src.stat().st_size / 1e6:.1f} MB)")


def build() -> None:
    if BUNDLE.exists():
        shutil.rmtree(BUNDLE)
    BUNDLE.mkdir(parents=True)
    print(f"montando {BUNDLE.relative_to(PROJ)}")

    lst = TOOLS / "MC3_PS2_Streams.lst"
    if not lst.is_file():
        sys.exit(f"ERRO: {lst} nao encontrado.")
    _copy(lst, BUNDLE / lst.name)

    for name in PS2_CLIS:
        exe = TOOLS_EXE / f"{name}.exe"
        if not exe.is_file():
            sys.exit(
                f"ERRO: {exe} nao encontrado.\n"
                f"       As CLIs PS2 sao .py no fonte e precisam virar .exe. Gere com:\n"
                f'         python -m PyInstaller --onefile --distpath "{TOOLS_EXE}" '
                f'"{TOOLS / (name + ".py")}"'
            )
        _copy(exe, BUNDLE / exe.name)

    # 'wav to rsm': binarios como estao, SEM os .py e sem __pycache__.
    src_dir = TOOLS / "wav to rsm"
    for item in sorted(src_dir.iterdir()):
        if item.is_dir() or item.suffix.lower() in (".py", ".pyc"):
            continue
        _copy(item, BUNDLE / src_dir.name / item.name)

    check(fatal=True)
    print("\nbundle pronto.")


def check(fatal: bool = False) -> bool:
    missing = [rel for rel in REQUIRED if not (BUNDLE / rel).is_file()]
    if missing:
        msg = "FALTANDO no bundle: " + ", ".join(missing)
        if fatal:
            sys.exit("ERRO: " + msg)
        print(msg)
        return False
    total = sum(f.stat().st_size for f in BUNDLE.rglob("*") if f.is_file())
    print(f"bundle OK — {len(REQUIRED)} itens obrigatorios presentes, {total / 1e6:.0f} MB")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="apenas confere o bundle existente")
    args = parser.parse_args()
    if args.check:
        sys.exit(0 if check() else 1)
    build()
