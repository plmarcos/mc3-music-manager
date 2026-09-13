"""The js_api bridge object — the pull side of Python <-> JS.

Every public method here is callable from the web UI as
`pywebview.api.method_name(args)` and returns a Promise. This is where the web
frontend reaches into the reused Python backend (backend/core.py).

Design notes that address the migration risks the analysis flagged:
- NATIVE pickers via window.create_file_dialog return REAL absolute paths (HTML
  drag-drop/`<input type=file>` do not), which the CLI tools require.
- Audio preview uses os.startfile (the OS default player, plays anything) rather
  than an HTML <audio> element (web codecs only).
- A single _busy flag serialises the long task so double-activation can't
  interleave destructive/expensive work (the re-entrancy hazard of non-blocking
  HTML modals).
"""

from __future__ import annotations

import os
import platform
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import webview

from . import core
from .bridge import Bridge
from .tasks import run_in_background

APP_NAME = "MC3 Music Manager"
# Where the "Enviar relatório de erro" button sends reports (owner's inbox).
REPORT_EMAIL = "207797225+plmarcos@users.noreply.github.com"
APP_VERSION = "0.3.0 — web-view (fluxo guiado)"


def _python_version() -> str:
    return "{0}.{1}.{2}".format(*sys.version_info[:3])


def _webview_version() -> str:
    """pywebview nao expoe __version__; a versao so existe nos metadados do pacote
    (e some num build congelado, dai o fallback)."""
    try:
        from importlib.metadata import version

        return version("pywebview")
    except Exception:  # noqa: BLE001 - rodape cosmetico, nunca pode derrubar o boot
        return "6.x"


