"""UI-free domain service.

No tkinter, no pywebview, no HTTP — just functions that orchestrate the PS2
tools via subprocess. This is the reusable core the feasibility analysis called
the key de-risking step: any frontend (the current Tkinter app, this web UI, or
a plain CLI) can call it. Progress/log are reported through INJECTED callbacks
so this module never imports a UI toolkit.

First ported capability: audio -> RSM conversion (safe, non-destructive), which
mirrors the proven pipeline from the original _convert_to_rsm:
  .rsm         -> copied as-is
  .ads / .ss2  -> straight to rstm_build (already PS2 audio)
  everything else (.wav/.mp3/...) -> ffmpeg normalize (32000/stereo/s16) -> rstm_build
Todo .rsm produzido passa por conform_rsm_to_game(), que acerta o que o rstm_build
deixa fora do padrao do jogo (ver o comentario da funcao) -- sem isso a faixa fica
MUDA no MC3.
The ffmpeg normalize step is what fixed the "ps2str exit -1" on some WAV headers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

def _resource_root() -> Path:
    """Bundled READ-ONLY resources (tools/, frontend/). Under PyInstaller these are
    unpacked next to the exe (onedir) or into sys._MEIPASS; from source they sit
    beside the code."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def _app_root() -> Path:
    """WRITABLE base where the workspace lives (game files, backups, options.json).

    When frozen this MUST be the folder the .exe sits in — never sys._MEIPASS, which
    is a temp dir Windows wipes (the ~19 GB of extracted game data would vanish)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


RESOURCE_ROOT = _resource_root()   # read-only, shipped with the app
PROJECT_ROOT = _app_root()         # writable workspace base
TOOLS_DIR = RESOURCE_ROOT / "tools" / "wav to rsm"
TOOLS_ROOT = RESOURCE_ROOT / "tools"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Timeouts (seconds) for tools that can hang WITHOUT printing progress. The long
# packaging tools (dave/hash_build/ImgBurn) intentionally have NO timeout — a real
# rebuild legitimately runs for minutes; the user aborts those via cancel_active().
FFMPEG_TIMEOUT = 300.0
FFPROBE_TIMEOUT = 20.0
MOUNT_TIMEOUT = 60.0

# Injected reporters. Both optional; the service works headless without them.
LogFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[float, str], None]]

AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".ads", ".ss2", ".rsm")

# Which PS2 disc images this app knows how to edit. The BOOT2 id in SYSTEM.CNF
# is the game's fingerprint; mirrors SUPPORTED_BOOT_IDS in the original app.
SUPPORTED_BOOT_IDS = frozenset({"SLUS_213.55"})
SUPPORTED_GAME_NAME = "Midnight Club 3: DUB Edition Remix (SLUS_213.55)"

# BOOT2 = cdrom0:\SLUS_213.55;1  ->  captures "SLUS_213.55"
_BOOT_ID_RE = re.compile(r"BOOT2?\s*=\s*cdrom0:\\([^;]+);", re.IGNORECASE)


class ToolError(RuntimeError):
    """Raised when an external tool fails. Carries a user-facing message."""


def find_ffmpeg() -> Optional[Path]:
    local = TOOLS_DIR / "ffmpeg.exe"
    if local.is_file():
        return local
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def find_rstm_build() -> Optional[Path]:
    exe = TOOLS_DIR / "rstm_build.exe"
    if exe.is_file():
        return exe
    script = TOOLS_DIR / "rstm_build.py"
    return script if script.is_file() else None


def tools_status() -> dict:
    """Report which tools are present (for a diagnostics screen)."""
    ff = find_ffmpeg()
    rb = find_rstm_build()
    return {
        "ffmpeg": str(ff) if ff else None,
        "rstm_build": str(rb) if rb else None,
        "tools_dir": str(TOOLS_DIR),
        "ready": bool(ff and rb),
    }


# Registry of in-flight subprocesses so a user "Cancelar" can kill the current
# task. The busy-lock guarantees at most one task at a time, so a module-level set
# is safe. Killing a proc makes _run return a non-zero code -> the caller raises
# ToolError -> the task's on_error path releases the busy-lock (already the norm).
_ACTIVE_PROCS: "set[subprocess.Popen]" = set()
_ACTIVE_LOCK = threading.Lock()


def cancel_active() -> int:
    """Kill every in-flight subprocess (user pressed Cancelar). Returns how many."""
    with _ACTIVE_LOCK:
        procs = list(_ACTIVE_PROCS)
    killed = 0
    for proc in procs:
        try:
            proc.kill()
            killed += 1
        except OSError:
            pass
    return killed


def _run(cmd, cwd: Optional[Path] = None, log: LogFn = None, input_text: str = "",
         timeout: Optional[float] = None) -> tuple[int, str]:
    command = [str(part) for part in cmd]
    if log:
        log("$ " + " ".join(command))
    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        # Decode as UTF-8 and REPLACE undecodable bytes. Without this, text=True
        # uses the Windows locale (cp1252, strict) and a stray byte in a tool's
        # output (e.g. ffmpeg's build-config banner has 0x8d) raises
        # UnicodeDecodeError mid-stream, aborting the whole task.
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.PIPE if input_text else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        creationflags=CREATE_NO_WINDOW,
    )
    with _ACTIVE_LOCK:
        _ACTIVE_PROCS.add(proc)

    # Watchdog: a tool that hangs without printing would block the stdout loop
    # forever. On deadline, kill the proc — closing the pipe unblocks the loop.
    proc_done = threading.Event()
    timed_out = threading.Event()
    watchdog: Optional[threading.Thread] = None
    if timeout and timeout > 0:
        def _watch() -> None:
            if not proc_done.wait(timeout):
                timed_out.set()
                try:
                    proc.kill()
                except OSError:
                    pass
        watchdog = threading.Thread(target=_watch, daemon=True)
        watchdog.start()

    try:
        # Some PS2 tools (hash_build/dave in build mode) prompt before overwriting;
        # feed the confirmation up front, then stream the output.
        if input_text and proc.stdin:
            proc.stdin.write(input_text)
            proc.stdin.close()
        lines: list[str] = []
        if proc.stdout:
            for line in proc.stdout:
                lines.append(line)
                clean = line.rstrip()
                if clean and log:
                    log(clean)
        code = proc.wait()
    finally:
        proc_done.set()
        if watchdog is not None:
            watchdog.join(timeout=1)
        with _ACTIVE_LOCK:
            _ACTIVE_PROCS.discard(proc)

    if timed_out.is_set():
        raise ToolError("A ferramenta excedeu o tempo limite (timeout).")
    return code, "".join(lines)


def _make_writable(path) -> None:
    """Clear the read-only bit so an existing target can be overwritten.

    Game files extracted from a PS2 ISO are commonly read-only; the original app
    clears the bit before every write (its _make_path_writable). Without this,
    overwriting an existing .rsm/.play/.strtbl/.DAT raises PermissionError.
    """
    p = Path(path)
    if not p.exists():
        return
    try:
        p.chmod(p.stat().st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _tool_detail(out: str, limit: int = 240) -> str:
    """Pull the meaningful tail out of a tool's output, for the error message.

    Without this the UI only ever said 'rstm_build falhou (codigo 1)' and the real
    reason (ps2str's own complaint, an assert, a permission problem) stayed buried
    in the console — which made a user's failure impossible to diagnose remotely."""
    lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
    if not lines:
        return ""
    for line in reversed(lines):  # prefer an explicit error line if there is one
        low = line.lower()
        if any(k in low for k in ("error", "erro", "exception", "assert", "failed", "denied")):
            return f" Detalhe: {line[:limit]}"
    return f" Detalhe: {lines[-1][:limit]}"


def convert_audio_to_rsm(
    source,
    output=None,
    log: LogFn = None,
    progress: ProgressFn = None,
) -> Path:
    """Convert an audio file to a PS2 RSM. Returns the output path.

    If ``output`` is omitted, writes next to the source with a .rsm suffix.
    Raises ToolError on any failure (never touches a UI).
    """
    source = Path(source)
    if not source.is_file():
        raise ToolError(f"Arquivo de origem nao encontrado: {source}")

    output = Path(output) if output else source.with_suffix(".rsm")
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()

    if progress:
        progress(5, "Preparando conversao...")

    if suffix == ".rsm":
        _make_writable(output)
        shutil.copy2(source, output)
        conform_rsm_to_game(output)  # pode vir de outra ferramenta, com o mesmo defeito
        if progress:
            progress(100, "Copiado (ja era RSM).")
        return output

    rstm = find_rstm_build()
    if rstm is None:
        raise ToolError("rstm_build nao encontrado em tools/wav to rsm.")
    ffmpeg = find_ffmpeg()

    # .ads/.ss2 are already PS2 audio; a .wav can also go straight to rstm_build
    # when ffmpeg is missing (legacy route — mirrors the original's fallback).
    if suffix in {".ads", ".ss2"} or (suffix == ".wav" and ffmpeg is None):
        if progress:
            progress(45, "Gerando RSM (PS2)...")
        with tempfile.TemporaryDirectory(prefix="mc3_rsm_", ignore_cleanup_errors=True) as scratch:
            temp_rsm = Path(scratch) / f"{source.stem}.rsm"
            code, out = _run(_tool_command(rstm, source, "-o", temp_rsm), cwd=rstm.parent, log=log)
            if code != 0:
                raise ToolError(f"rstm_build falhou (codigo {code}).{_tool_detail(out)}")
            _publish_rsm(temp_rsm, output)
        if progress:
            progress(100, "Concluido.")
        return output

    # Everything else -> ffmpeg normalize to the known-good WAV, then rstm_build.
    if ffmpeg is None:
        raise ToolError("FFmpeg nao encontrado em tools/wav to rsm nem no PATH do Windows.")

    # Scratch WAV goes in the OS temp dir — NOT inside output.parent — for the two
    # reasons the original app already handled (it used dir=base_path +
    # ignore_cleanup_errors + a startup sweep):
    #  * output.parent is STREAMS/Music/<genre>/, a folder packed into STREAMS.DAT
    #    on rebuild; a leftover temp dir there would smuggle junk into the game.
    #  * ignore_cleanup_errors swallows Windows "[WinError 32] file in use", which
    #    fires when Defender/the indexer briefly locks the freshly-written WAV
    #    during rmtree — otherwise that transient lock aborts the whole add.
    with tempfile.TemporaryDirectory(prefix="mc3_rsm_", ignore_cleanup_errors=True) as temp_dir:
        temp_wav = Path(temp_dir) / f"{source.stem}.wav"
        if progress:
            progress(25, "Normalizando audio (ffmpeg)...")
        # Keep `out`: _tool_detail() needs the tool's own complaint, and this
        # branch is also what a user "Cancelar" lands on (killing the proc makes
        # _run return non-zero) — discarding it raised UnboundLocalError here.
        code, out = _run(
            [ffmpeg, "-y", "-i", source, "-ar", str(MUSIC_SAMPLE_RATE),
             "-ac", "2", "-sample_fmt", "s16", temp_wav],
            log=log,
            timeout=FFMPEG_TIMEOUT,
        )
        if code != 0:
            raise ToolError(f"ffmpeg falhou (codigo {code}).{_tool_detail(out)}")

        if progress:
            progress(65, "Gerando RSM (PS2)...")
        # Build into the scratch dir, THEN move. rstm_build writes its OWN
        # intermediates (tmp_*.ads / tmp_*.wav) next to ITS OUTPUT
        # (rstm_build.py:59-61) and, when ps2str fails, leaves them behind
        # (rstm_build.py:122 only deletes on success). Aimed straight at
        # STREAMS/Music/<genre>/ that dumps junk into a folder that gets packed
        # into STREAMS.DAT — and makes the game folder's permissions/AV part of
        # the conversion. Scratch stays in %TEMP%; only the finished .rsm lands.
        temp_rsm = Path(temp_dir) / f"{source.stem}.rsm"
        code, out = _run(_tool_command(rstm, temp_wav, "-o", temp_rsm), cwd=rstm.parent, log=log)
        if code != 0:
            raise ToolError(f"rstm_build falhou (codigo {code}).{_tool_detail(out)}")
        _publish_rsm(temp_rsm, output)

    if progress:
        progress(100, "Concluido.")
    return output


# ---- conformidade do RSTM com o formato do proprio jogo ---------------------
# Medido nos 135 .rsm de musica que vieram do jogo (STREAMS/Music), TODOS iguais:
#   0x08 sample rate = 32000      0x1C loop start = 32 (= 1 frame estereo)
#   0x24 = 0xFFFFFFFF             dados comecam com 1 frame ZERADO (init do SPU)
#
# O rstm_build gera outra coisa: 44100 (porque e o que o nosso ffmpeg pede), loop
# start 0, 0x24 = 0 -- ele simplesmente NUNCA escreve esse campo -- e ainda REMOVE o
# frame de init ("wipe SPU initialization frame written by PS2STR, RSMs don't have
# these", rstm_build.py:152). Isso pode valer para o Bully; para a musica do MC3 e
# falso, e o resultado e faixa MUDA no jogo.
#
# Corrigir aqui, e nao no rstm_build.py, porque find_rstm_build() da prioridade ao
# rstm_build.EXE -- o .py nem chega a rodar.
RSTM_MAGIC = b"RSTM"
RSTM_HEADER_SIZE = 0x800
RSTM_NO_LOOP = 0xFFFFFFFF
# Taxa que o jogo usa em 100% da sua propria musica. Se um dia for provado que o
# streamer aguenta mais, e so mexer aqui.
MUSIC_SAMPLE_RATE = 32000


def _u32(buf, offset: int) -> int:
    return int.from_bytes(buf[offset:offset + 4], "little")


def _put_u32(buf: bytearray, offset: int, value: int) -> None:
    buf[offset:offset + 4] = int(value).to_bytes(4, "little")


def conform_rsm_to_game(path) -> list:
    """Ajusta um .rsm recem-gerado ao layout que o jogo usa. Devolve o que mudou.

    Idempotente: rodar de novo num arquivo ja conforme nao muda nada (e util,
    porque um .rsm de entrada pode ja estar correto)."""
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) <= RSTM_HEADER_SIZE or raw[:4] != RSTM_MAGIC:
        raise ToolError(f"Arquivo RSM invalido (sem cabecalho RSTM): {path.name}")

    header = bytearray(raw[:RSTM_HEADER_SIZE])
    body = raw[RSTM_HEADER_SIZE:]
    channels = _u32(header, 0x0C) or 2
    frame = 0x10 * channels
    mudou = []

    if body[:frame] != bytes(frame):
        # Devolve o frame de init que o rstm_build tirou. Os offsets de loop andam
        # junto com ele, para continuarem apontando para o mesmo audio.
        body = bytes(frame) + body
        _put_u32(header, 0x1C, _u32(header, 0x1C) + frame)
        _put_u32(header, 0x20, _u32(header, 0x20) + frame)
        _put_u32(header, 0x18, len(body))
        mudou.append("frame de init")
    elif _u32(header, 0x1C) == 0:
        # Ja havia um frame zerado (audio que comeca em silencio), mas o loop
        # apontava para dentro dele.
        _put_u32(header, 0x1C, frame)
        mudou.append("loop start")

    if _u32(header, 0x24) != RSTM_NO_LOOP:
        _put_u32(header, 0x24, RSTM_NO_LOOP)
        mudou.append("campo 0x24")

    if mudou:
        _make_writable(path)
        path.write_bytes(bytes(header) + body)
    return mudou