class Api:
    def __init__(self, bridge: Bridge) -> None:
        self._bridge = bridge
        self._busy_lock = threading.Lock()
        self._busy: bool = False
        self._cancel_requested: bool = False
        self._maximized: bool = False  # frameless title bar tracks this itself
        self._selected_iso: str | None = None
        self._add_source: str | None = None
        self._batch_sources: list[str] = []
        # Contador de geracao das deteccoes de tag. Duas escolhas rapidas disparam
        # duas threads de ffprobe; sem isto, a mais LENTA podia chegar por ultimo e
        # preencher os campos com os dados do arquivo anterior.
        self._guess_seq: int = 0
        self._iso_output: str | None = None
        # Workspace = the app's own folder, same model as the original app.
        self._workspace = core.default_workspace()
        # Persisted preferences (private — never a public non-method attr, or the
        # js_api introspection would recurse; see CLAUDE.md). Seed the last ISO /
        # output path only if they still exist on disk (mirrors the original).
        self._options = core.load_options(self._workspace)
        _last_iso = self._options.get("last_iso") or ""
        if _last_iso and Path(_last_iso).is_file():
            self._selected_iso = _last_iso
        if self._options.get("iso_output"):
            self._iso_output = self._options["iso_output"]

    # Only one background task at a time. Acquire/release are atomic under a
    # lock, so a double-click (or another screen's button) can't slip past the
    # check-then-set — this is the generalized busy-lock the analysis flagged as
    # risk #1 now that HTML confirmations are non-blocking (unlike Tk modals).
    def _acquire_busy(self) -> bool:
        with self._busy_lock:
            if self._busy:
                return False
            self._busy = True
            self._cancel_requested = False  # fresh slate per task
            return True

    def _release_busy(self) -> None:
        with self._busy_lock:
            self._busy = False

    def cancel_current(self) -> dict:
        """User pressed Cancelar: kill the running subprocess. The resulting tool
        error flows through the task's on_error, which releases the busy-lock —
        we never touch _busy here (avoids fighting the lock)."""
        killed = core.cancel_active()
        self._cancel_requested = True
        return {"ok": True, "killed": killed}

    def _error_payload(self, exc: BaseException) -> dict:
        """Build a *_done payload for a failed task, flagging a user-cancel so the
        UI labels it 'cancelado' instead of 'falhou'.

        Carrega tambem o backup da operacao, quando ela tinha um: add/remove fazem
        o trabalho por etapas, entao falhar no meio deixa estado parcial e o
        usuario precisa saber que da para desfazer (Recompilar & Backup ->
        Restaurar). Vale igual para o cancelamento: cancelar tambem para no meio."""
        payload = ({"ok": False, "cancelled": True, "error": "Operacao cancelada pelo usuario."}
                   if self._cancel_requested else {"ok": False, "error": str(exc)})
        backup = getattr(exc, core.BACKUP_ATTR, None)
        if backup:
            payload["backup"] = backup
        return payload

    # ---- window controls (frameless title bar) -----------------------------
    # The window has no native frame, so the HTML title bar drives it. Each call
    # is best-effort: the window may be gone mid-click.

    def window_minimize(self) -> dict:
        win = self._bridge.window
        if win is not None:
            try:
                win.minimize()
            except Exception:  # noqa: BLE001
                return {"ok": False}
        return {"ok": True}

    def window_toggle_maximize(self) -> dict:
        """Toggle maximize/restore. pywebview has no reliable is-maximized probe,
        so track it ourselves (private attr — never a public one, js_api gotcha)."""
        win = self._bridge.window
        if win is None:
            return {"ok": False}
        try:
            if self._maximized:
                win.restore()
            else:
                win.maximize()
            self._maximized = not self._maximized
        except Exception:  # noqa: BLE001
            return {"ok": False}
        return {"ok": True, "maximized": self._maximized}

    def window_close(self) -> dict:
        win = self._bridge.window
        if win is not None:
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                return {"ok": False}
        return {"ok": True}

    # ---- info / diagnostics ------------------------------------------------

    def get_app_info(self) -> dict:
        status = core.tools_status()
        return {
            "name": APP_NAME,
            "version": APP_VERSION,
            # Lido em tempo de execucao: estava fixo em "Python 3.14 / pywebview 6.2.1"
            # e passava a mentir assim que o app rodava noutro interpretador.
            "backend": f"Python {_python_version()} · pywebview {_webview_version()} "
                       f"· WebView2 (Edge Chromium)",
            "tools_ready": status["ready"],
        }

    def get_tools_status(self) -> dict:
        return core.tools_status()

    def get_overview(self) -> dict:
        """Dashboard + step-by-step status for the Início screen."""
        return core.overview(self._workspace)

    # ---- persisted options (Configurações) ---------------------------------

    def get_options(self) -> dict:
        return dict(self._options)

    def set_option(self, key: str, value) -> dict:
        """Update one persisted preference and write options.json."""
        if key in core.DEFAULT_OPTIONS:
            self._options[key] = value
            core.save_options(self._workspace, self._options)
            return {"ok": True}
        return {"ok": False, "reason": "unknown-key"}

    # ---- i18n (interface language) -----------------------------------------

    def get_i18n(self) -> dict:
        """Current language + all translation tables (the frontend can't fetch()
        the JSON directly under file://, so Python serves it)."""
        return {"language": self._options.get("language", "pt-BR"),
                "translations": core.load_translations()}

    def set_language(self, lang: str) -> dict:
        if lang in core.LANGUAGES:
            self._options["language"] = lang
            core.save_options(self._workspace, self._options)
            return {"ok": True, "language": lang}
        return {"ok": False, "reason": "unknown-language"}

    # ---- audio preview (OS default player, plays any format) ----------------

    def preview_add_audio(self) -> dict:
        """Pre-listen to the selected add-music file with the OS default player
        (not an HTML <audio>, which only supports web codecs)."""
        target = self._add_source
        if not target or not Path(target).is_file():
            return {"ok": False, "reason": "no-file"}
        try:
            os.startfile(target)  # noqa: S606 - intentional native handoff
            return {"ok": True}
        except OSError as exc:
            self._bridge.emit("add_log", {"line": f"Nao foi possivel abrir o player: {exc}"})
            return {"ok": False, "reason": str(exc)}

    # ---- validate ISO (part of Preparar Projeto) ---------------------------

    def validate_project_iso(self) -> dict:
        """Read-only validation of the selected ISO (mount, read SYSTEM.CNF/BOOT2,
        check DATs). Reports through the Preparar Projeto channel (pp_*)."""
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        source = self._selected_iso
        if not source or not Path(source).is_file():
            self._release_busy()
            self._bridge.emit("pp_log", {"line": "Escolha uma ISO válida primeiro."})
            return {"ok": False, "reason": "no-iso"}
        self._bridge.emit("pp_busy", {"busy": True})
        self._bridge.emit("pp_status", {"text": "Validando a ISO...", "progress": 0})
        run_in_background(
            "validate_iso",
            lambda: core.inspect_iso(
                source,
                log=lambda line: self._bridge.emit("pp_log", {"line": line}),
                progress=lambda pct, text: self._bridge.emit("pp_status", {"text": text, "progress": pct}),
            ),
            on_done=self._validate_done,
            on_error=self._validate_error,
        )
        return {"ok": True}

    def _validate_done(self, result: object) -> None:
        self._release_busy()
        self._bridge.emit("pp_busy", {"busy": False})
        self._bridge.emit("pp_validation", result if isinstance(result, dict) else {})

    def _validate_error(self, exc: BaseException) -> None:
        self._release_busy()
        self._bridge.emit("pp_log", {"line": f"ERRO: {exc}"})
        self._bridge.emit("pp_busy", {"busy": False})
        self._bridge.emit("pp_validation", {"error": str(exc)})

    # ---- recompile DATs + backup -------------------------------------------

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dict):
            return {k: Api._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [Api._json_safe(v) for v in value]
        if isinstance(value, Path):
            return str(value)
        return value

    def get_rebuild_status(self) -> dict:
        ws = self._workspace
        tools = core.rebuild_tools_status()
        latest = core.latest_backup_dir(ws)
        return {
            "base_path": str(ws.base_path),
            "has_streams": ws.streams_path.is_dir(),
            "has_assets": ws.assets_path.is_dir(),
            "tools": tools,
            "rebuild_ready": bool(tools["ready"] and ws.streams_path.is_dir() and ws.assets_path.is_dir()),
            "latest_backup": latest.name if latest else None,
        }

    # -- rebuild --

    def rebuild_streams(self) -> dict:
        return self._start_rebuild("STREAMS.DAT", core.rebuild_streams_dat)

    def rebuild_assets(self) -> dict:
        return self._start_rebuild("ASSETS.DAT", core.rebuild_assets_dat)

    def rebuild_all_dats(self) -> dict:
        return self._start_rebuild("todos os DATs", core.rebuild_all)

    def _start_rebuild(self, kind: str, fn) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        self._bridge.emit("rb_busy", {"busy": True})
        self._bridge.emit("rb_status", {"text": f"Iniciando: {kind}...", "progress": 0})
        run_in_background(
            f"rebuild_{kind}",
            lambda: fn(
                self._workspace,
                log=lambda line: self._bridge.emit("rb_log", {"line": line}),
                progress=lambda pct, text: self._bridge.emit("rb_status", {"text": text, "progress": pct}),
            ),
            on_done=lambda result: self._rebuild_done(kind, result),
            on_error=self._rebuild_error,
        )
        return {"ok": True}

    def _rebuild_done(self, kind: str, result: object) -> None:
        self._release_busy()
        self._bridge.emit("rb_busy", {"busy": False})
        self._bridge.emit("rb_done", {"ok": True, "kind": kind, "result": self._json_safe(result)})

    def _rebuild_error(self, exc: BaseException) -> None:
        self._release_busy()
        self._bridge.emit("rb_log", {"line": f"ERRO: {exc}"})
        self._bridge.emit("rb_busy", {"busy": False})
        self._bridge.emit("rb_done", self._error_payload(exc))

    # -- backup / restore --

    def create_manual_backup(self) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        self._bridge.emit("bk_busy", {"busy": True})
        run_in_background("manual_backup", self._manual_backup_worker, on_done=self._backup_done, on_error=self._backup_error)
        return {"ok": True}

    def restore_latest_backup(self) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        self._bridge.emit("bk_busy", {"busy": True})
        run_in_background("restore_backup", self._restore_worker, on_done=self._restore_done, on_error=self._backup_error)
        return {"ok": True}

    def _manual_backup_worker(self) -> dict:
        ws = self._workspace
        log = lambda line: self._bridge.emit("bk_log", {"line": line})
        backup_dir = core.create_backup_session(ws, "manual")
        log(f"Backup criado: {backup_dir}")
        targets = [
            ws.root_strtbl_path,
            ws.strtbl_path,
            ws.strings_json_path,
            ws.base_path / "STREAMS.DAT",
            ws.base_path / "ASSETS.DAT",
        ]
        if ws.playlists_root.is_dir():
            targets += sorted(ws.playlists_root.rglob("*.play"))
        saved = core.backup_files(ws, targets, backup_dir)
        log(f"{saved} arquivo(s) copiados para o backup.")
        return {"backup": str(backup_dir), "count": saved}

    def _restore_worker(self) -> dict:
        ws = self._workspace
        latest = core.latest_backup_dir(ws)
        if latest is None:
            raise core.ToolError("Nenhum backup encontrado para restaurar.")
        return core.restore_backup(ws, latest, log=lambda line: self._bridge.emit("bk_log", {"line": line}))

    def _backup_done(self, result: object) -> None:
        self._release_busy()
        self._bridge.emit("bk_busy", {"busy": False})
        self._bridge.emit("bk_done", {"ok": True, "kind": "backup", "result": self._json_safe(result)})

    def _restore_done(self, result: object) -> None:
        self._release_busy()
        self._bridge.emit("bk_busy", {"busy": False})
        self._bridge.emit("bk_done", {"ok": True, "kind": "restore", "result": self._json_safe(result)})

    def _backup_error(self, exc: BaseException) -> None:
        self._release_busy()
        self._bridge.emit("bk_log", {"line": f"ERRO: {exc}"})
        self._bridge.emit("bk_busy", {"busy": False})
        self._bridge.emit("bk_done", {"ok": False, "error": str(exc)})

    # ---- add music ---------------------------------------------------------

    def get_add_status(self) -> dict:
        ws = self._workspace
        # ffmpeg converts mp3/flac/... -> wav -> rsm, so the add screen must require
        # it too; otherwise the batch only blows up halfway through the conversion.
        has_ffmpeg = core.find_ffmpeg() is not None
        has_rstm_build = core.find_rstm_build() is not None
        has_strtbl = core.find_strtbl() is not None
        return {
            "genres": list(core.GENRES),
            "genre_defaults": dict(core.GENRE_DEFAULT_PLAYLISTS),
            "city_playlists": sorted(core.CITY_PLAYLIST_NAMES),
            "has_ffmpeg": has_ffmpeg,
            "has_rstm_build": has_rstm_build,
            "has_strtbl": has_strtbl,
            "tools_ready": has_ffmpeg and has_rstm_build and has_strtbl,
            "has_playlists": ws.playlists_root.is_dir(),
            "playlists": self._list_playlists(),
        }

    def _list_playlists(self) -> list:
        # Only city/<city>/music/*.play — same filter as the original's
        # music_playlists (non-music .play files are not valid song targets).
        return core.list_target_playlists(self._workspace)

    def _is_busy(self) -> bool:
        with self._busy_lock:
            return self._busy

    def pick_add_audio(self) -> dict:
        # A UI ja desabilita o botao durante uma tarefa, mas o estado que o picker
        # troca (_add_source/_batch_sources) e lido pelo worker em andamento —
        # entao o backend recusa por conta propria.
        if self._is_busy():
            return {"path": None, "reason": "busy"}
        window = self._bridge.window
        if window is None:
            return {"path": None}
        file_types = ("Áudio (*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac;*.wma;*.ads;*.ss2;*.rsm)", "Todos os arquivos (*.*)")
        result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types)
        path = result[0] if result else None
        self._add_source = path
        if path:
            self._bridge.emit("add_selected", {"path": path, "name": Path(path).name})
            # Reading tags (ffprobe) can be slow, so guess off the UI thread.
            self._guess_seq += 1
            seq = self._guess_seq
            run_in_background(
                "add_guess",
                lambda: core.source_guess(path, log=lambda line: self._bridge.emit("add_log", {"line": line})),
                on_done=lambda g: self._emit_guess(seq, g),
                on_error=lambda exc: self._bridge.emit("add_log", {"line": f"Aviso na deteccao: {exc}"}),
            )
        return {"path": path}

    def _emit_guess(self, seq: int, guess: object) -> None:
        """So publica a deteccao se ela ainda for a da escolha ATUAL."""
        if seq != self._guess_seq:
            return
        self._bridge.emit("add_guess", guess if isinstance(guess, dict) else {})

    def preview_add(self, title: str, artist: str, genre: str, asset_name: str) -> dict:
        if not self._add_source:
            return {"ok": False, "error": "Escolha um arquivo de áudio primeiro."}
        try:
            spec = core.build_add_spec(self._workspace, self._add_source, title, artist, genre, asset_name)
        except core.ToolError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "asset_name": spec["asset_name"],
            "string_key": spec["string_key"],
            "playlist_entry": spec["playlist_entry"],
            "stream_target": str(spec["stream_target"]),
            "exists": bool(core.existing_spec_targets([spec])),
        }

    def add_selected(self, payload: dict) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        source = self._add_source
        if not source or not Path(source).is_file():
            self._release_busy()
            self._bridge.emit("add_log", {"line": "Nenhum arquivo de áudio selecionado."})
            return {"ok": False, "reason": "no-file"}
        self._bridge.emit("add_busy", {"busy": True})
        self._bridge.emit("add_status", {"text": "Iniciando adição...", "progress": 0})
        run_in_background("add_song", lambda: self._add_worker(source, payload or {}),
                          on_done=self._add_done, on_error=self._add_error)
        return {"ok": True}

    def _add_worker(self, source: str, payload: dict) -> dict:
        ws = self._workspace
        log = lambda line: self._bridge.emit("add_log", {"line": line})
        progress = lambda pct, text: self._bridge.emit("add_status", {"text": text, "progress": pct})
        spec = core.build_add_spec(
            ws, source, payload.get("title", ""), payload.get("artist", ""),
            payload.get("genre", ""), payload.get("asset_name", ""),
        )
        rels = payload.get("playlists") or []
        # resolve_playlist prende o caminho dentro de ASSETS/ e exige .play
        selected = [core.resolve_playlist(ws, rel) for rel in rels]
        if not selected:
            raise core.ToolError("Selecione ao menos uma playlist de destino.")
        # Expand city picks with the genre's race playlist (mirrors the original;
        # without this the song never lands in the race lists = invisible in game).
        spec["playlist_targets"] = core.playlist_targets_for_genre(selected, spec["genre"]) or selected
        return core.add_songs(ws, [spec], allow_overwrite=bool(payload.get("allow_overwrite")),
                              log=log, progress=progress)

    def _add_done(self, result: object) -> None:
        self._release_busy()
        self._bridge.emit("add_busy", {"busy": False})
        self._bridge.emit("add_done", {"ok": True, "result": self._json_safe(result)})

    def _add_error(self, exc: BaseException) -> None:
        self._release_busy()
        self._bridge.emit("add_log", {"line": f"ERRO: {exc}"})
        self._bridge.emit("add_busy", {"busy": False})
        self._bridge.emit("add_done", self._error_payload(exc))

    # ---- add music: batch (multiple files) ---------------------------------

    def pick_batch_audio(self) -> dict:
        if self._is_busy():
            return {"count": 0, "reason": "busy"}
        window = self._bridge.window
        if window is None:
            return {"count": 0}
        file_types = ("Áudio (*.wav;*.mp3;*.flac;*.ogg;*.m4a;*.aac;*.wma;*.ads;*.ss2;*.rsm)", "Todos os arquivos (*.*)")
        result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True, file_types=file_types)
        paths = list(result) if result else []
        self._batch_sources = paths
        if paths:
            self._bridge.emit("add_log", {"line": f"Lendo tags de {len(paths)} arquivo(s)..."})
            run_in_background(
                "batch_guess",
                lambda: self._batch_guess_worker(paths),
                on_done=lambda rows: self._bridge.emit("batch_loaded", {"rows": rows if isinstance(rows, list) else []}),
                on_error=lambda exc: self._bridge.emit("add_log", {"line": f"Aviso na deteccao: {exc}"}),
            )
        return {"count": len(paths)}

    def _batch_guess_worker(self, paths: list) -> list:
        rows = []
        for index, path in enumerate(paths):
            guess = core.source_guess(path)
            rows.append({
                "index": index,
                "name": Path(path).name,
                "title": guess.get("title", ""),
                "artist": guess.get("artist", ""),
                "asset_name": guess.get("asset_name", ""),
                "genre": core.GENRES[0],
            })
        return rows

    def add_batch(self, payload: dict) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        rows = (payload or {}).get("rows") or []
        if not rows:
            self._release_busy()
            self._bridge.emit("add_log", {"line": "Nenhuma faixa no lote."})
            return {"ok": False, "reason": "empty"}
        self._bridge.emit("add_busy", {"busy": True})
        self._bridge.emit("add_status", {"text": "Iniciando lote...", "progress": 0})
        # Congela as origens sob o busy-lock: o picker nao pode trocar o mapeamento
        # indice -> caminho enquanto o worker o percorre.
        sources = list(self._batch_sources)
        run_in_background("add_batch", lambda: self._add_batch_worker(payload or {}, sources),
                          on_done=self._add_done, on_error=self._add_error)
        return {"ok": True}

    def _add_batch_worker(self, payload: dict, sources: list) -> dict:
        ws = self._workspace
        log = lambda line: self._bridge.emit("add_log", {"line": line})
        progress = lambda pct, text: self._bridge.emit("add_status", {"text": text, "progress": pct})
        rels = payload.get("playlists") or []
        selected = [core.resolve_playlist(ws, rel) for rel in rels]
        if not selected:
            raise core.ToolError("Selecione ao menos uma playlist de destino.")

        specs = []
        skipped = []
        for row in payload.get("rows", []):
            index = row.get("index")
            if index is None or index >= len(sources):
                continue
            source = sources[index]
            try:
                spec = core.build_add_spec(
                    ws, source, row.get("title", ""), row.get("artist", ""),
                    row.get("genre", ""), row.get("asset_name", ""),
                )
            except core.ToolError as exc:
                skipped.append(f"{Path(source).name}: {exc}")
                continue
            # Per-track expansion — each row has its own genre (mirrors original;
            # a track with no compatible playlist is skipped, like the original).
            targets = core.playlist_targets_for_genre(selected, spec["genre"])
            if not targets:
                skipped.append(f"{Path(source).name}: (nenhuma playlist compativel com o genero {spec['genre']})")
                continue
            spec["playlist_targets"] = targets
            specs.append(spec)

        if not specs:
            raise core.ToolError("Nenhuma faixa valida no lote. " + ("; ".join(skipped) if skipped else ""))
        result = core.add_songs(ws, specs, allow_overwrite=bool(payload.get("allow_overwrite")),
                                backup_label="add_music_batch", log=log, progress=progress)
        result["skipped"] = skipped
        return result

    # ---- remove music ------------------------------------------------------

    def list_songs(self) -> dict:
        ws = self._workspace
        return {
            "songs": core.list_songs(ws),
            "playlist_count": len(core._music_playlists(ws)),
            "genres": list(core.GENRES),
        }

    def remove_selected(self, payload: dict) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        songs = (payload or {}).get("songs") or []
        if not songs:
            self._release_busy()
            self._bridge.emit("rm_log", {"line": "Nenhuma música selecionada."})
            return {"ok": False, "reason": "empty"}
        self._bridge.emit("rm_busy", {"busy": True})
        self._bridge.emit("rm_status", {"text": "Iniciando remoção...", "progress": 0})
        run_in_background("remove_songs", lambda: self._remove_worker(payload or {}),
                          on_done=self._remove_done, on_error=self._remove_error)
        return {"ok": True}

    def _remove_worker(self, payload: dict) -> dict:
        ws = self._workspace
        log = lambda line: self._bridge.emit("rm_log", {"line": line})
        rebuild_after = bool(payload.get("rebuild_after"))
        # If rebuilding after, leave the last 20% of the bar for the rebuild.
        span = 0.8 if rebuild_after else 1.0
        result = core.remove_songs(
            ws, payload.get("songs", []),
            remove_audio=bool(payload.get("remove_audio", True)),
            remove_playlists=bool(payload.get("remove_playlists", True)),
            remove_strings=bool(payload.get("remove_strings", True)),
            log=log,
            progress=lambda pct, text: self._bridge.emit("rm_status", {"text": text, "progress": pct * span}),
        )
        if rebuild_after:
            core.rebuild_all(
                ws, log=log,
                progress=lambda pct, text: self._bridge.emit("rm_status", {"text": text, "progress": 80 + pct * 0.2}),
            )
            result["rebuilt"] = True
        return result

    def _remove_done(self, result: object) -> None:
        self._release_busy()
        self._bridge.emit("rm_busy", {"busy": False})
        self._bridge.emit("rm_done", {"ok": True, "result": self._json_safe(result)})

    def _remove_error(self, exc: BaseException) -> None:
        self._release_busy()
        self._bridge.emit("rm_log", {"line": f"ERRO: {exc}"})
        self._bridge.emit("rm_busy", {"busy": False})
        self._bridge.emit("rm_done", self._error_payload(exc))

    # ---- generate final ISO (ImgBurn) --------------------------------------

    def get_iso_status(self) -> dict:
        status = core.iso_output_status(self._workspace)
        status["output"] = self._iso_output or status["default_output"]
        return status

    def pick_iso_output(self) -> dict:
        window = self._bridge.window
        if window is None:
            return {"path": None}
        file_types = ("Imagem de disco (*.iso)", "Todos os arquivos (*.*)")
        result = window.create_file_dialog(webview.SAVE_DIALOG, save_filename="MC3_mod.iso", file_types=file_types)
        path = result if isinstance(result, str) else (result[0] if result else None)
        if path:
            if not path.lower().endswith(".iso"):
                path += ".iso"
            self._iso_output = path
            self._options["iso_output"] = path
            core.save_options(self._workspace, self._options)
            self._bridge.emit("gi_output", {"path": path})
        return {"path": self._iso_output}

    def generate_iso(self, payload: dict) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        output = self._iso_output or str(self._workspace.base_path / "ISO" / "MC3_mod.iso")
        label = (payload or {}).get("volume_label") or "MClub"
        self._bridge.emit("gi_busy", {"busy": True})
        self._bridge.emit("gi_status", {"text": "Iniciando geração da ISO final...", "progress": 0})
        run_in_background("generate_iso", lambda: self._generate_worker(output, label),
                          on_done=self._generate_done, on_error=self._generate_error)
        return {"ok": True}

    def _generate_worker(self, output: str, label: str) -> dict:
        out = core.generate_final_iso(
            self._workspace, output, volume_label=label,
            log=lambda line: self._bridge.emit("gi_log", {"line": line}),
            progress=lambda pct, text: self._bridge.emit("gi_status", {"text": text, "progress": pct}),
        )
        size = out.stat().st_size if out.exists() else 0
        return {"output": str(out), "bytes": size}

    def _generate_done(self, result: object) -> None:
        self._release_busy()
        self._bridge.emit("gi_busy", {"busy": False})
        self._bridge.emit("gi_done", {"ok": True, "result": self._json_safe(result)})

    def _generate_error(self, exc: BaseException) -> None:
        self._release_busy()
        self._bridge.emit("gi_log", {"line": f"ERRO: {exc}"})
        self._bridge.emit("gi_busy", {"busy": False})
        self._bridge.emit("gi_done", self._error_payload(exc))

    # ---- install PC tools via winget (ffmpeg / imgburn / foobar) ------------

    def install_tool(self, kind: str) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        self._bridge.emit("inst_busy", {"busy": True})
        self._bridge.emit("inst_status", {"text": f"Instalando {kind}...", "progress": 0})
        run_in_background("install_" + str(kind), lambda: self._install_worker(kind),
                          on_done=self._install_done, on_error=self._install_error)
        return {"ok": True}

    def _install_worker(self, kind: str) -> dict:
        return core.install_tool(
            kind,
            log=lambda line: self._bridge.emit("inst_log", {"line": line}),
            progress=lambda pct, text: self._bridge.emit("inst_status", {"text": text, "progress": pct}),
        )

    def _install_done(self, result: object) -> None:
        self._release_busy()
        self._bridge.emit("inst_busy", {"busy": False})
        self._bridge.emit("inst_done", {"ok": True, "result": self._json_safe(result)})

    def _install_error(self, exc: BaseException) -> None:
        self._release_busy()
        self._bridge.emit("inst_log", {"line": f"ERRO: {exc}"})
        self._bridge.emit("inst_busy", {"busy": False})
        self._bridge.emit("inst_done", self._error_payload(exc))

    def open_download_page(self, kind: str) -> dict:
        """Open the official download page for a tool (user-initiated click)."""
        url = core.tool_download_url(kind)
        if url:
            webbrowser.open(url)
        return {"ok": bool(url), "url": url}

    # ---- error report (one click -> pre-filled email to the developer) ------

    def get_error_report(self, payload: dict) -> dict:
        """Build the full diagnostics + error text WITHOUT opening anything (Copiar)."""
        return {"ok": True, "report": self._build_report(payload or {}, full=True), "email": REPORT_EMAIL}

    def send_error_report(self, payload: dict) -> dict:
        """Open the user's mail client pre-filled (mailto) with a COMPACT report.
        Returns the FULL report too — mailto is length-capped and may not open if no
        mail client is set, so the UI can still show/copy the complete text."""
        p = payload or {}
        subject = "[MC3] Relatorio de erro" + (f" - {p['screen']}" if p.get("screen") else "")
        body = self._build_report(p, full=False)   # compact: fits the mailto URL
        query = urllib.parse.urlencode({"subject": subject, "body": body}, quote_via=urllib.parse.quote)
        mailto = f"mailto:{REPORT_EMAIL}?{query}"
        opened = False
        try:
            os.startfile(mailto)  # noqa: S606 - hand off to the OS default mail client
            opened = True
        except OSError:
            try:
                webbrowser.open(mailto)
                opened = True
            except Exception:  # noqa: BLE001
                opened = False
        return {"ok": True, "opened": opened, "email": REPORT_EMAIL,
                "report": self._build_report(p, full=True)}

    def _build_report(self, payload: dict, *, full: bool = True) -> str:
        ws = self._workspace
        p = payload or {}
        out = ["=== MC3 Music Manager - relatorio ===", ""]

        # Error FIRST — it's the point of the report and survives any mailto cut.
        out += ["-- erro --",
                f"  tela: {p.get('screen') or '(nao informado)'}",
                f"  mensagem: {(p.get('error') or '').strip() or '(sem mensagem)'}"]
        log = (p.get("log") or "").strip()
        if log:
            out += ["  log recente:"] + ["    " + ln for ln in log.splitlines()[-(40 if full else 10):]]

        out += ["", "-- ambiente --",
                f"  app {APP_VERSION} | {platform.platform()} | "
                f"frozen={getattr(sys, 'frozen', False)} | idioma={self._options.get('language', 'pt-BR')}"]
        if full:
            out.append(f"  exe: {sys.executable}")

        out += ["", "-- ferramentas --"]
        checks = [
            ("ffmpeg", core.find_ffmpeg), ("ffprobe", core.find_ffprobe),
            ("rstm_build", core.find_rstm_build), ("dave", core.find_dave),
            ("hash_build", core.find_hash_build), ("strtbl", core.find_strtbl),
            ("streams.lst", core.find_streams_list), ("imgburn", core.find_imgburn),
            ("foobar", core.find_foobar), ("winget", core.find_winget),
        ]
        for name, fn in checks:
            try:
                found = fn()
            except Exception:  # noqa: BLE001
                found = None
            # full report keeps the resolved path; compact (mailto) drops it to fit.
            out.append(f"  {name:12} {'OK' if found else 'FALTA'}" + (f"  {found}" if (full and found) else ""))

        out += ["", "-- workspace --"]
        try:
            st = core.workspace_status(ws)
            songs = len(core.list_songs(ws)) if ws.streams_path.is_dir() else 0
            out.append(f"  preparado={st['prepared']} arquivos_iso={st['has_game_files']} "
                       f"assets={st['has_assets']} streams={st['has_streams']} "
                       f"strings={st['has_strings_json']} musicas={songs}")
            if full:
                out.append(f"  base: {ws.base_path}")
        except Exception as exc:  # noqa: BLE001
            out.append(f"  (falha ao ler o workspace: {exc})")

        out += ["", "(gerado pelo botao 'Enviar relatorio de erro')"]
        return "\n".join(out)

    # ---- prepare project (steps 3+4: extract ISO -> workspace) --------------

    def get_prepare_status(self) -> dict:
        status = core.workspace_status(self._workspace)
        status["iso"] = Path(self._selected_iso).name if self._selected_iso else None
        return status

    def pick_project_iso(self) -> dict:
        window = self._bridge.window
        if window is None:
            return {"path": None}
        file_types = ("Imagem de disco PS2 (*.iso)", "Todos os arquivos (*.*)")
        result = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types)
        path = result[0] if result else None
        self._selected_iso = path
        if path:
            self._options["last_iso"] = path
            core.save_options(self._workspace, self._options)
            self._bridge.emit("pp_iso_selected", {"path": path, "name": Path(path).name})
            self._bridge.emit("pp_log", {"line": f"ISO selecionada: {path}"})
        return {"path": path}

    def copy_iso_files(self) -> dict:
        return self._start_prepare(
            "copiar arquivos da ISO",
            lambda ws, log, prog: core.copy_iso_to_game_files(ws, self._selected_iso, log=log, progress=prog),
            need_iso=True,
        )

    def prepare_files(self) -> dict:
        return self._start_prepare(
            "preparar arquivos para editar",
            lambda ws, log, prog: core.decompile_workspace(ws, force_refresh=False, log=log, progress=prog),
            need_iso=False,
        )

    def prepare_all(self) -> dict:
        return self._start_prepare(
            "preparar tudo automaticamente",
            lambda ws, log, prog: core.prepare_project_from_iso(ws, self._selected_iso, log=log, progress=prog),
            need_iso=True,
        )

    def _start_prepare(self, kind: str, fn, *, need_iso: bool) -> dict:
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        if need_iso and (not self._selected_iso or not Path(self._selected_iso).is_file()):
            self._release_busy()
            self._bridge.emit("pp_log", {"line": "Escolha uma ISO válida primeiro."})
            return {"ok": False, "reason": "no-iso"}
        self._bridge.emit("pp_busy", {"busy": True})
        self._bridge.emit("pp_status", {"text": f"Iniciando: {kind}...", "progress": 0})
        run_in_background(
            f"prepare_{kind}",
            lambda: fn(
                self._workspace,
                lambda line: self._bridge.emit("pp_log", {"line": line}),
                lambda pct, text: self._bridge.emit("pp_status", {"text": text, "progress": pct}),
            ),
            on_done=lambda result: self._prepare_done(kind, result),
            on_error=self._prepare_error,
        )
        return {"ok": True}

    def _prepare_done(self, kind: str, result: object) -> None:
        self._release_busy()
        self._bridge.emit("pp_busy", {"busy": False})
        self._bridge.emit("pp_done", {"ok": True, "kind": kind, "result": self._json_safe(result)})

    def _prepare_error(self, exc: BaseException) -> None:
        self._release_busy()
        self._bridge.emit("pp_log", {"line": f"ERRO: {exc}"})
        self._bridge.emit("pp_busy", {"busy": False})
        self._bridge.emit("pp_done", self._error_payload(exc))

    # ---- reset project (start over with another ISO) -----------------------

    def reset_project(self) -> dict:
        """Wipe the extracted workspace so the user can pick a different ISO and
        prepare from scratch. Destructive — gated by a confirm in the UI + the
        global busy-lock. Reports on the Preparar Projeto channel (pp_*)."""
        if not self._acquire_busy():
            return {"ok": False, "reason": "busy"}
        self._bridge.emit("pp_busy", {"busy": True})
        self._bridge.emit("pp_status", {"text": "Resetando o projeto...", "progress": 0})
        run_in_background(
            "reset_project",
            lambda: core.reset_workspace(
                self._workspace,
                log=lambda line: self._bridge.emit("pp_log", {"line": line}),
                progress=lambda pct, text: self._bridge.emit("pp_status", {"text": text, "progress": pct}),
            ),
            on_done=self._reset_done,
            on_error=self._prepare_error,
        )
        return {"ok": True}

    def _reset_done(self, result: object) -> None:
        self._release_busy()
        # Start over: forget the ISO (in memory AND persisted) so the user must
        # choose one again — otherwise the next boot would re-seed the old ISO.
        self._selected_iso = None
        self._options["last_iso"] = ""
        core.save_options(self._workspace, self._options)
        self._bridge.emit("pp_busy", {"busy": False})
        self._bridge.emit("pp_done", {"ok": True, "kind": "reset", "result": self._json_safe(result)})