def _publish_rsm(temp_rsm: Path, output: Path) -> None:
    """Move a freshly built .rsm from scratch into the game folder."""
    if not temp_rsm.is_file() or temp_rsm.stat().st_size == 0:
        raise ToolError("rstm_build terminou sem gerar o arquivo RSM.")
    output.parent.mkdir(parents=True, exist_ok=True)
    _make_writable(output)  # extracted game files carry the read-only bit
    shutil.move(str(temp_rsm), str(output))
    conform_rsm_to_game(output)


# ---- ISO inspection (read-only) -------------------------------------------
# Mirrors the original _inspect_game_iso: mount the disc image with the native
# Windows PowerShell (Mount-DiskImage), read SYSTEM.CNF, confirm it is the
# supported Midnight Club 3 and that ASSETS.DAT/STREAMS.DAT are present, then
# ALWAYS dismount. This never writes anything.

def _find_powershell() -> str:
    return shutil.which("powershell") or "powershell"


def _powershell_quote(value) -> str:
    # PowerShell single-quoted strings: only the single quote needs escaping
    # (by doubling). Backslashes in Windows paths are literal here.
    return "'" + str(value).replace("'", "''") + "'"


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _parse_boot_id(system_cnf_text: str) -> str:
    match = _BOOT_ID_RE.search(system_cnf_text)
    return match.group(1).strip() if match else ""


def _mount_iso_drive(iso_path: Path, log: LogFn = None) -> Path:
    """Mount an .iso and return the assigned drive root (e.g. Path('E:\\'))."""
    command = (
        f"$img = Mount-DiskImage -ImagePath {_powershell_quote(iso_path)} -PassThru -ErrorAction Stop; "
        "Start-Sleep -Milliseconds 500; "
        "$vol = $img | Get-Volume | Where-Object { $_.DriveLetter } | Select-Object -First 1; "
        "if (-not $vol) { throw 'Nao foi possivel localizar a unidade montada.' }; "
        "Write-Output ($vol.DriveLetter + ':\\')"
    )
    code, out = _run([_find_powershell(), "-NoProfile", "-Command", command], log=log, timeout=MOUNT_TIMEOUT)
    if code != 0:
        raise ToolError("Nao foi possivel montar a ISO selecionada.")
    mounted = ""
    for line in out.splitlines():
        line = line.strip()
        if line.endswith(":\\"):
            mounted = line
    if not mounted:
        raise ToolError("A ISO foi montada, mas a letra da unidade nao foi encontrada.")
    return Path(mounted)


def _dismount_iso(iso_path: Path, log: LogFn = None) -> None:
    command = f"Dismount-DiskImage -ImagePath {_powershell_quote(iso_path)} -ErrorAction SilentlyContinue"
    _run([_find_powershell(), "-NoProfile", "-Command", command], log=log)


def inspect_iso(source, log: LogFn = None, progress: ProgressFn = None) -> dict:
    """Inspect a PS2 disc image (read-only). Returns a report dict:

        {iso_path, boot_id, supported, game_name, has_assets, has_streams}

    Raises ToolError if the file is missing, cannot be mounted, or is not a
    recognizable PS2 game image (no SYSTEM.CNF / no BOOT2). An unsupported but
    otherwise valid PS2 image is reported with supported=False (not an error).
    """
    iso_path = Path(source)
    if not iso_path.is_file():
        raise ToolError(f"Arquivo ISO nao encontrado: {iso_path}")

    if progress:
        progress(10, "Montando a ISO...")
    mounted_root = _mount_iso_drive(iso_path, log=log)
    try:
        if progress:
            progress(45, "Lendo SYSTEM.CNF...")
        system_cnf = mounted_root / "SYSTEM.CNF"
        if not system_cnf.is_file():
            raise ToolError("SYSTEM.CNF nao foi encontrado na ISO.")
        boot_id = _parse_boot_id(_read_text_best_effort(system_cnf))
        if not boot_id:
            raise ToolError("Nao foi possivel identificar o BOOT2 da ISO.")

        if progress:
            progress(75, "Verificando arquivos do jogo...")
        has_assets = (mounted_root / "ASSETS.DAT").is_file()
        has_streams = (mounted_root / "STREAMS.DAT").is_file()
        if not (has_assets and has_streams):
            raise ToolError("A ISO nao possui ASSETS.DAT e STREAMS.DAT no formato esperado.")

        supported = boot_id in SUPPORTED_BOOT_IDS
        if log:
            log(f"BOOT2={boot_id} | suportada={supported}")
        if progress:
            progress(100, "Concluido.")
        return {
            "iso_path": str(iso_path),
            "boot_id": boot_id,
            "supported": supported,
            "game_name": SUPPORTED_GAME_NAME,
            "has_assets": has_assets,
            "has_streams": has_streams,
        }
    finally:
        # Always release the drive, even on failure, so the ISO isn't left mounted.
        _dismount_iso(iso_path, log=log)


def assert_supported_iso(source, log: LogFn = None) -> dict:
    """Preflight de "Preparar projeto": recusa uma ISO que nao seja o jogo suportado.

    Antes disso o `inspect_iso` so era chamado pelo botao OPCIONAL "Validar ISO", entao
    dava para apontar o app para qualquer .iso e ele copiava ~4 GB, extraia os DATs e so
    entao explodia la dentro do strtbl com um traceback do Python -- a 91%. Checar o
    BOOT2 antes custa uns segundos e transforma isso numa frase que o usuario entende.
    """
    report = inspect_iso(source, log=log)
    if not report.get("supported"):
        raise ToolError(
            f"Esta ISO nao e a versao que o app sabe editar. "
            f"Detectado BOOT2={report.get('boot_id') or '?'}; o suportado e {SUPPORTED_GAME_NAME}. "
            "Outras versoes/regioes do jogo tem os arquivos internos em outro formato, "
            "e a extracao falharia no meio."
        )
    return report


# ---- Workspace + DAT rebuild + backup -------------------------------------
# The workspace is the folder that holds the extracted game files (ASSETS/,
# STREAMS/, "Arquivos da ISO"/ and the mcstrings files). Like the original
# Tkinter app, this defaults to the app's own folder (base_path). All the paths
# below mirror the original MC3MusicManager.__init__.


@dataclass(frozen=True)
class Workspace:
    """Locations of the extracted game files. Derived from a single base_path."""

    base_path: Path

    @property
    def assets_path(self) -> Path:
        return self.base_path / "ASSETS"

    @property
    def streams_path(self) -> Path:
        return self.base_path / "STREAMS"

    @property
    def game_files_path(self) -> Path:
        return self.base_path / "Arquivos da ISO"

    @property
    def playlists_root(self) -> Path:
        # NOTE: "audio" has NO accent — this exact spelling matters (bug source).
        return self.assets_path / "tune" / "audio" / "playlist" / "city"

    @property
    def root_strtbl_path(self) -> Path:
        return self.base_path / "mcstrings02.strtbl"

    @property
    def strtbl_path(self) -> Path:
        return self.assets_path / "fonts" / "mcstrings02.strtbl"

    @property
    def strings_json_path(self) -> Path:
        return self.base_path / "mcstrings02.json"

    @property
    def backups_dir(self) -> Path:
        return self.base_path / "backups"


def default_workspace() -> Workspace:
    return Workspace(PROJECT_ROOT)


# ---- persisted options (mirrors the original's project_state.json) ----------
OPTIONS_FILE = "options.json"
DEFAULT_OPTIONS = {
    "language": "pt-BR",
    "last_iso": "",
    "iso_output": "",
    "iso_volume_label": "MClub",
    "add_genre": "",
    "rebuild_after_add": False,
    "rebuild_after_remove": False,
    "remove_audio": True,
    "remove_playlists": True,
    "remove_strings": True,
    "playlist_presets": {},
}


# ---- UI translations (i18n) -------------------------------------------------
LOCALES_DIR = RESOURCE_ROOT / "frontend" / "locales"
LANGUAGES = ("pt-BR", "en", "es")


def load_translations() -> dict:
    """Load the {lang: {key: text}} tables from frontend/locales/*.json. Served to
    the frontend by Api.get_i18n() because a file:// page can't fetch() local JSON
    (WebView2/Chromium blocks it). Tolerant of missing/corrupt files."""
    out = {}
    for lang, fname in {"pt-BR": "pt.json", "en": "en.json", "es": "es.json"}.items():
        try:
            out[lang] = json.loads((LOCALES_DIR / fname).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[lang] = {}
    return out


def options_path(ws: Workspace) -> Path:
    return ws.base_path / OPTIONS_FILE


def load_options(ws: Workspace) -> dict:
    """Persisted options merged over DEFAULT_OPTIONS. Tolerant of a missing or
    corrupt file (falls back to a copy of the defaults). Unknown keys are dropped."""
    data = copy.deepcopy(DEFAULT_OPTIONS)
    path = options_path(ws)
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data.update({k: v for k, v in stored.items() if k in DEFAULT_OPTIONS})
        except (OSError, ValueError):
            pass
    return data


def save_options(ws: Workspace, data: dict) -> None:
    """Write options (merged over defaults) to base_path/options.json. Best-effort."""
    merged = copy.deepcopy(DEFAULT_OPTIONS)
    merged.update({k: v for k, v in (data or {}).items() if k in DEFAULT_OPTIONS})
    path = options_path(ws)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _make_writable(path)
        path.write_text(json.dumps(merged, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        pass


def _first_existing(*paths: Path) -> Optional[Path]:
    for path in paths:
        if path.is_file():
            return path
    return None


def find_dave() -> Optional[Path]:
    return _first_existing(TOOLS_ROOT / "dave.exe", TOOLS_ROOT / "dave.py")


def find_hash_build() -> Optional[Path]:
    return _first_existing(TOOLS_ROOT / "hash_build.exe", TOOLS_ROOT / "hash_build.py")


def find_strtbl() -> Optional[Path]:
    return _first_existing(TOOLS_ROOT / "strtbl.exe", TOOLS_ROOT / "strtbl.py")


def _tool_command(tool: Path, *args) -> list:
    """Build a command list for a PS2 tool: run .py via this interpreter, .exe directly."""
    if tool.suffix.lower() == ".py":
        if getattr(sys, "frozen", False):
            # DANGER: in a packaged build sys.executable is THIS GUI, not Python —
            # `MC3.exe dave.py ...` would relaunch the app instead of running the
            # tool (spawning windows forever). The build ships .exe tools; a .py
            # here means a broken install, so fail loudly instead.
            raise ToolError(
                f"A ferramenta {tool.name} so existe como .py, e este app esta empacotado "
                "(sem interpretador Python). Reinstale o app — ele deve trazer as "
                "ferramentas .exe em tools/."
            )
        head = [sys.executable, str(tool)]
    else:
        head = [str(tool)]
    return head + [str(arg) for arg in args]


def rebuild_tools_status() -> dict:
    dave = find_dave()
    hb = find_hash_build()
    st = find_strtbl()
    return {
        "dave": str(dave) if dave else None,
        "hash_build": str(hb) if hb else None,
        "strtbl": str(st) if st else None,
        "ready": bool(dave and hb),  # strtbl only needed for add/remove, not rebuild
    }


def publish_dat_to_game_files(ws: Workspace, dat_name: str, log: LogFn = None) -> Path:
    """Copy a freshly built .DAT from base_path into 'Arquivos da ISO'.

    Rebuilding only writes the .DAT to base_path; the game image is assembled
    from 'Arquivos da ISO', so the DAT must be published there to take effect.
    """
    source = ws.base_path / dat_name
    if not source.is_file():
        raise ToolError(f"{dat_name} nao encontrado apos a recompilacao: {source}")
    ws.game_files_path.mkdir(parents=True, exist_ok=True)
    target = ws.game_files_path / dat_name
    _make_writable(target)
    shutil.copy2(source, target)
    if log:
        log(f"Publicado em Arquivos da ISO: {target}")
    return target


REBUILD_SCRATCH = ".mc3_rebuild"


def _rebuild_scratch_dir(ws: Workspace) -> Path:
    """Pasta de trabalho da recompilacao, DENTRO de base_path.

    Fica no mesmo volume de proposito: os.replace() so e atomico dentro do mesmo
    sistema de arquivos, e um DAT tem >1 GB. O NOME do arquivo e preservado la
    dentro porque o hash_build deriva o .lst irmao do nome de saida (build_hash
    escreve STREAMS.LST ao lado de STREAMS.DAT) - construir num "STREAMS.DAT.tmp"
    produziria "STREAMS.DAT.LST" e deixaria o STREAMS.LST desatualizado.
    """
    scratch = ws.base_path / REBUILD_SCRATCH
    _rmtree(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    return scratch


def _publish_rebuilt(scratch: Path, ws: Workspace, dat_name: str, log: LogFn = None) -> Path:
    """Move o DAT recem-construido (e o .lst irmao, se houver) do scratch para
    base_path. So roda no sucesso: um `dave`/`hash_build` morto no meio - por
    "Cancelar" ou por falta de espaco - nao chega aqui, entao o DAT bom da rodada
    anterior continua intacto. Antes a ferramenta escrevia direto no destino, e o
    open(..., "wb") truncava o arquivo logo de cara: cancelar deixava um DAT
    corrompido na raiz.

    Custo aceito: durante a recompilacao existem 2 copias do DAT em disco.
    """
    built = scratch / dat_name
    if not built.is_file() or built.stat().st_size == 0:
        raise ToolError(f"A recompilacao terminou sem gerar {dat_name}.")
    target = ws.base_path / dat_name
    _make_writable(target)
    os.replace(str(built), str(target))
    # O .lst e subproduto do hash_build; move junto para nao ficar desatualizado.
    for sidecar in scratch.glob("*.[Ll][Ss][Tt]"):
        sidecar_target = ws.base_path / sidecar.name
        _make_writable(sidecar_target)
        os.replace(str(sidecar), str(sidecar_target))
        if log:
            log(f"Lista de nomes atualizada: {sidecar_target.name}")
    return target


def sweep_stale_scratch(ws: Workspace, log: LogFn = None) -> int:
    """Remove leftover conversion/tool scratch dirs a failed Windows cleanup may
    have left behind (ports the original's _sweep_stale_temp_dirs).

    New builds keep the WAV scratch in the OS temp dir, but an OLDER build wrote it
    inside STREAMS/Music/<genre>/ — a folder packed into STREAMS.DAT — so sweep the
    STREAMS tree (and the workspace root, where the strtbl tools scratch) to keep
    that junk out of the game archive. Matches the stdlib TemporaryDirectory name
    (tmp*) and our own conversion prefix (mc3_rsm_*)."""
    removed = 0
    seen = set()
    scans = []
    if ws.base_path.is_dir():
        # REBUILD_SCRATCH so sobra se o processo foi morto a forca (o finally do
        # rebuild ja limpa) - e cada sobra guarda >1 GB, entao vale varrer.
        scans += [ws.base_path.glob("tmp*"), ws.base_path.glob("mc3_rsm_*"),
                  ws.base_path.glob(REBUILD_SCRATCH)]
    if ws.streams_path.is_dir():
        scans += [ws.streams_path.rglob("tmp*"), ws.streams_path.rglob("mc3_rsm_*")]
    for scan in scans:
        for path in scan:
            if path.is_dir() and path not in seen:
                seen.add(path)
                _rmtree(path)
                removed += 1
                if log:
                    log(f"Scratch obsoleto removido: {path}")
    return removed


def rebuild_streams_dat(ws: Workspace, log: LogFn = None, progress: ProgressFn = None) -> Path:
    """Recompile STREAMS/ into STREAMS.DAT (hash_build) and publish it."""
    if progress:
        progress(5, "Recompilando STREAMS.DAT...")
    if not ws.streams_path.is_dir():
        raise ToolError(f"STREAMS nao encontrado: {ws.streams_path}")
    tool = find_hash_build()
    if tool is None:
        raise ToolError("hash_build nao encontrado em tools/.")

    # Never let a stale conversion scratch dir get packed into STREAMS.DAT.
    sweep_stale_scratch(ws, log=log)

    scratch = _rebuild_scratch_dir(ws)
    try:
        output = scratch / "STREAMS.DAT"
        if progress:
            progress(30, "Executando hash_build (MClub)...")
        code, out = _run(
            _tool_command(tool, "B", ws.streams_path, output, "-a", "MClub"),
            cwd=ws.base_path,
            log=log,
            input_text="y\n",
        )
        if code != 0:
            raise ToolError(f"hash_build falhou (codigo {code}).{_tool_detail(out)}")
        _publish_rebuilt(scratch, ws, "STREAMS.DAT", log=log)
    finally:
        _rmtree(scratch)

    if progress:
        progress(80, "Publicando STREAMS.DAT...")
    target = publish_dat_to_game_files(ws, "STREAMS.DAT", log=log)
    if progress:
        progress(100, "Concluido.")
    return target


def rebuild_assets_dat(ws: Workspace, log: LogFn = None, progress: ProgressFn = None) -> Path:
    """Recompile ASSETS/ into ASSETS.DAT (dave) and publish it.

    The -cf -fc 1 flags compress only the files known to be safe for MC3;
    compressing the wrong ones makes the game hang (see dave.py header).
    """
    if progress:
        progress(5, "Recompilando ASSETS.DAT...")
    if not ws.assets_path.is_dir():
        raise ToolError(f"ASSETS nao encontrado: {ws.assets_path}")
    tool = find_dave()
    if tool is None:
        raise ToolError("dave nao encontrado em tools/.")

    scratch = _rebuild_scratch_dir(ws)
    try:
        output = scratch / "ASSETS.DAT"
        if progress:
            progress(30, "Executando dave...")
        code, out = _run(
            _tool_command(tool, "B", ws.assets_path, output, "-ca", "-cn", "-cf", "-fc", "1"),
            cwd=ws.base_path,
            log=log,
            input_text="y\n",
        )
        if code != 0:
            raise ToolError(f"dave falhou (codigo {code}).{_tool_detail(out)}")
        _publish_rebuilt(scratch, ws, "ASSETS.DAT", log=log)
    finally:
        _rmtree(scratch)

    if progress:
        progress(80, "Publicando ASSETS.DAT...")
    target = publish_dat_to_game_files(ws, "ASSETS.DAT", log=log)
    if progress:
        progress(100, "Concluido.")
    return target


def rebuild_all(ws: Workspace, log: LogFn = None, progress: ProgressFn = None) -> dict:
    """STREAMS.DAT then ASSETS.DAT, both published. Mirrors _rebuild_all_impl."""
    def _sub(low: float, high: float) -> ProgressFn:
        if not progress:
            return None
        return lambda pct, text: progress(low + (high - low) * pct / 100.0, text)

    if progress:
        progress(2, "Iniciando recompilacao completa...")
    streams = rebuild_streams_dat(ws, log=log, progress=_sub(2, 50))
    assets = rebuild_assets_dat(ws, log=log, progress=_sub(50, 100))
    if progress:
        progress(100, "DATs recompilados e publicados.")
    return {"streams_target": streams, "assets_target": assets}


# ---- Backup / restore ------------------------------------------------------
# A backup session is a timestamped folder under backups/ with a manifest that
# records, per file, whether it EXISTED before. On restore, files that existed
# are copied back; files that did not exist are DELETED — so a partial add can
# be fully undone. Mirrors the original _backup_file / restore machinery.

_BACKUP_MANIFEST = "backup_manifest.json"

# Chave usada para pendurar o caminho do backup numa excecao. add_songs/remove_songs
# fazem o trabalho por etapas (converte -> playlists -> strings): uma falha no meio
# deixa estado PARCIAL. O backup para desfazer ja existe, mas antes a UI so dizia
# "falhou" e o caminho ficava perdido no meio do log - o usuario nao tinha como
# saber que bastava restaurar. Agora ele viaja junto com o erro ate a tela.
BACKUP_ATTR = "mc3_backup"


def create_backup_session(ws: Workspace, label: str, timestamp: Optional[str] = None) -> Path:
    ts = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ws.backups_dir / f"{ts}_{label}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"label": label, "created_at": ts, "files": {}}
    (backup_dir / _BACKUP_MANIFEST).write_text(
        json.dumps(manifest, indent=4) + "\n", encoding="utf-8"
    )
    return backup_dir


def _backup_relative(ws: Workspace, source: Path) -> Path:
    try:
        return source.relative_to(ws.base_path)
    except ValueError:
        return Path(source.name)


def _read_manifest(backup_root: Path) -> dict:
    manifest_path = backup_root / _BACKUP_MANIFEST
    if manifest_path.is_file():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    return {"label": backup_root.name, "created_at": "", "files": {}}


def _write_manifest(backup_root: Path, manifest: dict) -> None:
    (backup_root / _BACKUP_MANIFEST).write_text(
        json.dumps(manifest, indent=4) + "\n", encoding="utf-8")


def backup_files(ws: Workspace, sources, backup_root: Path) -> int:
    """Registra N arquivos no manifesto com UMA gravacao, copiando os que existem.
    Devolve quantos foram copiados.

    O backup manual salva TODOS os .play do workspace (dezenas); reescrever o
    manifesto inteiro a cada arquivo fazia disso um O(n^2) de I/O de JSON."""
    manifest = _read_manifest(backup_root)
    files = manifest.setdefault("files", {})
    copied = 0
    for source in sources:
        source = Path(source)
        existed = source.exists()
        relative = _backup_relative(ws, source)
        files[relative.as_posix()] = {"existed": existed}
        if not existed:
            continue
        target = backup_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    _write_manifest(backup_root, manifest)
    return copied


def backup_file(ws: Workspace, source, backup_root: Path) -> None:
    """Record a file in the backup manifest and copy it if it currently exists."""
    backup_files(ws, [source], backup_root)


def latest_backup_dir(ws: Workspace) -> Optional[Path]:
    if not ws.backups_dir.is_dir():
        return None
    sessions = [p for p in ws.backups_dir.iterdir() if p.is_dir() and (p / _BACKUP_MANIFEST).is_file()]
    if not sessions:
        return None
    # Names start with YYYYMMDD_HHMMSS so lexical sort == chronological.
    return sorted(sessions, key=lambda p: p.name)[-1]


def restore_backup(ws: Workspace, backup_root, log: LogFn = None) -> dict:
    """Restore a backup session. Files marked existed=True are copied back;
    files marked existed=False are DELETED (they were created by the operation)."""
    backup_root = Path(backup_root)
    manifest_path = backup_root / _BACKUP_MANIFEST
    if not manifest_path.is_file():
        raise ToolError(f"Manifest de backup nao encontrado: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    restored = 0
    removed = 0
    for rel_text, info in manifest.get("files", {}).items():
        relative = Path(rel_text)
        dest = ws.base_path / relative
        if info.get("existed"):
            source = backup_root / relative
            if source.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                _make_writable(dest)
                shutil.copy2(source, dest)
                restored += 1
                if log:
                    log(f"Restaurado: {relative.as_posix()}")
        else:
            if dest.exists():
                _make_writable(dest)  # read-only files can't be unlinked on Windows
                dest.unlink()
                removed += 1
                if log:
                    log(f"Removido (nao existia antes do backup): {relative.as_posix()}")
    if log:
        log(f"Restauracao concluida: {restored} restaurado(s), {removed} removido(s).")
    return {"restored": restored, "removed": removed, "backup": str(backup_root)}


# ---- Add music -------------------------------------------------------------
# Ports the destructive add pipeline (_apply_add_specs + helpers). A "spec" is a
# plain dict describing one song to install; add_songs() takes a list, so the
# same core serves single-song and batch. All naming/normalization mirrors the
# original so the game reads the result identically.

GENRES = ("Dancehall", "Drum_N_Bass", "HipHop", "Instrumental", "Rock", "Techno")
LANGUAGE_CONNECTORS = ("by", "de", "par", "von", "di", "by")
GENRE_DEFAULT_PLAYLISTS = {
    "Dancehall": "dance_hall_race_music.play",
    "Drum_N_Bass": "drums_bass_race_music.play",
    "HipHop": "rap_race_music.play",
    "Instrumental": "garage.play",
    "Rock": "pop_race_music.play",
    "Techno": "techno_race_music.play",
}
CITY_PLAYLIST_NAMES = frozenset({"atlanta.play", "detroit.play", "sd.play", "tokyo.play"})
# Playlists that curate their OWN tracks and must never receive user songs via the
# "already contains this genre" heuristic. frontend.play is the MENU music: it holds
# HipHop tracks, so the original's content match dragged HipHop adds into it — but NO
# stock song is listed there (proved against the real game data). Adding songs there
# would silently replace the menu music.
PLAYLIST_CONTENT_MATCH_EXCLUDE = frozenset({"frontend.play"})
DEFAULT_TITLE_PLACEHOLDER = "MusicName"
DEFAULT_ARTIST_PLACEHOLDER = "SingerName"
ASSET_NAME_MAX_LENGTH = 40
ASSET_NAME_NOISE_TOKENS = {
    "official", "video", "audio", "lyrics", "lyric", "edit", "speed", "sped",
    "up", "underwater", "remaster", "remastered", "hq", "hd", "4k", "mp3", "wav",
}
ASSET_NAME_NOISE_NUMBERS = {"96", "112", "128", "160", "192", "224", "256", "320"}

_SONG_FONT = {"name": "smallspace", "scale32": [1.0, 1.0], "scale8": [0, 0], "size": 15}


# -- text / name normalization --

def _read_text(path) -> tuple[str, str]:
    path = Path(path)
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ToolError(f"Nao foi possivel ler {path}")


def _write_text(path, text: str, encoding: str = "utf-8") -> None:
    _make_writable(path)  # .play files from an extracted ISO are read-only
    Path(path).write_text(text, encoding=encoding, newline="\n")


def normalize_game_text(value) -> str:
    cleaned = str(value or "")
    # Strip a leading BOM (real U+FEFF or its cp1252 mojibake) some taggers embed.
    bom = chr(0xFEFF)
    mojibake = chr(0xEF) + chr(0xBB) + chr(0xBF)
    while cleaned.startswith(bom):
        cleaned = cleaned[1:]
    if cleaned.startswith(mojibake):
        cleaned = cleaned[3:]
    cleaned = cleaned.replace(chr(34), chr(39))  # double-quote -> apostrophe
    cleaned = " ".join(cleaned.split())
    return cleaned.strip(" -_")


def default_song_title(value) -> str:
    return normalize_game_text(value) or DEFAULT_TITLE_PLACEHOLDER


def default_song_artist(value) -> str:
    return normalize_game_text(value) or DEFAULT_ARTIST_PLACEHOLDER


def _sanitize_asset_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    return cleaned.strip("_")


def _is_noise_token(lowered: str) -> bool:
    return (
        lowered in ASSET_NAME_NOISE_TOKENS
        or lowered in ASSET_NAME_NOISE_NUMBERS
        or bool(re.fullmatch(r"\d{5,}", lowered))
        or bool(re.fullmatch(r"\d+k", lowered))
    )


def _compact_asset_component(value: str) -> str:
    cleaned = _sanitize_asset_name(normalize_game_text(value))
    if not cleaned:
        return ""
    tokens = [t for t in cleaned.split("_") if t and not _is_noise_token(t.lower())]
    return "_".join(tokens)


def coerce_asset_name(asset_name: str, *, artist: str = "", title: str = "") -> str:
    """Produce a filesystem/key-safe internal name, capped at 40 chars."""
    manual_seed = asset_name.strip()
    if manual_seed:
        raw_seed = manual_seed
    elif artist and title:
        raw_seed = f"{artist}_{title}"
    else:
        raw_seed = artist or title
    sanitized_seed = _sanitize_asset_name(raw_seed)
    if not sanitized_seed:
        return ""

    compact_artist = _compact_asset_component(artist)
    compact_title = _compact_asset_component(title)
    compact_seed = _compact_asset_component(sanitized_seed)

    candidates = []
    if compact_artist and compact_title:
        candidates.append(f"{compact_artist}_{compact_title}")
    elif compact_title:
        candidates.append(compact_title)
    elif compact_artist:
        candidates.append(compact_artist)
    if compact_seed:
        candidates.append(compact_seed)
    candidates.append(sanitized_seed)

    seen = set()
    ordered = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)
    for candidate in ordered:
        if len(candidate) <= ASSET_NAME_MAX_LENGTH:
            return candidate

    base = ordered[0] if ordered else sanitized_seed
    suffix = hashlib.sha1(sanitized_seed.encode("utf-8")).hexdigest()[:6]
    cutoff = max(8, ASSET_NAME_MAX_LENGTH - len(suffix) - 1)
    trimmed = base[:cutoff].rstrip("_")
    return f"{trimmed}_{suffix}" if trimmed else suffix


# -- audio tag / filename guessing --

def find_ffprobe() -> Optional[Path]:
    local = TOOLS_DIR / "ffprobe.exe"
    if local.is_file():
        return local
    found = shutil.which("ffprobe")
    return Path(found) if found else None


def read_audio_tags(source, log: LogFn = None) -> dict:
    source = Path(source)
    payload = {"title": "", "artist": "", "provider": ""}
    ffprobe = find_ffprobe()
    if not source.is_file() or ffprobe is None:
        return payload
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_entries",
             "format_tags=title,artist,album_artist", "-of", "json", str(source)],
            cwd=str(source.parent), text=True, encoding="utf-8", errors="replace",
            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=FFPROBE_TIMEOUT,
        )
        if result.returncode == 0 and result.stdout.strip():
            tags = (json.loads(result.stdout).get("format") or {}).get("tags") or {}
            title = normalize_game_text(tags.get("title", ""))
            artist = normalize_game_text(tags.get("artist", "") or tags.get("album_artist", ""))
            if title or artist:
                payload = {"title": title, "artist": artist, "provider": "Tags locais"}
    except Exception as exc:  # noqa: BLE001 - tag reading is best-effort
        if log:
            log(f"Aviso: falha ao ler tags de {source.name}: {exc}")
    return payload


def _clean_source_stem(stem: str) -> str:
    cleaned = stem.replace("_", " ").strip()
    cleaned = re.sub(
        r"\s*[\[(](official(\s+music|\s+audio|\s+video)?|audio|video|lyrics?|hq|hd|4k|remaster(?:ed)?|live)[\])]\s*$",
        "", cleaned, flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", cleaned).strip(" -_")


def _clean_detected_title(value: str) -> str:
    normalized = normalize_game_text(value)
    kept = []
    for token in normalized.split():
        compact = re.sub(r"[^A-Za-z0-9]+", "", token).lower()
        if not compact or _is_noise_token(compact):
            continue
        kept.append(token)
    return " ".join(kept).strip(" -_") or normalized


def split_source_metadata(stem: str) -> tuple[str, str]:
    """Best-effort '(title, artist)' from a filename stem. Simplified vs the
    original: keeps the dash-separator patterns, drops the fuzzy no-dash fallback."""
    cleaned = _clean_source_stem(stem)
    patterns = [
        r"^(?P<artist>.+?)\s+-\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s+–\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s+—\s+(?P<title>.+)$",
        r"^(?P<artist>.+?)\s*_\-_\s*(?P<title>.+)$",
        r"^(?P<artist>.+?)\s*-\s*(?P<title>.+)$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned)
        if not match:
            continue
        artist = re.sub(r"\s+", " ", match.group("artist")).strip(" -_")
        title = _clean_detected_title(re.sub(r"\s+", " ", match.group("title")).strip(" -_"))
        if artist and title:
            return title, artist
    return _clean_detected_title(cleaned), ""


def source_guess(source_value, genre: Optional[str] = None, log: LogFn = None) -> dict:
    """Guess title/artist/asset_name for an audio file (tags first, then name)."""
    source = Path(source_value) if source_value else None
    if source is None or not str(source).strip():
        return {"source": None, "title": "", "artist": "", "asset_name": "",
                "display_name": "", "string_key": "", "playlist_entry": "", "detected_by": ""}
    tags = read_audio_tags(source, log=log)
    name_title, name_artist = split_source_metadata(source.stem)
    title = normalize_game_text(tags.get("title") or name_title)
    artist = normalize_game_text(tags.get("artist") or name_artist)
    asset_seed = f"{artist}_{title}" if artist and title else title or source.stem
    asset_name = coerce_asset_name(asset_seed, artist=artist, title=title)
    resolved_genre = (genre or GENRES[0]).strip() or GENRES[0]
    return {
        "source": str(source),
        "title": title,
        "artist": artist,
        "asset_name": asset_name,
        "display_name": f"{artist} - {title}" if artist and title else title or source.name,
        "string_key": string_key(resolved_genre, asset_name) if asset_name else "",
        "playlist_entry": playlist_entry(resolved_genre, asset_name) if asset_name else "",
        "detected_by": tags.get("provider") or "Nome do arquivo",
    }


# -- keys / string-table entries --

def string_key(genre: str, asset_name: str) -> str:
    return f"music_{genre}_{asset_name}"


def playlist_entry(genre: str, asset_name: str) -> str:
    return f"music\\{genre}\\{asset_name}"


def _playlist_entry_sort_key(entry: str) -> tuple:
    match = re.match(r"music\\([^\\]+)\\(.+)$", entry, flags=re.IGNORECASE)
    if not match:
        return ("", entry.lower())
    return (match.group(1).lower(), match.group(2).lower())


def _song_text_for_language(song: str, artist: str, language_key: str, template_text: str = "") -> str:
    match = re.search(r"(\d+)$", language_key)
    index = int(match.group(1)) if match else 0
    connector = LANGUAGE_CONNECTORS[index] if index < len(LANGUAGE_CONNECTORS) else "by"
    if index == 5 and template_text == "":
        return ""
    return f"\"{song}\"\n{connector} {artist}"


def _find_song_template(entries: dict, genre: str) -> dict:
    for key, value in entries.items():
        if key.startswith(f"music_{genre}_") and isinstance(value, dict):
            return copy.deepcopy(value)
    for key, value in entries.items():
        if key.startswith("music_") and isinstance(value, dict):
            return copy.deepcopy(value)
    return {}


def song_entry(entries: dict, genre: str, song: str, artist: str) -> dict:
    """Build the 6-language string-table entry for a song, cloning an existing
    genre entry as the font/format template (or a default if none exists)."""
    template = _find_song_template(entries, genre)
    if not template:
        template = {
            f"Language {index:02d}": {"text": "", "font": dict(_SONG_FONT)}
            for index in range(6)
        }
    for language_key, language_value in template.items():
        if not isinstance(language_value, dict):
            continue
        template_text = str(language_value.get("text", ""))
        language_value["text"] = _song_text_for_language(song, artist, language_key, template_text)
        language_value.setdefault("font", dict(_SONG_FONT))
    return template


def insert_entry_near_genre_block(entries: dict, key: str, value: dict, genre: str) -> dict:
    """Insert `key` alphabetically within its genre's block, keeping the file
    organized the way the game's tools produced it."""
    if key in entries:
        entries[key] = value
        return entries
    genre_prefix = f"music_{genre}_"
    items = list(entries.items())
    genre_indexes = [i for i, (ek, _ev) in enumerate(items) if ek.startswith(genre_prefix)]
    if not genre_indexes:
        entries[key] = value
        return entries
    insert_at = genre_indexes[-1] + 1
    for i in genre_indexes:
        if key.lower() < items[i][0].lower():
            insert_at = i
            break
    new_items = items[:insert_at] + [(key, value)] + items[insert_at:]
    return dict(new_items)


# -- playlists / strtbl round-trip --

def update_playlist(playlist_path, entry: str, mode: str) -> bool:
    """Add/remove a `music\\Genre\\asset` entry in a .play file and refresh the
    num_songs header. Returns True if the file changed."""
    playlist_path = Path(playlist_path)
    content, encoding = _read_text(playlist_path)
    lines = content.splitlines()
    stripped = [line.strip() for line in lines]
    changed = False

    if mode == "add":
        if entry not in stripped:
            insert_at = len(lines)
            music_indexes = [i for i, line in enumerate(stripped) if line.lower().startswith("music\\")]
            entry_genre, entry_asset = _playlist_entry_sort_key(entry)
            if music_indexes:
                insert_at = music_indexes[-1] + 1
                genre_indexes = [i for i, line in enumerate(stripped)
                                 if _playlist_entry_sort_key(line)[0] == entry_genre]
                if genre_indexes:
                    insert_at = genre_indexes[-1] + 1
                    for i in genre_indexes:
                        if (entry_genre, entry_asset) < _playlist_entry_sort_key(stripped[i]):
                            insert_at = i
                            break
            elif stripped and stripped[0].lower().startswith("num_songs:"):
                insert_at = 1
            lines.insert(insert_at, entry)
            changed = True
    elif mode == "remove":
        new_lines = [line for line in lines if line.strip() != entry]
        changed = len(new_lines) != len(lines)
        lines = new_lines
    else:
        raise ToolError("Modo de playlist invalido.")

    if not changed:
        return False

    song_count = sum(1 for line in lines if line.strip().lower().startswith("music\\"))
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("num_songs:"):
            lines[i] = f"num_songs: {song_count}"
            break
    else:
        lines.insert(0, f"num_songs: {song_count}")

    _write_text(playlist_path, "\n".join(lines).rstrip() + "\n", encoding)
    return True


def _playlist_path_matches_genre(playlist_path, genre: str) -> bool:
    """A playlist 'matches' a genre if it is a city block, the genre's default
    race playlist, or already contains a music\\{genre}\\ entry. Ports the
    original's _playlist_path_matches_genre."""
    playlist_path = Path(playlist_path)
    if playlist_path.name in CITY_PLAYLIST_NAMES:
        return True
    if playlist_path.name == GENRE_DEFAULT_PLAYLISTS.get(genre, ""):
        return True
    if playlist_path.name in PLAYLIST_CONTENT_MATCH_EXCLUDE:
        return False  # menu music — never auto-matched by content
    try:
        content, _encoding = _read_text(playlist_path)
    except Exception:  # noqa: BLE001 - unreadable playlist just doesn't match
        return False
    return f"music\\{genre}\\".lower() in content.lower()


def playlist_targets_for_genre(selected_playlists: list, genre: str) -> list:
    """Expand the user's playlist selection for one song's genre — the rule the
    game depends on for songs to APPEAR (ports _playlist_targets_for_genre):
    every selected CITY playlist also pulls in its sibling genre race playlist
    (e.g. atlanta.play -> + techno_race_music.play); non-city playlists are kept
    only if they match the genre. Missing this expansion left added songs in the
    cruise blocks only, invisible in races."""
    targets = []
    default_name = GENRE_DEFAULT_PLAYLISTS.get(genre, "")
    for playlist in selected_playlists:
        playlist = Path(playlist)
        if playlist.name in CITY_PLAYLIST_NAMES:
            targets.append(playlist)
            if default_name:
                sibling = playlist.with_name(default_name)
                if sibling.is_file():
                    targets.append(sibling)
        elif _playlist_path_matches_genre(playlist, genre):
            targets.append(playlist)
    return list(dict.fromkeys(targets))


def decode_strings(ws: Workspace, log: LogFn = None) -> dict:
    """Decode the workspace mcstrings02.strtbl to a dict via `strtbl dec`."""
    tool = find_strtbl()
    if tool is None:
        raise ToolError("strtbl nao encontrado em tools/.")
    if not ws.strtbl_path.is_file():
        raise ToolError(f"mcstrings02.strtbl nao encontrado: {ws.strtbl_path}")
    ws.base_path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(ws.base_path), ignore_cleanup_errors=True) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        temp_strtbl = temp_dir / "mcstrings02.strtbl"
        shutil.copy2(ws.strtbl_path, temp_strtbl)
        code, out = _run(_tool_command(tool, "dec", temp_strtbl), cwd=ws.base_path, log=log, input_text="y\n")
        if code != 0:
            raise ToolError(f"Falha ao decodificar mcstrings02.strtbl.{_tool_detail(out)}")
        return json.loads((temp_dir / "mcstrings02.json").read_text(encoding="utf-8"))


def sync_strings_json(ws: Workspace, data: dict) -> None:
    _make_writable(ws.strings_json_path)
    ws.strings_json_path.write_text(
        json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def compile_strings_json_to_strtbl(ws: Workspace, log: LogFn = None) -> None:
    """Compile the workspace mcstrings02.json back to .strtbl via `strtbl enc`
    and publish it to both the root and ASSETS/fonts copies."""
    tool = find_strtbl()
    if tool is None:
        raise ToolError("strtbl nao encontrado em tools/.")
    if not ws.strings_json_path.is_file():
        raise ToolError("mcstrings02.json nao foi encontrado para compilar.")
    with tempfile.TemporaryDirectory(dir=str(ws.base_path), ignore_cleanup_errors=True) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        temp_json = temp_dir / "mcstrings02.json"
        content, _encoding = _read_text(ws.strings_json_path)
        temp_json.write_text(json.dumps(json.loads(content), indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        code, out = _run(_tool_command(tool, "enc", temp_json), cwd=ws.base_path, log=log, input_text="y\n")
        if code != 0:
            raise ToolError(f"Falha ao compilar mcstrings02.json para mcstrings02.strtbl.{_tool_detail(out)}")
        compiled = temp_dir / "mcstrings02.strtbl"
        _make_writable(ws.root_strtbl_path)
        shutil.copy2(compiled, ws.root_strtbl_path)
        ws.strtbl_path.parent.mkdir(parents=True, exist_ok=True)
        _make_writable(ws.strtbl_path)
        shutil.copy2(compiled, ws.strtbl_path)
        if log:
            log(f"mcstrings02.strtbl atualizado em {ws.root_strtbl_path} e {ws.strtbl_path}")


def _load_strings_json_data(ws: Workspace, fallback_data: Optional[dict] = None) -> dict:
    if ws.strings_json_path.is_file():
        try:
            content, _ = _read_text(ws.strings_json_path)
            return json.loads(content)
        except Exception:  # noqa: BLE001 - fall back to the decoded strtbl data
            pass
    return json.loads(json.dumps(fallback_data or {}, ensure_ascii=False))


# -- specs + orchestration --

def build_add_spec(ws: Workspace, source, title: str, artist: str, genre: str, asset_name: str) -> dict:
    """Build one add-spec. Reads tags only if asset_name is empty (short-circuit)."""
    source = Path(source)
    cleaned_title = default_song_title(title)
    cleaned_artist = default_song_artist(artist)
    cleaned_genre = genre.strip()
    effective_seed = asset_name.strip() or source_guess(source, genre=cleaned_genre).get("asset_name", "")
    cleaned_asset = coerce_asset_name(
        effective_seed,
        artist="" if cleaned_artist == DEFAULT_ARTIST_PLACEHOLDER else cleaned_artist,
        title="" if cleaned_title == DEFAULT_TITLE_PLACEHOLDER else cleaned_title,
    )
    if not cleaned_title or not cleaned_artist or not cleaned_asset or cleaned_genre not in GENRES:
        raise ToolError("Preencha titulo, artista, genero e nome interno do arquivo.")
    return {
        "source": source,
        "title": cleaned_title,
        "artist": cleaned_artist,
        "genre": cleaned_genre,
        "asset_name": cleaned_asset,
        "stream_target": ws.streams_path / "Music" / cleaned_genre / f"{cleaned_asset}.rsm",
        "string_key": string_key(cleaned_genre, cleaned_asset),
        "playlist_entry": playlist_entry(cleaned_genre, cleaned_asset),
    }


def existing_spec_targets(specs: list) -> list:
    return [spec["stream_target"] for spec in specs if Path(spec["stream_target"]).exists()]


def find_duplicate_spec_targets(specs: list) -> list:
    """Specs do mesmo lote que gravariam no MESMO .rsm.

    Retorna [(nome_do_arquivo, [caminhos de origem]), ...] apenas para os destinos
    reivindicados por mais de uma spec — a ordem segue a do lote."""
    by_target: dict = {}
    for spec in specs:
        by_target.setdefault(Path(spec["stream_target"]), []).append(spec["source"])
    return [(target.name, sources) for target, sources in by_target.items() if len(sources) > 1]


def add_songs(ws: Workspace, specs: list, *, allow_overwrite: bool = False,
              backup_label: str = "add_music", log: LogFn = None, progress: ProgressFn = None,
              timestamp: Optional[str] = None) -> dict:
    """Install songs into the workspace (destructive). Mirrors _apply_add_specs:
    backup -> per song: convert to RSM, update playlists, add string entries ->
    write mcstrings02.json + recompile STRTBL. Each spec needs the keys produced
    by build_add_spec plus a 'playlist_targets' list of .play Paths."""
    if not specs:
        raise ToolError("Nenhuma musica valida foi preparada para adicao.")

    # Colisao DENTRO do proprio lote. coerce_asset_name normaliza agressivamente
    # (tira "official", "video", "hq", bitrates...), entao dois arquivos distintos
    # — duas versoes da mesma faixa, ou faixas homonimas — convergem para o mesmo
    # .rsm. existing_spec_targets so olha o DISCO, entao nao ve isso: o lote rodava
    # "com sucesso" e a segunda faixa sobrescrevia a primeira em silencio.
    duplicates = find_duplicate_spec_targets(specs)
    if duplicates:
        detail = "; ".join(
            f"{name} <- " + ", ".join(Path(src).name for src in sources)
            for name, sources in duplicates
        )
        raise ToolError(
            f"Adicao cancelada: {len(duplicates)} nome(s) interno(s) repetido(s) no lote - "
            f"as faixas se sobrescreveriam. Edite o titulo/artista (ou o nome interno) "
            f"para diferencia-las. {detail}"
        )

    existing = existing_spec_targets(specs)
    if existing and not allow_overwrite:
        raise ToolError(
            f"Adicao cancelada: {len(existing)} arquivo(s) .rsm de destino ja existem. "
            "Marque a opcao de sobrescrever para continuar."
        )

    backup_dir = create_backup_session(ws, backup_label, timestamp=timestamp)
    if log:
        log(f"Backup preparado em {backup_dir}")

    unique_playlists = []
    seen = set()
    for spec in specs:
        for playlist in spec.get("playlist_targets", []):
            if playlist not in seen:
                seen.add(playlist)
                unique_playlists.append(playlist)

    backup_files(ws, [ws.root_strtbl_path, ws.strtbl_path, ws.strings_json_path]
                 + list(unique_playlists)
                 + [spec["stream_target"] for spec in specs], backup_dir)

    try:
        return _apply_add_specs(ws, specs, backup_dir, log=log, progress=progress)
    except BaseException as exc:
        setattr(exc, BACKUP_ATTR, str(backup_dir))
        raise


def _apply_add_specs(ws: Workspace, specs: list, backup_dir: Path,
                     log: LogFn = None, progress: ProgressFn = None) -> dict:
    strings_data = decode_strings(ws, log=log)
    entries = strings_data.setdefault("data", {})
    strings_json_data = _load_strings_json_data(ws, strings_data)
    strings_json_entries = strings_json_data.setdefault("data", {})

    total = len(specs)
    playlist_changes = 0
    for index, spec in enumerate(specs, start=1):
        if progress:
            progress(12.0 + ((index - 1) / max(total, 1)) * 56.0,
                     f"[{index}/{total}] {Path(spec['source']).name}...")
        spec["stream_target"].parent.mkdir(parents=True, exist_ok=True)
        convert_audio_to_rsm(spec["source"], spec["stream_target"], log=log)
        if log:
            log(f"RSM instalado em {spec['stream_target']}")

        for playlist in spec.get("playlist_targets", []):
            if update_playlist(playlist, spec["playlist_entry"], "add"):
                playlist_changes += 1
                if log:
                    log(f"Playlist atualizada: {Path(playlist).name} -> {spec['asset_name']}")

        new_entry = song_entry(entries, spec["genre"], spec["title"], spec["artist"])
        strings_data["data"] = insert_entry_near_genre_block(entries, spec["string_key"], new_entry, spec["genre"])
        entries = strings_data["data"]

        text_entry = song_entry(strings_json_entries, spec["genre"], spec["title"], spec["artist"])
        strings_json_data["data"] = insert_entry_near_genre_block(
            strings_json_entries, spec["string_key"], text_entry, spec["genre"])
        strings_json_entries = strings_json_data["data"]

    if progress:
        progress(74, "Gravando mcstrings02.json e recompilando STRTBL...")
    sync_strings_json(ws, strings_json_data)
    compile_strings_json_to_strtbl(ws, log=log)
    if progress:
        progress(100, "Concluido.")
    return {"added": total, "playlist_changes": playlist_changes, "backup": str(backup_dir)}


# ---- Prepare project: extract ISO -> editable workspace (steps 3 + 4) ------
# The mirror of the rebuild functions: the SAME PS2 tools in EXTRACT mode (X)
# instead of build mode (B). Populates ASSETS/, STREAMS/ and mcstrings02.json
# from the game's ASSETS.DAT/STREAMS.DAT. Ports _prepare_project_from_iso_worker
# and _decompile_workspace_from_game_files_worker. Read-only from the ISO;
# writes only into fresh workspace folders.


def find_streams_list() -> Optional[Path]:
    """The namelist that lets hash_build recover real .rsm names from hashes."""
    for candidate in (TOOLS_ROOT / "MC3_PS2_Streams.lst", PROJECT_ROOT / "MC3_PS2_Streams.lst"):
        if candidate.is_file():
            return candidate
    return None


def _rmtree(path) -> None:
    """rmtree that clears the read-only bit on failure (ISO extracts are RO)."""
    path = Path(path)
    if not path.exists():
        return

    def _on_error(func, p, _exc):
        try:
            Path(p).chmod(stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=_on_error)  # py>=3.12
    except TypeError:  # pragma: no cover - very old Python
        shutil.rmtree(path, onerror=lambda f, p, e: _on_error(f, p, e))


def _copy_tree_with_progress(source_root, target_root, log: LogFn = None, progress: ProgressFn = None) -> int:
    source_root = Path(source_root)
    target_root = Path(target_root)
    all_paths = sorted(source_root.rglob("*"))
    file_count = sum(1 for p in all_paths if p.is_file())
    target_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in all_paths:
        relative = path.relative_to(source_root)
        target = target_root / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _make_writable(target)
        shutil.copy2(path, target)
        copied += 1
        if progress and file_count:
            progress((copied / file_count) * 100.0, f"Copiando ({copied}/{file_count})...")
    if log:
        log(f"{copied} arquivo(s) copiados para {target_root}")
    return copied


def copy_iso_to_game_files(ws: Workspace, iso_source, log: LogFn = None,
                           progress: ProgressFn = None, verify: bool = True) -> int:
    """Step 3: mount the ISO, copy all its files into 'Arquivos da ISO', dismount.

    verify=False so quando quem chamou JA rodou o assert_supported_iso (evita montar
    a imagem duas vezes)."""
    iso_source = Path(iso_source)
    if not iso_source.is_file():
        raise ToolError(f"Arquivo ISO nao encontrado: {iso_source}")
    if verify:
        if progress:
            progress(1, "Conferindo a ISO...")
        assert_supported_iso(iso_source, log=log)
    if progress:
        progress(2, "Montando a ISO...")
    mounted = _mount_iso_drive(iso_source, log=log)
    try:
        return _copy_tree_with_progress(mounted, ws.game_files_path, log=log, progress=progress)
    finally:
        _dismount_iso(iso_source, log=log)


def _extract_assets_dat(ws: Workspace, log: LogFn = None) -> None:
    tool = find_dave()
    if tool is None:
        raise ToolError("dave nao encontrado em tools/.")
    code, out = _run(_tool_command(tool, "X", ws.base_path / "ASSETS.DAT"), cwd=ws.base_path, log=log)
    if code != 0 or not ws.assets_path.is_dir():
        raise ToolError(f"Falha ao extrair ASSETS.DAT.{_tool_detail(out)}")


def _extract_streams_dat(ws: Workspace, log: LogFn = None) -> None:
    tool = find_hash_build()
    if tool is None:
        raise ToolError("hash_build nao encontrado em tools/.")
    lst = find_streams_list()
    if lst is None:
        raise ToolError("MC3_PS2_Streams.lst nao encontrado em tools/.")
    code, out = _run(
        _tool_command(tool, "X", ws.base_path / "STREAMS.DAT", "-nl", lst, "-a", "mclub", "-th", "45"),
        cwd=ws.base_path, log=log,
    )
    if code != 0 or not ws.streams_path.is_dir():
        raise ToolError(f"Falha ao extrair STREAMS.DAT.{_tool_detail(out)}")


def _prepare_strings_workspace(ws: Workspace, log: LogFn = None) -> None:
    """Copy ASSETS/fonts/mcstrings02.strtbl to the root and decode it to JSON."""
    if not ws.strtbl_path.is_file():
        raise ToolError("mcstrings02.strtbl nao foi encontrado dentro de ASSETS/fonts.")
    _make_writable(ws.root_strtbl_path)
    shutil.copy2(ws.strtbl_path, ws.root_strtbl_path)
    strtbl = find_strtbl()
    if strtbl is None:
        raise ToolError("strtbl nao encontrado em tools/.")
    with tempfile.TemporaryDirectory(dir=str(ws.base_path), ignore_cleanup_errors=True) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        temp_strtbl = temp_dir / "mcstrings02.strtbl"
        shutil.copy2(ws.root_strtbl_path, temp_strtbl)
        code, out = _run(_tool_command(strtbl, "dec", temp_strtbl), cwd=ws.base_path, log=log, input_text="y\n")
        if code != 0:
            raise ToolError(
                "Falha ao decodificar mcstrings02.strtbl (a tabela de textos do jogo). "
                f"O arquivo extraido tem {ws.root_strtbl_path.stat().st_size if ws.root_strtbl_path.is_file() else 0} bytes. "
                "Isso costuma significar que a ISO nao e a versao suportada "
                f"({SUPPORTED_GAME_NAME}) ou que a extracao saiu incompleta (disco cheio "
                "ou antivirus). Use o botao de relatorio de erro para enviar o diagnostico."
                + _tool_detail(out))
        json_text = (temp_dir / "mcstrings02.json").read_text(encoding="utf-8")
    _make_writable(ws.strings_json_path)
    ws.strings_json_path.write_text(json_text, encoding="utf-8")
    if log:
        log(f"mcstrings02.json gerado: {ws.strings_json_path}")


def decompile_workspace(ws: Workspace, *, force_refresh: bool = False,
                        log: LogFn = None, progress: ProgressFn = None) -> dict:
    """Step 4: from ASSETS.DAT/STREAMS.DAT in 'Arquivos da ISO', build the editable
    ASSETS/, STREAMS/ and mcstrings02.json. Ports _decompile_workspace_from_game_files_worker."""
    if not ws.game_files_path.is_dir():
        raise ToolError(f"Arquivos da ISO nao encontrados: {ws.game_files_path}")
    assets_source = ws.game_files_path / "ASSETS.DAT"
    streams_source = ws.game_files_path / "STREAMS.DAT"
    if not assets_source.is_file():
        raise ToolError(f"ASSETS.DAT nao encontrado em Arquivos da ISO: {assets_source}")
    if not streams_source.is_file():
        raise ToolError(f"STREAMS.DAT nao encontrado em Arquivos da ISO: {streams_source}")

    assets_root = ws.base_path / "ASSETS.DAT"
    streams_root = ws.base_path / "STREAMS.DAT"
    copied_root = []
    if force_refresh or not assets_root.is_file():
        if progress:
            progress(8, "Copiando ASSETS.DAT para a raiz...")
        _make_writable(assets_root)
        shutil.copy2(assets_source, assets_root)
        copied_root.append("ASSETS.DAT")
    if force_refresh or not streams_root.is_file():
        if progress:
            progress(16, "Copiando STREAMS.DAT para a raiz...")
        _make_writable(streams_root)
        shutil.copy2(streams_source, streams_root)
        copied_root.append("STREAMS.DAT")

    extracted_assets = False
    if force_refresh or not ws.assets_path.is_dir() or not ws.strtbl_path.is_file():
        if ws.assets_path.is_dir():
            _rmtree(ws.assets_path)
        if progress:
            progress(32, "Descompilando ASSETS.DAT...")
        _extract_assets_dat(ws, log=log)
        extracted_assets = True

    extracted_streams = False
    if force_refresh or not ws.streams_path.is_dir():
        if ws.streams_path.is_dir():
            _rmtree(ws.streams_path)
        if progress:
            progress(56, "Descompilando STREAMS.DAT...")
        _extract_streams_dat(ws, log=log)
        extracted_streams = True

    prepared_strings = False
    if force_refresh or not ws.root_strtbl_path.is_file() or not ws.strings_json_path.is_file() or extracted_assets:
        if progress:
            progress(82, "Descompilando mcstrings02.strtbl para JSON...")
        _prepare_strings_workspace(ws, log=log)
        prepared_strings = True

    if progress:
        progress(100, "Workspace pronto para editar.")
    return {
        "copied_root_files": copied_root,
        "extracted_assets": extracted_assets,
        "extracted_streams": extracted_streams,
        "prepared_strings": prepared_strings,
    }


def prepare_project_from_iso(ws: Workspace, iso_source, log: LogFn = None, progress: ProgressFn = None) -> dict:
    """Steps 3+4 in one ("Preparar tudo automaticamente"): copy the ISO into
    'Arquivos da ISO' then decompile to a ready-to-edit workspace.
    Ports _prepare_project_from_iso_worker. Clears prior game_files first."""
    iso_source = Path(iso_source)
    if not iso_source.is_file():
        raise ToolError(f"Arquivo ISO nao encontrado: {iso_source}")

    def _sub(low: float, high: float) -> ProgressFn:
        if not progress:
            return None
        return lambda pct, text: progress(low + (high - low) * pct / 100.0, text)

    # Confere ANTES de apagar o workspace atual: recusar uma ISO errada nao pode
    # custar a extracao que ja estava la.
    if progress:
        progress(1, "Conferindo a ISO...")
    assert_supported_iso(iso_source, log=log)

    if ws.game_files_path.exists():
        _rmtree(ws.game_files_path)  # fresh import
    count = copy_iso_to_game_files(ws, iso_source, log=log, progress=_sub(2, 48), verify=False)
    if log:
        log(f"ISO copiada ({count} arquivo(s)). Descompilando o workspace...")
    result = decompile_workspace(ws, force_refresh=True, log=log, progress=_sub(48, 100))
    result["copied_count"] = count
    return result


def workspace_status(ws: Workspace) -> dict:
    """Snapshot of how prepared the workspace is (for the Início/Preparar screen)."""
    return {
        "base_path": str(ws.base_path),
        "has_game_files": ws.game_files_path.is_dir(),
        "has_assets_dat": (ws.game_files_path / "ASSETS.DAT").is_file(),
        "has_streams_dat": (ws.game_files_path / "STREAMS.DAT").is_file(),
        "has_assets": ws.assets_path.is_dir(),
        "has_streams": ws.streams_path.is_dir(),
        "has_strings_json": ws.strings_json_path.is_file(),
        "prepared": ws.assets_path.is_dir() and ws.streams_path.is_dir() and ws.strings_json_path.is_file(),
        "tools_ready": all([find_dave(), find_hash_build(), find_strtbl(), find_streams_list()]),
    }


def reset_workspace(ws: Workspace, *, log: LogFn = None, progress: ProgressFn = None) -> dict:
    """Wipe the extracted/decompiled workspace so the user can start over from a
    different ISO — the inverse of prepare_project_from_iso.

    Removes 'Arquivos da ISO', ASSETS/, STREAMS/, the root ASSETS.DAT/STREAMS.DAT
    working copies and the strings artifacts. KEEPS backups/ (the safety net) and
    any generated ISO output. Read-only bits are cleared via _rmtree/_make_writable
    (PS2 ISO extracts are read-only), so it works on Windows too.
    """
    dir_targets = [ws.game_files_path, ws.assets_path, ws.streams_path]
    file_targets = [
        ws.base_path / "ASSETS.DAT",
        ws.base_path / "STREAMS.DAT",
        ws.root_strtbl_path,
        ws.strings_json_path,
    ]
    targets = dir_targets + file_targets
    total = len(targets) or 1
    removed = []
    for index, target in enumerate(targets):
        if progress:
            progress((index / total) * 100.0, f"Removendo {target.name}...")
        if target.is_dir():
            _rmtree(target)
            removed.append(target.name)
            if log:
                log(f"Removido: {target}")
        elif target.exists():
            _make_writable(target)
            try:
                target.unlink()
                removed.append(target.name)
                if log:
                    log(f"Removido: {target}")
            except OSError as exc:  # noqa: PERF203 - report and keep going
                if log:
                    log(f"Aviso: nao foi possivel remover {target}: {exc}")
    if progress:
        progress(100.0, "Projeto resetado. Escolha uma nova ISO para comecar.")
    if log:
        log(f"Reset concluido: {len(removed)} item(ns) removido(s). Backups preservados.")
    return {"removed": removed, "count": len(removed)}


# ---- Remove music ----------------------------------------------------------
# The inverse of add: enumerate songs from STREAMS/Music, then optionally remove
# their .rsm, their playlist entries, and their string entries. Ports remove_music.


def _music_playlists(ws: Workspace) -> list:
    """The .play files under city/<city>/music/ (the ones that hold song entries)."""
    root = ws.playlists_root
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.play") if p.parent.name.lower() == "music")


def list_target_playlists(ws: Workspace) -> list:
    """Playlists a song can be added to, as {rel, name, city} dicts. Same filter
    as _music_playlists (only city/<city>/music/*.play) — mirrors the original's
    self.music_playlists, so non-music .play files are NOT offered as targets."""
    items = []
    for path in _music_playlists(ws):
        try:
            rel = path.relative_to(ws.assets_path).as_posix()
        except ValueError:
            rel = path.name
        items.append({"rel": rel, "name": path.name, "city": path.parent.parent.name})
    return items


def _playlist_usage_counts(ws: Workspace) -> dict:
    counts = {}
    for playlist in _music_playlists(ws):
        try:
            content, _ = _read_text(playlist)
        except Exception:  # noqa: BLE001
            continue
        for line in content.splitlines():
            entry = line.strip()
            if entry.lower().startswith("music\\"):
                counts[entry.lower()] = counts.get(entry.lower(), 0) + 1
    return counts


def list_songs(ws: Workspace) -> list:
    """Enumerate installed songs (STREAMS/Music/<genre>/*.rsm), each with how many
    playlists reference it and whether its string entry exists."""
    music_root = ws.streams_path / "Music"
    if not music_root.is_dir():
        return []
    counts = _playlist_usage_counts(ws)
    string_keys = set()
    if ws.strings_json_path.is_file():
        try:
            content, _ = _read_text(ws.strings_json_path)
            string_keys = set(json.loads(content).get("data", {}).keys())
        except Exception:  # noqa: BLE001
            pass
    songs = []
    for genre_dir in sorted(p for p in music_root.iterdir() if p.is_dir()):
        for rsm in sorted(genre_dir.glob("*.rsm")):
            genre, asset = genre_dir.name, rsm.stem
            songs.append({
                "genre": genre,
                "asset_name": asset,
                "playlist_count": counts.get(playlist_entry(genre, asset).lower(), 0),
                "has_strings": string_key(genre, asset) in string_keys,
            })
    return songs


# -- validacao do que vem da UI ----------------------------------------------
# A tela manda de volta genero/nome interno/playlist que ELA recebeu do backend,
# entao na pratica sao sempre validos. Mas esses valores viram CAMINHO e terminam
# num unlink(): o custo de conferir e proximo de zero e o custo de errar e apagar
# um arquivo fora do workspace. (O lado do "adicionar" ja era seguro por
# construcao: coerce_asset_name reduz tudo a [A-Za-z0-9_].)


def validate_genre(genre) -> str:
    """Aceita apenas um dos generos conhecidos do jogo."""
    cleaned = str(genre or "").strip()
    if cleaned not in GENRES:
        raise ToolError(f"Genero invalido: {genre!r}")
    return cleaned


def validate_asset_name(asset_name) -> str:
    """Aceita apenas o alfabeto que coerce_asset_name produz: [A-Za-z0-9_].

    Isso descarta separadores de caminho, '..' e nomes vazios de uma vez so."""
    cleaned = str(asset_name or "").strip()
    if not cleaned or not re.fullmatch(r"[A-Za-z0-9_]+", cleaned):
        raise ToolError(f"Nome interno invalido: {asset_name!r}")
    return cleaned


def resolve_playlist(ws: Workspace, rel) -> Path:
    """Resolve uma playlist relativa a ASSETS/, garantindo que ela fique DENTRO
    do workspace e seja mesmo um .play (a UI manda o 'rel' que list_target_playlists
    devolveu)."""
    candidate = (ws.assets_path / str(rel)).resolve()
    root = ws.assets_path.resolve()
    if candidate == root or root not in candidate.parents:
        raise ToolError(f"Playlist fora do workspace: {rel!r}")
    if candidate.suffix.lower() != ".play":
        raise ToolError(f"Playlist invalida: {rel!r}")
    return candidate


def _find_song_playlists(ws: Workspace, genre: str, asset_name: str) -> list:
    entry = playlist_entry(genre, asset_name)
    found = []
    for playlist in _music_playlists(ws):
        try:
            content, _ = _read_text(playlist)
        except Exception:  # noqa: BLE001
            continue
        if entry in {line.strip() for line in content.splitlines()}:
            found.append(playlist)
    return found


def remove_songs(ws: Workspace, selection: list, *, remove_audio: bool = True,
                 remove_playlists: bool = True, remove_strings: bool = True,
                 backup_label: str = "remove_music", log: LogFn = None,
                 progress: ProgressFn = None, timestamp: Optional[str] = None) -> dict:
    """Remove songs (destructive). selection = [{genre, asset_name}, ...].
    Mirrors remove_music: backup -> remove from playlists -> remove string entries
    (recompile STRTBL) -> delete the .rsm files. Each action is optional."""
    if not selection:
        raise ToolError("Nenhuma música selecionada para remover.")
    if not (remove_audio or remove_playlists or remove_strings):
        raise ToolError("Marque ao menos uma ação (áudio, playlists ou strings).")

    records = []
    for item in selection:
        genre = validate_genre(item.get("genre"))
        asset = validate_asset_name(item.get("asset_name"))
        records.append({
            "genre": genre,
            "asset_name": asset,
            "path": ws.streams_path / "Music" / genre / f"{asset}.rsm",
        })

    backup_dir = create_backup_session(ws, backup_label, timestamp=timestamp)
    if log:
        log(f"Backup preparado em {backup_dir}")

    try:
        return _apply_remove(ws, records, backup_dir, remove_audio=remove_audio,
                             remove_playlists=remove_playlists, remove_strings=remove_strings,
                             log=log, progress=progress)
    except BaseException as exc:
        setattr(exc, BACKUP_ATTR, str(backup_dir))
        raise


def _apply_remove(ws: Workspace, records: list, backup_dir: Path, *, remove_audio: bool,
                  remove_playlists: bool, remove_strings: bool,
                  log: LogFn = None, progress: ProgressFn = None) -> dict:
    affected = {(r["genre"], r["asset_name"]): _find_song_playlists(ws, r["genre"], r["asset_name"]) for r in records}
    unique_playlists = []
    seen = set()
    for playlists in affected.values():
        for playlist in playlists:
            if playlist not in seen:
                seen.add(playlist)
                unique_playlists.append(playlist)

    if remove_audio:
        backup_files(ws, [record["path"] for record in records], backup_dir)

    playlist_changes = 0
    if remove_playlists:
        if progress:
            progress(30, "Removendo das playlists...")
        backup_files(ws, unique_playlists, backup_dir)
        for record in records:
            entry = playlist_entry(record["genre"], record["asset_name"])
            for playlist in affected[(record["genre"], record["asset_name"])]:
                if update_playlist(playlist, entry, "remove"):
                    playlist_changes += 1
                    if log:
                        log(f"Removido da playlist {Path(playlist).name}: {record['asset_name']}")

    removed_strings = 0
    if remove_strings:
        if progress:
            progress(58, "Atualizando mcstrings02...")
        backup_files(ws, (ws.root_strtbl_path, ws.strtbl_path, ws.strings_json_path), backup_dir)
        strings_data = decode_strings(ws, log=log)
        entries = strings_data.setdefault("data", {})
        json_data = _load_strings_json_data(ws, strings_data)
        json_entries = json_data.setdefault("data", {})
        removed_any = False
        for record in records:
            key = string_key(record["genre"], record["asset_name"])
            hit = False
            if key in entries:
                del entries[key]
                hit = True
            if key in json_entries:
                del json_entries[key]
                hit = True
            if hit:
                removed_any = True
                removed_strings += 1
                if log:
                    log(f"Entrada removida do mcstrings02: {key}")
        if removed_any:
            sync_strings_json(ws, json_data)
            compile_strings_json_to_strtbl(ws, log=log)

    removed_audio = 0
    if remove_audio:
        if progress:
            progress(78, "Apagando arquivos de áudio...")
        for record in records:
            target = record["path"]
            if target.exists():
                _make_writable(target)  # extracted .rsm is read-only on Windows
                target.unlink()
                removed_audio += 1
                if log:
                    log(f"Arquivo apagado: {target.name}")

    if progress:
        progress(100, "Concluido.")
    return {
        "removed": len(records),
        "playlist_changes": playlist_changes,
        "removed_strings": removed_strings,
        "removed_audio": removed_audio,
        "backup": str(backup_dir),
    }


# ---- Generate final ISO (ImgBurn) ------------------------------------------
# Rebuilds all DATs (published into 'Arquivos da ISO') then builds the final
# bootable .iso with ImgBurn. Ports _generate_final_iso_impl + _build_imgburn_command.


def _sanitize_volume_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", (value or "").strip()).strip("_")
    return (cleaned or "MClub")[:32]


def find_imgburn() -> Optional[Path]:
    """Locate ImgBurn.exe: PATH first, then the standard install folders."""
    found = shutil.which("ImgBurn")
    if found:
        return Path(found)
    for env_name in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        base = os.environ.get(env_name, "").strip()
        if base:
            candidate = Path(base) / "ImgBurn" / "ImgBurn.exe"
            if candidate.is_file():
                return candidate
    return None


def _build_imgburn_command(imgburn: Path, source_dir: Path, output_iso: Path, volume_label: str) -> list:
    label = _sanitize_volume_label(volume_label)
    return [
        str(imgburn),
        "/MODE", "BUILD",
        "/BUILDINPUTMODE", "STANDARD",
        "/BUILDOUTPUTMODE", "IMAGEFILE",
        "/SRC", str(source_dir) + "\\",
        "/DEST", str(output_iso),
        "/FILESYSTEM", "ISO9660 + UDF",
        "/UDFREVISION", "1.02",
        "/VOLUMELABEL", label,
        "/VOLUMELABEL_ISO9660", label,
        "/ROOTFOLDER", "YES",
        "/OVERWRITE", "YES",
        "/START", "/CLOSE", "/NOIMAGEDETAILS",
    ]


def iso_output_status(ws: Workspace) -> dict:
    imgburn = find_imgburn()
    has_cnf = (ws.game_files_path / "SYSTEM.CNF").is_file()
    # generate_final_iso rebuilds the DATs BEFORE calling ImgBurn, so dave/hash_build
    # are required too — without this the screen offered a button that died halfway.
    has_rebuild_tools = bool(find_dave() and find_hash_build())
    return {
        "imgburn": str(imgburn) if imgburn else None,
        "has_game_files": ws.game_files_path.is_dir(),
        "has_system_cnf": has_cnf,
        "has_rebuild_tools": has_rebuild_tools,
        "ready": bool(imgburn) and has_cnf and has_rebuild_tools,
        "default_output": str(ws.base_path / "ISO" / "MC3_mod.iso"),
    }


def find_foobar() -> Optional[Path]:
    found = shutil.which("foobar2000")
    if found:
        return Path(found)
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
        base = os.environ.get(env_name, "").strip()
        if base:
            candidate = Path(base) / "foobar2000" / "foobar2000.exe"
            if candidate.is_file():
                return candidate
    return None


def find_winget() -> Optional[Path]:
    found = shutil.which("winget")
    return Path(found) if found else None


# winget package IDs + official download pages (ported from the original app).
_TOOL_SPECS = {
    "ffmpeg": {"winget_id": "Gyan.FFmpeg", "label": "FFmpeg",
               "url": "https://www.gyan.dev/ffmpeg/builds/", "find": find_ffmpeg},
    "imgburn": {"winget_id": "LIGHTNINGUK.ImgBurn", "label": "ImgBurn",
                "url": "https://www.imgburn.com/index.php?act=download", "find": find_imgburn},
    "foobar": {"winget_id": "PeterPawlowski.foobar2000", "label": "foobar2000",
               "url": "https://www.foobar2000.org/download", "find": find_foobar},
}


def tool_download_url(kind: str) -> str:
    spec = _TOOL_SPECS.get(kind)
    return spec["url"] if spec else ""


def install_tool(kind: str, log: LogFn = None, progress: ProgressFn = None) -> dict:
    """Install a PC tool (ffmpeg/imgburn/foobar) via winget. Ports the original's
    _install_with_winget. If winget is missing this does NOT raise — it returns
    reason='no-winget' + the official URL so the UI can offer a manual download.
    The winget subprocess is cancelable (registered in _ACTIVE_PROCS)."""
    spec = _TOOL_SPECS.get(kind)
    if spec is None:
        raise ToolError(f"Ferramenta desconhecida: {kind}")
    label = spec["label"]
    winget = find_winget()
    if winget is None:
        if log:
            log("winget nao foi encontrado neste Windows.")
        return {"ok": False, "reason": "no-winget", "label": label, "download_url": spec["url"]}

    if progress:
        progress(5, f"Instalando {label} via winget...")
    code, out = _run(
        [winget, "install", "--id", spec["winget_id"], "--exact",
         "--accept-package-agreements", "--accept-source-agreements", "--silent"],
        log=log,
    )
    # winget returns non-zero for "already installed / no upgrade available" too,
    # so the real success test is whether the tool is now detectable.
    found = spec["find"]() is not None
    if progress:
        progress(100, "Concluido." if found else "Terminado.")
    if not found and code != 0:
        raise ToolError(f"Nao foi possivel instalar {label} via winget (codigo {code}).{_tool_detail(out)}")
    return {"ok": True, "label": label, "installed": found, "code": code}


def overview(ws: Workspace) -> dict:
    """Aggregate status for the Início dashboard + step-by-step guide."""
    ffmpeg = find_ffmpeg()
    imgburn = find_imgburn()
    foobar = find_foobar()
    ps2_tools_ok = all([find_dave(), find_hash_build(), find_strtbl(), find_streams_list()])
    prepared = ws.assets_path.is_dir() and ws.streams_path.is_dir() and ws.strings_json_path.is_file()
    has_game_files = (ws.game_files_path / "SYSTEM.CNF").is_file()
    song_count = len(list_songs(ws)) if ws.streams_path.is_dir() else 0
    playlist_count = len(_music_playlists(ws))
    latest = latest_backup_dir(ws)

    winget_ok = bool(find_winget())
    programs = [
        {"name": "FFmpeg", "role": "converte áudio para RSM", "found": bool(ffmpeg), "essential": True,
         "kind": "ffmpeg", "installable": winget_ok, "download_url": _TOOL_SPECS["ffmpeg"]["url"]},
        {"name": "ImgBurn", "role": "gera a ISO final", "found": bool(imgburn), "essential": True,
         "kind": "imgburn", "installable": winget_ok, "download_url": _TOOL_SPECS["imgburn"]["url"]},
        {"name": "Ferramentas PS2", "role": "dave / hash_build / strtbl + lista", "found": ps2_tools_ok, "essential": True},
        {"name": "foobar2000", "role": "preview de áudio (opcional)", "found": bool(foobar), "essential": False,
         "kind": "foobar", "installable": winget_ok, "download_url": _TOOL_SPECS["foobar"]["url"]},
    ]

    tools_ready = bool(ffmpeg and imgburn and ps2_tools_ok)
    steps = [
        {"n": 1, "title": "Ferramentas do PC", "state": "ok" if tools_ready else "pending", "target": "card-inicio"},
        {"n": 2, "title": "Preparar o projeto (extrair a ISO)", "state": "ok" if prepared else "pending", "target": "card-prepare"},
        {"n": 3, "title": "Adicionar / Remover músicas", "state": "available" if prepared else "blocked", "target": "card-add"},
        {"n": 4, "title": "Recompilar os DATs", "state": "available" if prepared else "blocked", "target": "card-rebuild"},
        {"n": 5, "title": "Gerar a ISO final", "state": "available" if (prepared and imgburn) else "blocked", "target": "card-iso-out"},
    ]

    return {
        "prepared": prepared,
        "has_game_files": has_game_files,
        "song_count": song_count,
        "playlist_count": playlist_count,
        "last_backup": latest.name if latest else None,
        "programs": programs,
        "steps": steps,
    }


def generate_final_iso(ws: Workspace, output_iso, volume_label: str = "MClub",
                       log: LogFn = None, progress: ProgressFn = None) -> Path:
    """Recompile all DATs, then build the final .iso with ImgBurn. Mirrors
    _generate_final_iso_impl (the integrity report step is not ported)."""
    imgburn = find_imgburn()
    if imgburn is None:
        raise ToolError("ImgBurn nao foi encontrado. Instale o ImgBurn para gerar a ISO final.")
    if not ws.game_files_path.is_dir():
        raise ToolError(f"Arquivos da ISO nao encontrados: {ws.game_files_path}")
    if not (ws.game_files_path / "SYSTEM.CNF").is_file():
        raise ToolError("SYSTEM.CNF nao foi encontrado em Arquivos da ISO.")

    output_iso = Path(output_iso)

    def _sub(low: float, high: float) -> ProgressFn:
        if not progress:
            return None
        return lambda pct, text: progress(low + (high - low) * pct / 100.0, text)

    if progress:
        progress(4, "Recompilando DATs antes de gerar a ISO...")
    rebuild_all(ws, log=log, progress=_sub(4, 68))

    output_iso.parent.mkdir(parents=True, exist_ok=True)
    _make_writable(output_iso)
    if progress:
        progress(72, "Gerando ISO final com ImgBurn...")
    code, out = _run(_build_imgburn_command(imgburn, ws.game_files_path, output_iso, volume_label), log=log)
    if code != 0 or not output_iso.is_file():
        raise ToolError(f"O ImgBurn nao conseguiu gerar a ISO final.{_tool_detail(out)}")
    if progress:
        progress(100, "Concluido.")
    return output_iso
