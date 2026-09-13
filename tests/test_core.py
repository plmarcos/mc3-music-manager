"""Tests for the UI-free backend service (backend/core.py).

These are integration tests: they run the REAL bundled tools (ffmpeg +
rstm_build) so a broken tool path or a regression in the conversion pipeline
fails loudly. They skip gracefully if the tools folder is missing.

    pytest                       (from the project root)
    python tests/test_core.py
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 'sys' is used below for the tool-command interpreter check.

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend import core  # noqa: E402


TOOLS_READY = core.tools_status()["ready"]


class ToolsPresence(unittest.TestCase):
    def test_tools_are_discovered(self):
        status = core.tools_status()
        self.assertTrue(status["ready"], f"tools not ready: {status}")
        self.assertTrue(Path(status["ffmpeg"]).is_file())
        self.assertTrue(Path(status["rstm_build"]).is_file())


@unittest.skipUnless(TOOLS_READY, "PS2 tools not available")
class WavToRsmRoundTrip(unittest.TestCase):
    def test_wav_converts_to_nonempty_rsm(self):
        ffmpeg = core.find_ffmpeg()
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "tone.wav"
            # 1s mono 48k sine — deliberately NOT the 44100/stereo/s16 that
            # rstm_build wants, so the ffmpeg-normalize branch is exercised.
            subprocess.run(
                [str(ffmpeg), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 "-ar", "48000", "-ac", "1", str(wav)],
                check=True, capture_output=True,
            )
            out = core.convert_audio_to_rsm(wav)
            self.assertTrue(out.exists(), "RSM was not created")
            self.assertGreater(out.stat().st_size, 0, "RSM is empty")

    def test_missing_source_raises_toolerror(self):
        with self.assertRaises(core.ToolError):
            core.convert_audio_to_rsm(Path("does-not-exist.wav"))


class ConversionScratchHygiene(unittest.TestCase):
    """Regression: WinError 32 batch crash + scratch polluting STREAMS.DAT."""

    def test_scratch_wav_not_inside_output_parent(self):
        # The scratch WAV must NOT be created inside output.parent
        # (STREAMS/Music/<genre>/) — a folder packed into STREAMS.DAT — or a
        # failed Windows cleanup would smuggle it into the game archive. Mock the
        # tools so we can see exactly where the WAV lands, no real audio needed.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "song.mp3"
            src.write_bytes(b"fake-mp3")
            out = Path(td) / "STREAMS" / "Music" / "Techno" / "song.rsm"
            out.parent.mkdir(parents=True)

            seen = {}

            def fake_run(cmd, cwd=None, log=None, input_text="", timeout=None):
                target = Path(cmd[-1])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x" * 16)  # non-empty: a real tool produces bytes
                if "ffmpeg" in str(cmd[0]).lower():
                    seen["wav"] = target
                return (0, "")

            orig = (core._run, core.find_ffmpeg, core.find_rstm_build)
            try:
                core._run = fake_run
                core.find_ffmpeg = lambda: Path("ffmpeg.exe")
                core.find_rstm_build = lambda: Path("rstm_build.exe")
                result = core.convert_audio_to_rsm(src, out)
            finally:
                core._run, core.find_ffmpeg, core.find_rstm_build = orig

            self.assertTrue(result.exists())
            self.assertIn("wav", seen)
            self.assertNotIn(out.parent, seen["wav"].parents)  # scratch is elsewhere
            self.assertEqual([p.name for p in out.parent.iterdir()], ["song.rsm"])

    def test_rstm_build_targets_scratch_not_the_game_folder(self):
        # rstm_build writes its own tmp_*.ads/.wav NEXT TO ITS OUTPUT and leaves
        # them there when ps2str fails. So we must never point it straight at
        # STREAMS/Music/<genre>/ (that folder gets packed into STREAMS.DAT).
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "song.mp3"
            src.write_bytes(b"fake-mp3")
            out = Path(td) / "STREAMS" / "Music" / "Techno" / "song.rsm"
            out.parent.mkdir(parents=True)
            seen = {}

            def fake_run(cmd, cwd=None, log=None, input_text="", timeout=None):
                target = Path(cmd[-1])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x" * 16)
                if "ffmpeg" in str(cmd[0]).lower():
                    seen["wav_out"] = target
                else:
                    seen["rstm_out"] = target  # where rstm_build was told to write
                return (0, "")

            orig = (core._run, core.find_ffmpeg, core.find_rstm_build)
            try:
                core._run = fake_run
                core.find_ffmpeg = lambda: Path("ffmpeg.exe")
                core.find_rstm_build = lambda: Path("rstm_build.exe")
                core.convert_audio_to_rsm(src, out)
            finally:
                core._run, core.find_ffmpeg, core.find_rstm_build = orig

            self.assertTrue(out.is_file(), "the finished .rsm must land in the game folder")
            # rstm_build must have been aimed at scratch, NOT at the packed folder
            self.assertNotIn(out.parent, seen["rstm_out"].parents)
            self.assertNotIn(out.parent, seen["wav_out"].parents)
            self.assertEqual([p.name for p in out.parent.iterdir()], ["song.rsm"])

    def test_tool_detail_surfaces_the_real_reason(self):
        self.assertIn("ps2str", core._tool_detail("blah\nERROR: ps2str.exe returned 1\ndone"))
        self.assertEqual(core._tool_detail(""), "")

    def test_sweep_removes_stale_scratch_keeps_content(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            genre = ws.streams_path / "Music" / "Techno"
            genre.mkdir(parents=True)
            real_rsm = genre / "RealSong.rsm"
            real_rsm.write_bytes(b"rsm")
            stale = genre / "tmpABCDEFGH"              # old-bug scratch inside STREAMS
            stale.mkdir()
            (stale / "leftover.wav").write_bytes(b"x")
            base_stale = ws.base_path / "mc3_rsm_zzz"  # our own prefix, in base
            base_stale.mkdir()

            removed = core.sweep_stale_scratch(ws)

            self.assertFalse(stale.exists())
            self.assertFalse(base_stale.exists())
            self.assertTrue(real_rsm.exists())         # real content untouched
            self.assertEqual(removed, 2)


class RobustnessSubprocess(unittest.TestCase):
    """Timeout watchdog + user-cancel kill switch on _run."""

    def test_run_timeout_raises_toolerror_fast(self):
        import time
        start = time.monotonic()
        with self.assertRaises(core.ToolError):
            core._run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1.0)
        self.assertLess(time.monotonic() - start, 10.0)  # killed near the 1s deadline

    def test_cancel_active_kills_inflight_run(self):
        import threading
        import time
        result = {}

        def worker():
            try:
                result["code"], _ = core._run([sys.executable, "-c", "import time; time.sleep(30)"])
            except Exception as exc:  # noqa: BLE001
                result["exc"] = exc

        t = threading.Thread(target=worker)
        t.start()
        for _ in range(60):  # wait until the proc registers
            if core._ACTIVE_PROCS:
                break
            time.sleep(0.05)
        killed = core.cancel_active()
        t.join(timeout=8)

        self.assertGreaterEqual(killed, 1)
        self.assertFalse(t.is_alive(), "worker should finish after the proc is killed")
        self.assertEqual(len(core._ACTIVE_PROCS), 0, "registry must be cleared")

    def test_run_tolerates_undecodable_output(self):
        # Regression: ffmpeg's build banner emitted a byte (0x8d) that the Windows
        # cp1252 locale couldn't decode, crashing _run mid-stream and aborting the
        # whole batch. _run must decode as UTF-8 with errors="replace".
        code, out = core._run(
            [sys.executable, "-c",
             r"import sys; sys.stdout.buffer.write(b'ok\x8dmore\n'); sys.stdout.flush()"])
        self.assertEqual(code, 0)
        self.assertIn("ok", out)
        self.assertIn("more", out)


class Options(unittest.TestCase):
    """Persisted preferences (options.json)."""

    def test_options_round_trip_and_merge(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            self.assertEqual(core.load_options(ws)["language"], "pt-BR")  # defaults
            self.assertFalse(core.options_path(ws).exists())

            core.save_options(ws, {"language": "en", "iso_volume_label": "MYDISC", "bogus": 1})
            self.assertTrue(core.options_path(ws).is_file())

            loaded = core.load_options(ws)
            self.assertEqual(loaded["language"], "en")
            self.assertEqual(loaded["iso_volume_label"], "MYDISC")
            self.assertNotIn("bogus", loaded)          # unknown keys dropped
            self.assertEqual(loaded["remove_audio"], True)  # default preserved

    def test_load_options_tolerates_corrupt_file(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            core.options_path(ws).write_text("{not json", encoding="utf-8")
            self.assertEqual(core.load_options(ws), core.DEFAULT_OPTIONS)


class InstallTool(unittest.TestCase):
    """winget install command shape + graceful no-winget fallback."""

    def test_install_tool_builds_winget_command(self):
        calls = {}

        def fake_run(cmd, cwd=None, log=None, input_text="", timeout=None):
            calls["cmd"] = [str(c) for c in cmd]
            return (0, "")

        orig = (core._run, core.find_winget)
        try:
            core._run = fake_run
            core.find_winget = lambda: Path("winget.exe")
            res = core.install_tool("ffmpeg")
        finally:
            core._run, core.find_winget = orig

        self.assertTrue(res["ok"])
        self.assertIn("winget.exe", calls["cmd"][0])
        self.assertIn("--id", calls["cmd"])
        self.assertIn("Gyan.FFmpeg", calls["cmd"])
        self.assertIn("--silent", calls["cmd"])

    def test_install_tool_without_winget_offers_page(self):
        orig = core.find_winget
        try:
            core.find_winget = lambda: None
            res = core.install_tool("imgburn")
        finally:
            core.find_winget = orig
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "no-winget")
        self.assertIn("imgburn.com", res["download_url"])

    def test_install_tool_unknown_kind_raises(self):
        with self.assertRaises(core.ToolError):
            core.install_tool("nope")


class I18n(unittest.TestCase):
    """Translation tables load from frontend/locales/*.json."""

    def test_load_translations_has_three_languages(self):
        tr = core.load_translations()
        self.assertEqual(set(tr.keys()), {"pt-BR", "en", "es"})
        self.assertEqual(tr["en"]["rail.inicio"], "Home")
        self.assertEqual(tr["pt-BR"]["rail.inicio"], "Início")
        self.assertIn("title.settings", tr["es"])
        # every locale exposes the same key set (no missing translations)
        self.assertEqual(set(tr["en"]), set(tr["pt-BR"]))
        self.assertEqual(set(tr["es"]), set(tr["pt-BR"]))


class MissingToolsGating(unittest.TestCase):
    """A screen must not offer an action whose tool is missing — otherwise the
    task only dies halfway through (found by simulating a toolless machine)."""

    def test_iso_not_ready_without_ps2_rebuild_tools(self):
        # generate_final_iso rebuilds the DATs first, so dave/hash_build are required.
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            ws.game_files_path.mkdir(parents=True)
            (ws.game_files_path / "SYSTEM.CNF").write_text("BOOT2 = x", encoding="utf-8")
            saved = (core.find_imgburn, core.find_dave, core.find_hash_build)
            try:
                core.find_imgburn = lambda: Path("ImgBurn.exe")
                core.find_dave = lambda: None          # tools/ broken
                core.find_hash_build = lambda: None
                st = core.iso_output_status(ws)
                self.assertFalse(st["has_rebuild_tools"])
                self.assertFalse(st["ready"], "ISO must not be offered without the PS2 tools")
                # ...and with them present it IS ready
                core.find_dave = lambda: Path("dave.py")
                core.find_hash_build = lambda: Path("hash_build.py")
                self.assertTrue(core.iso_output_status(ws)["ready"])
            finally:
                core.find_imgburn, core.find_dave, core.find_hash_build = saved

    def test_generate_iso_raises_clearly_without_imgburn(self):
        with tempfile.TemporaryDirectory() as td:
            saved = core.find_imgburn
            try:
                core.find_imgburn = lambda: None
                with self.assertRaises(core.ToolError) as ctx:
                    core.generate_final_iso(core.Workspace(Path(td)), Path(td) / "out.iso")
                self.assertIn("ImgBurn", str(ctx.exception))
            finally:
                core.find_imgburn = saved


class PlaylistTargets(unittest.TestCase):
    """Regression: an added song must ALSO land in its genre's RACE playlist.
    Without this expansion it only hit the city cruise blocks and never showed
    up in the game (found in a real run: added songs were in atlanta/detroit/
    sd/tokyo.play but missing from techno_race_music.play, unlike stock songs)."""

    def _make_city(self, td, city="atlanta"):
        ws = core.Workspace(Path(td))
        music = ws.playlists_root / city / "music"
        music.mkdir(parents=True)
        for name in (f"{city}.play", "techno_race_music.play", "rap_race_music.play"):
            (music / name).write_text("num_songs: 0\n", encoding="utf-8")
        return music

    def test_city_pick_expands_to_genre_race_playlist(self):
        with tempfile.TemporaryDirectory() as td:
            music = self._make_city(td)
            names = [p.name for p in core.playlist_targets_for_genre([music / "atlanta.play"], "Techno")]
            self.assertIn("atlanta.play", names)
            self.assertIn("techno_race_music.play", names)   # the fix
            self.assertNotIn("rap_race_music.play", names)   # other genre untouched

    def test_non_city_playlist_kept_only_when_genre_matches(self):
        with tempfile.TemporaryDirectory() as td:
            music = self._make_city(td)
            # rap_race_music is HipHop's default -> dropped for Techno...
            self.assertEqual(core.playlist_targets_for_genre([music / "rap_race_music.play"], "Techno"), [])
            # ...and kept for HipHop.
            names = [p.name for p in core.playlist_targets_for_genre([music / "rap_race_music.play"], "HipHop")]
            self.assertEqual(names, ["rap_race_music.play"])

    def test_frontend_menu_music_never_matched_by_content(self):
        # frontend.play holds HipHop menu tracks, so the content heuristic used to
        # drag HipHop adds into it — but no stock song is listed there.
        with tempfile.TemporaryDirectory() as td:
            music = self._make_city(td)
            frontend = music / "frontend.play"
            frontend.write_text("num_songs: 1\nmusic\\HipHop\\SomeMenuTrack\n", encoding="utf-8")
            self.assertEqual(core.playlist_targets_for_genre([frontend], "HipHop"), [])
            # and a city pick must not pull it in either
            names = [p.name for p in core.playlist_targets_for_genre([music / "atlanta.play"], "HipHop")]
            self.assertNotIn("frontend.play", names)

    def test_expansion_dedups(self):
        with tempfile.TemporaryDirectory() as td:
            music = self._make_city(td)
            targets = core.playlist_targets_for_genre(
                [music / "atlanta.play", music / "techno_race_music.play"], "Techno")
            self.assertEqual(len(targets), len(set(targets)))
            self.assertEqual(sorted(p.name for p in targets), ["atlanta.play", "techno_race_music.play"])


class InspectIsoUnit(unittest.TestCase):
    """Pure-logic tests for the ISO inspector. The mount/dismount path needs a
    real MC3 image and a Windows drive, so it is exercised manually, not here."""

    def test_parse_boot_id_extracts_slus(self):
        text = "BOOT2 = cdrom0:\\SLUS_213.55;1\nVER = 1.00\nVMODE = NTSC\n"
        self.assertEqual(core._parse_boot_id(text), "SLUS_213.55")

    def test_parse_boot_id_is_case_insensitive(self):
        text = "boot2=cdrom0:\\SLUS_213.55;1"
        self.assertEqual(core._parse_boot_id(text), "SLUS_213.55")

    def test_parse_boot_id_missing_returns_empty(self):
        self.assertEqual(core._parse_boot_id("no boot line here"), "")

    def test_supported_boot_id_is_mc3(self):
        self.assertIn("SLUS_213.55", core.SUPPORTED_BOOT_IDS)

    def test_powershell_quote_escapes_single_quotes(self):
        self.assertEqual(core._powershell_quote("a'b"), "'a''b'")

    def test_inspect_missing_iso_raises_toolerror(self):
        with self.assertRaises(core.ToolError):
            core.inspect_iso(Path("does-not-exist.iso"))


class BackupRestore(unittest.TestCase):
    """Fully self-contained (no external tools) — pure file operations."""

    def test_backup_and_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))

            # A file that exists before the operation...
            existing = ws.strings_json_path  # base/mcstrings02.json
            existing.write_text("original", encoding="utf-8")
            # ...and one that does NOT exist yet (will be "created" by the op).
            created = ws.base_path / "STREAMS.DAT"

            backup_dir = core.create_backup_session(ws, "test", timestamp="20200101_000000")
            core.backup_file(ws, existing, backup_dir)
            core.backup_file(ws, created, backup_dir)  # existed=False recorded

            # Simulate a destructive operation:
            existing.write_text("MODIFIED", encoding="utf-8")
            created.write_text("made by the operation", encoding="utf-8")

            result = core.restore_backup(ws, backup_dir)

            # existed=True -> content restored; existed=False -> file deleted.
            self.assertEqual(existing.read_text(encoding="utf-8"), "original")
            self.assertFalse(created.exists(), "file created by the op should be removed")
            self.assertEqual(result["restored"], 1)
            self.assertEqual(result["removed"], 1)

    def test_backup_dir_name_and_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            backup_dir = core.create_backup_session(ws, "add_music", timestamp="20260712_120000")
            self.assertEqual(backup_dir.name, "20260712_120000_add_music")
            self.assertTrue((backup_dir / "backup_manifest.json").is_file())
            self.assertEqual(core.latest_backup_dir(ws), backup_dir)

    def test_restore_missing_manifest_raises(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            with self.assertRaises(core.ToolError):
                core.restore_backup(ws, Path(td) / "nope")


class RebuildPreflight(unittest.TestCase):
    def test_rebuild_streams_without_streams_dir_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(core.ToolError):
                core.rebuild_streams_dat(core.Workspace(Path(td)))

    def test_rebuild_assets_without_assets_dir_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(core.ToolError):
                core.rebuild_assets_dat(core.Workspace(Path(td)))

    def test_publish_missing_dat_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(core.ToolError):
                core.publish_dat_to_game_files(core.Workspace(Path(td)), "STREAMS.DAT")

    def test_publish_copies_dat_to_game_files(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            (ws.base_path / "STREAMS.DAT").write_bytes(b"DATDATA")
            target = core.publish_dat_to_game_files(ws, "STREAMS.DAT")
            self.assertEqual(target, ws.game_files_path / "STREAMS.DAT")
            self.assertEqual(target.read_bytes(), b"DATDATA")

    def test_tool_command_runs_py_with_interpreter(self):
        cmd = core._tool_command(Path("tools/hash_build.py"), "B", "a", "b")
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[-3:], ["B", "a", "b"])

    def test_tool_command_runs_exe_directly(self):
        exe = Path("tools/hash_build.exe")
        cmd = core._tool_command(exe, "B")
        self.assertEqual(cmd[0], str(exe))  # exe runs directly, no interpreter
        self.assertNotEqual(cmd[0], sys.executable)

    def test_bundled_ps2_tools_present(self):
        # The three tools were copied into tools/ for the rebuild step.
        self.assertIsNotNone(core.find_dave(), "dave not found in tools/")
        self.assertIsNotNone(core.find_hash_build(), "hash_build not found in tools/")
        self.assertIsNotNone(core.find_strtbl(), "strtbl not found in tools/")


class AddMusicHelpers(unittest.TestCase):
    """Pure helpers for the add pipeline — no PS2 tools / ffprobe needed."""

    def test_string_key_and_playlist_entry(self):
        self.assertEqual(core.string_key("Techno", "Foo"), "music_Techno_Foo")
        self.assertEqual(core.playlist_entry("Techno", "Foo"), "music\\Techno\\Foo")

    def test_normalize_game_text(self):
        self.assertEqual(core.normalize_game_text('  He said "hi" '), "He said 'hi'")
        self.assertEqual(core.normalize_game_text("﻿Title"), "Title")  # BOM stripped
        self.assertEqual(core.normalize_game_text("a   b\tc"), "a b c")

    def test_coerce_asset_name_basic_and_noise(self):
        self.assertEqual(core.coerce_asset_name("", artist="DJ Test", title="My Song"), "DJ_Test_My_Song")
        self.assertEqual(core.coerce_asset_name("Song (Official Video)"), "Song")

    def test_coerce_asset_name_caps_length_with_hash(self):
        name = core.coerce_asset_name("", artist="", title="a" * 100)
        self.assertLessEqual(len(name), core.ASSET_NAME_MAX_LENGTH)

    def test_split_source_metadata_dash(self):
        title, artist = core.split_source_metadata("DJ Test - My Song")
        self.assertEqual((title, artist), ("My Song", "DJ Test"))

    def test_song_entry_six_languages(self):
        entry = core.song_entry({}, "Techno", "My Song", "DJ Test")
        self.assertEqual(len(entry), 6)
        self.assertEqual(entry["Language 00"]["text"], '"My Song"\nby DJ Test')
        self.assertEqual(entry["Language 01"]["text"], '"My Song"\nde DJ Test')
        self.assertEqual(entry["Language 05"]["text"], "")  # empty template stays empty
        self.assertEqual(entry["Language 00"]["font"]["name"], "smallspace")

    def test_insert_entry_near_genre_block_orders_within_genre(self):
        entries = {"music_Techno_aaa": {}, "music_Techno_ccc": {}, "music_Rock_zzz": {}}
        result = core.insert_entry_near_genre_block(entries, "music_Techno_bbb", {"x": 1}, "Techno")
        self.assertEqual(
            list(result.keys()),
            ["music_Techno_aaa", "music_Techno_bbb", "music_Techno_ccc", "music_Rock_zzz"],
        )

    def test_update_playlist_adds_entry_and_updates_num_songs(self):
        with tempfile.TemporaryDirectory() as td:
            pl = Path(td) / "techno.play"
            pl.write_text("num_songs: 1\nmusic\\Techno\\Existing_Aaa\n", encoding="utf-8")
            self.assertTrue(core.update_playlist(pl, "music\\Techno\\New_Bbb", "add"))
            text = pl.read_text(encoding="utf-8")
            self.assertIn("num_songs: 2", text)
            self.assertIn("music\\Techno\\New_Bbb", text)
            # Adding the same entry again is a no-op.
            self.assertFalse(core.update_playlist(pl, "music\\Techno\\New_Bbb", "add"))

    def test_build_add_spec_paths_and_keys(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            src = Path(td) / "song.mp3"
            src.write_bytes(b"x")
            # asset_name non-empty -> no ffprobe; artist+title compact wins (faithful).
            spec = core.build_add_spec(ws, src, "My Song", "DJ Test", "Techno", "MySong")
            self.assertEqual(spec["asset_name"], "DJ_Test_My_Song")
            self.assertEqual(spec["string_key"], "music_Techno_DJ_Test_My_Song")
            self.assertEqual(spec["playlist_entry"], "music\\Techno\\DJ_Test_My_Song")
            self.assertEqual(spec["stream_target"], ws.streams_path / "Music" / "Techno" / "DJ_Test_My_Song.rsm")

    def test_build_add_spec_bad_genre_raises(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            src = Path(td) / "song.mp3"
            src.write_bytes(b"x")
            with self.assertRaises(core.ToolError):
                core.build_add_spec(ws, src, "T", "A", "NotAGenre", "asset")

    def test_add_songs_empty_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(core.ToolError):
                core.add_songs(core.Workspace(Path(td)), [])

    def test_list_target_playlists_only_music_folder(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            (ws.playlists_root / "tokyo" / "music").mkdir(parents=True)
            (ws.playlists_root / "tokyo" / "music" / "techno_race_music.play").write_text("x", encoding="utf-8")
            # These must be EXCLUDED (not under a music/ folder):
            (ws.playlists_root / "tokyo" / "loose.play").write_text("x", encoding="utf-8")
            (ws.playlists_root / "tokyo" / "other").mkdir(parents=True)
            (ws.playlists_root / "tokyo" / "other" / "weird.play").write_text("x", encoding="utf-8")
            targets = core.list_target_playlists(ws)
            self.assertEqual([t["name"] for t in targets], ["techno_race_music.play"])
            self.assertEqual(targets[0]["city"], "tokyo")


class ReadOnlyOverwrite(unittest.TestCase):
    """Regression tests for the audit findings: writes must clear the read-only
    bit (extracted-ISO game files are read-only) and rstm runs via _tool_command."""

    def test_make_writable_allows_overwrite_and_unlink(self):
        import os
        import stat as _stat
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "readonly.play"
            f.write_text("num_songs: 0\n", encoding="utf-8")
            os.chmod(f, _stat.S_IREAD)  # make read-only
            core._make_writable(f)
            f.write_text("num_songs: 1\n", encoding="utf-8")  # would raise if still RO
            self.assertIn("num_songs: 1", f.read_text(encoding="utf-8"))
            core._make_writable(f)
            f.unlink()
            self.assertFalse(f.exists())

    def test_update_playlist_overwrites_readonly_file(self):
        import os
        import stat as _stat
        with tempfile.TemporaryDirectory() as td:
            pl = Path(td) / "techno.play"
            pl.write_text("num_songs: 0\n", encoding="utf-8")
            os.chmod(pl, _stat.S_IREAD)  # simulate a read-only ISO extract
            self.assertTrue(core.update_playlist(pl, "music\\Techno\\Foo", "add"))
            self.assertIn("music\\Techno\\Foo", pl.read_text(encoding="utf-8"))

    def test_rstm_stage_uses_tool_command(self):
        # The .py-only fallback must run through the interpreter, not directly.
        cmd = core._tool_command(Path("tools/wav to rsm/rstm_build.py"), "in.wav", "-o", "out.rsm")
        self.assertEqual(cmd[0], sys.executable)


class PrepareProject(unittest.TestCase):
    """Extraction pipeline (steps 3+4). Real dave X / hash_build X need a real
    ISO/DATs, so here we cover discovery, preflight, copy-tree and rmtree."""

    def test_streams_list_bundled(self):
        self.assertIsNotNone(core.find_streams_list(), "MC3_PS2_Streams.lst not in tools/")

    def test_copy_tree_copies_all_files(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src"
            (src / "sub").mkdir(parents=True)
            (src / "a.txt").write_text("A", encoding="utf-8")
            (src / "sub" / "b.txt").write_text("B", encoding="utf-8")
            dst = Path(td) / "dst"
            count = core._copy_tree_with_progress(src, dst)
            self.assertEqual(count, 2)
            self.assertEqual((dst / "a.txt").read_text(encoding="utf-8"), "A")
            self.assertEqual((dst / "sub" / "b.txt").read_text(encoding="utf-8"), "B")

    def test_decompile_without_game_files_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(core.ToolError):
                core.decompile_workspace(core.Workspace(Path(td)))

    def test_decompile_without_dats_raises(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            ws.game_files_path.mkdir(parents=True)  # dir exists but no ASSETS.DAT
            with self.assertRaises(core.ToolError):
                core.decompile_workspace(ws)

    def test_prepare_from_missing_iso_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(core.ToolError):
                core.prepare_project_from_iso(core.Workspace(Path(td)), Path("nope.iso"))

    def test_workspace_status_fresh_is_unprepared(self):
        with tempfile.TemporaryDirectory() as td:
            status = core.workspace_status(core.Workspace(Path(td)))
            self.assertFalse(status["prepared"])
            self.assertFalse(status["has_game_files"])
            self.assertTrue(status["tools_ready"])  # tools + .lst are bundled

    def test_rmtree_removes_readonly_tree(self):
        import os
        import stat as _stat
        with tempfile.TemporaryDirectory() as td:
            tree = Path(td) / "ro"
            tree.mkdir()
            f = tree / "locked.bin"
            f.write_bytes(b"x")
            os.chmod(f, _stat.S_IREAD)  # read-only file blocks a naive rmtree
            core._rmtree(tree)
            self.assertFalse(tree.exists())

    def test_reset_workspace_wipes_artifacts_keeps_backups(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            # Fake a fully prepared workspace.
            ws.game_files_path.mkdir(parents=True)
            (ws.game_files_path / "ASSETS.DAT").write_bytes(b"x")
            ws.assets_path.mkdir(parents=True)
            (ws.assets_path / "f.bin").write_bytes(b"x")
            ws.streams_path.mkdir(parents=True)
            (ws.base_path / "ASSETS.DAT").write_bytes(b"x")
            (ws.base_path / "STREAMS.DAT").write_bytes(b"x")
            ws.root_strtbl_path.write_bytes(b"x")
            ws.strings_json_path.write_text("{}", encoding="utf-8")
            # A backup that MUST survive the reset.
            keep = ws.backups_dir / "session01" / "keep.txt"
            keep.parent.mkdir(parents=True)
            keep.write_text("safe", encoding="utf-8")

            result = core.reset_workspace(ws)

            for gone in (ws.game_files_path, ws.assets_path, ws.streams_path,
                         ws.base_path / "ASSETS.DAT", ws.base_path / "STREAMS.DAT",
                         ws.root_strtbl_path, ws.strings_json_path):
                self.assertFalse(gone.exists(), f"{gone} should be gone")
            self.assertTrue(keep.exists(), "backups must be preserved")
            self.assertFalse(core.workspace_status(ws)["prepared"])
            self.assertEqual(result["count"], 7)

    def test_reset_workspace_empty_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            result = core.reset_workspace(core.Workspace(Path(td)))
            self.assertEqual(result["count"], 0)


class RemoveMusic(unittest.TestCase):
    """Enumeration + the tool-free part of removal (audio + playlists)."""

    def _make_song(self, td):
        ws = core.Workspace(Path(td))
        music_dir = ws.streams_path / "Music" / "Techno"
        music_dir.mkdir(parents=True)
        (music_dir / "Foo.rsm").write_bytes(b"rsm-bytes")
        pl_dir = ws.playlists_root / "atlanta" / "music"
        pl_dir.mkdir(parents=True)
        pl = pl_dir / "techno_race_music.play"
        pl.write_text("num_songs: 1\nmusic\\Techno\\Foo\n", encoding="utf-8")
        ws.strings_json_path.write_text('{"data": {"music_Techno_Foo": {}}}', encoding="utf-8")
        return ws, music_dir / "Foo.rsm", pl

    def test_list_songs_enumerates_with_counts(self):
        with tempfile.TemporaryDirectory() as td:
            ws, _rsm, _pl = self._make_song(td)
            songs = core.list_songs(ws)
            self.assertEqual(len(songs), 1)
            self.assertEqual(songs[0]["genre"], "Techno")
            self.assertEqual(songs[0]["asset_name"], "Foo")
            self.assertEqual(songs[0]["playlist_count"], 1)
            self.assertTrue(songs[0]["has_strings"])

    def test_list_songs_empty_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(core.list_songs(core.Workspace(Path(td))), [])

    def test_remove_audio_and_playlists_without_tools(self):
        with tempfile.TemporaryDirectory() as td:
            ws, rsm, pl = self._make_song(td)
            result = core.remove_songs(
                ws, [{"genre": "Techno", "asset_name": "Foo"}],
                remove_audio=True, remove_playlists=True, remove_strings=False,
            )
            self.assertFalse(rsm.exists(), "the .rsm should be deleted")
            self.assertNotIn("music\\Techno\\Foo", pl.read_text(encoding="utf-8"))
            self.assertEqual(result["removed_audio"], 1)
            self.assertEqual(result["playlist_changes"], 1)

    def test_remove_empty_selection_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(core.ToolError):
                core.remove_songs(core.Workspace(Path(td)), [])

    def test_remove_no_action_raises(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(core.ToolError):
                core.remove_songs(
                    core.Workspace(Path(td)), [{"genre": "Techno", "asset_name": "Foo"}],
                    remove_audio=False, remove_playlists=False, remove_strings=False,
                )


class Overview(unittest.TestCase):
    def test_overview_shape(self):
        with tempfile.TemporaryDirectory() as td:
            ov = core.overview(core.Workspace(Path(td)))
            for key in ("prepared", "song_count", "playlist_count", "programs", "steps"):
                self.assertIn(key, ov)
            self.assertEqual(len(ov["steps"]), 5)
            self.assertFalse(ov["prepared"])
            self.assertEqual(ov["song_count"], 0)
            # each step has a state and a navigation target
            for step in ov["steps"]:
                self.assertIn(step["state"], ("ok", "pending", "available", "blocked"))
                self.assertTrue(step["target"])


class GenerateIso(unittest.TestCase):
    def test_sanitize_volume_label(self):
        self.assertEqual(core._sanitize_volume_label("My Label!"), "My_Label")
        self.assertEqual(core._sanitize_volume_label(""), "MClub")
        self.assertLessEqual(len(core._sanitize_volume_label("x" * 60)), 32)

    def test_build_imgburn_command_shape(self):
        cmd = core._build_imgburn_command(Path("ImgBurn.exe"), Path("C:/gf"), Path("C:/out.iso"), "My Label")
        self.assertEqual(cmd[1:3], ["/MODE", "BUILD"])
        self.assertIn("/DEST", cmd)
        # /SRC value must end with a trailing backslash (ImgBurn folder source)
        src_val = cmd[cmd.index("/SRC") + 1]
        self.assertTrue(src_val.endswith("\\"))
        # sanitized label is used
        self.assertIn("My_Label", cmd)

    def test_iso_output_status_keys(self):
        with tempfile.TemporaryDirectory() as td:
            status = core.iso_output_status(core.Workspace(Path(td)))
            for key in ("imgburn", "has_game_files", "has_system_cnf", "ready", "default_output"):
                self.assertIn(key, status)
            self.assertFalse(status["has_system_cnf"])

    def test_generate_without_workspace_raises(self):
        with tempfile.TemporaryDirectory() as td:
            # Raises either "ImgBurn nao encontrado" or "Arquivos da ISO nao encontrados".
            with self.assertRaises(core.ToolError):
                core.generate_final_iso(core.Workspace(Path(td)), Path(td) / "out.iso")


class BusyLock(unittest.TestCase):
    """The busy-lock must be atomic: a second acquire fails until release."""

    def _make_api(self):
        try:
            from backend.bridge import Bridge
            from backend.api import Api
        except Exception as exc:  # pragma: no cover - pywebview missing
            self.skipTest(f"api/webview not importable: {exc}")
        return Api(Bridge())

    def test_acquire_is_exclusive_until_released(self):
        api = self._make_api()
        self.assertTrue(api._acquire_busy(), "first acquire should win")
        self.assertFalse(api._acquire_busy(), "second acquire must fail while busy")
        api._release_busy()
        self.assertTrue(api._acquire_busy(), "acquire should succeed again after release")


class ConversionFailureReporting(unittest.TestCase):
    """Regressao: o ramo do ffmpeg descartava a saida do _run e depois a usava em
    _tool_detail(out) -> UnboundLocalError em vez da mensagem. Atingia tambem quem
    apertava "Cancelar" durante a conversao (matar o processo = codigo != 0)."""

    def setUp(self):
        self._run, self._ff, self._rb = core._run, core.find_ffmpeg, core.find_rstm_build
        core.find_ffmpeg = lambda: Path("ffmpeg.exe")
        core.find_rstm_build = lambda: Path("rstm_build.exe")

    def tearDown(self):
        core._run, core.find_ffmpeg, core.find_rstm_build = self._run, self._ff, self._rb

    def test_ffmpeg_failure_raises_toolerror_with_detail(self):
        core._run = lambda *a, **k: (1, "Error: Invalid data found when processing input")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "musica.mp3"
            src.write_bytes(b"nao e audio de verdade")
            with self.assertRaises(core.ToolError) as ctx:
                core.convert_audio_to_rsm(src, Path(td) / "out.rsm")
            message = str(ctx.exception)
            self.assertIn("ffmpeg falhou", message)
            self.assertIn("Invalid data found", message,
                          "o motivo real da ferramenta tem de aparecer na mensagem")


class RebuildFailureReporting(unittest.TestCase):
    def test_rebuild_failure_carries_tool_output(self):
        if core.find_dave() is None:
            self.skipTest("dave nao disponivel")
        original = core._run
        core._run = lambda *a, **k: (2, "dave: Error! Refusing to write outside")
        try:
            with tempfile.TemporaryDirectory() as td:
                ws = core.Workspace(Path(td))
                ws.assets_path.mkdir(parents=True)
                with self.assertRaises(core.ToolError) as ctx:
                    core.rebuild_assets_dat(ws)
                self.assertIn("Refusing to write outside", str(ctx.exception))
        finally:
            core._run = original


class BatchDuplicateTargets(unittest.TestCase):
    """coerce_asset_name normaliza agressivamente (tira official/video/hq/bitrate),
    entao dois arquivos distintos podem reivindicar o MESMO .rsm — e
    existing_spec_targets so olha o disco, entao nao enxergava isso."""

    def _two_colliding_specs(self, td):
        ws = core.Workspace(Path(td))
        (ws.streams_path / "Music" / "Rock").mkdir(parents=True)
        a = Path(td) / "Artista - Musica (Official Video).mp3"
        b = Path(td) / "Artista - Musica [HQ].mp3"
        a.write_bytes(b"x")
        b.write_bytes(b"x")
        return ws, [core.build_add_spec(ws, a, "Musica", "Artista", "Rock", ""),
                    core.build_add_spec(ws, b, "Musica", "Artista", "Rock", "")]

    def test_colliding_sources_share_one_target(self):
        with tempfile.TemporaryDirectory() as td:
            _ws, specs = self._two_colliding_specs(td)
            self.assertEqual(specs[0]["stream_target"], specs[1]["stream_target"])

    def test_add_songs_refuses_duplicate_targets(self):
        with tempfile.TemporaryDirectory() as td:
            ws, specs = self._two_colliding_specs(td)
            with self.assertRaises(core.ToolError) as ctx:
                core.add_songs(ws, specs)
            self.assertIn("repetido", str(ctx.exception))

    def test_distinct_targets_are_not_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            ws, specs = self._two_colliding_specs(td)
            other = Path(td) / "Artista - Outra.mp3"
            other.write_bytes(b"x")
            ok = core.build_add_spec(ws, other, "Outra", "Artista", "Rock", "")
            self.assertEqual(core.find_duplicate_spec_targets([specs[0], ok]), [])


class InputValidation(unittest.TestCase):
    """genero/nome interno/playlist vindos da UI viram CAMINHO e terminam num
    unlink(), entao sao conferidos antes de virar caminho."""

    def test_validate_genre_accepts_known_and_rejects_other(self):
        self.assertEqual(core.validate_genre("Rock"), "Rock")
        for bad in ("", "rock", "../Rock", None):
            with self.assertRaises(core.ToolError):
                core.validate_genre(bad)

    def test_validate_asset_name_rejects_path_traversal(self):
        self.assertEqual(core.validate_asset_name("Artista_Musica_1"), "Artista_Musica_1")
        for bad in ("", "..", "../../x", "a/b", "a" + chr(92) + "b", "nome com espaco"):
            with self.assertRaises(core.ToolError):
                core.validate_asset_name(bad)

    def test_remove_songs_rejects_escaping_selection(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            victim = Path(td) / "arquivo_importante.rsm"
            victim.write_bytes(b"dados")
            with self.assertRaises(core.ToolError):
                core.remove_songs(ws, [{"genre": "Rock",
                                        "asset_name": "../../arquivo_importante"}])
            self.assertTrue(victim.exists(), "nada fora do workspace pode ser apagado")

    def test_resolve_playlist_confines_to_assets(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            target = ws.playlists_root / "atlanta" / "music" / "techno_race_music.play"
            target.parent.mkdir(parents=True)
            target.write_text("num_songs: 0" + chr(10), encoding="utf-8")
            rel = "tune/audio/playlist/city/atlanta/music/techno_race_music.play"
            self.assertEqual(core.resolve_playlist(ws, rel), target.resolve())
            for bad in ("../../../etc/passwd", "..",
                        "tune/audio/playlist/city/atlanta/music"):
                with self.assertRaises(core.ToolError):
                    core.resolve_playlist(ws, bad)


class AtomicRebuild(unittest.TestCase):
    """Antes o dave/hash_build escrevia DIRETO no destino e o open(..., "wb")
    truncava na hora: cancelar deixava um DAT corrompido na raiz."""

    def test_failed_rebuild_keeps_the_previous_dat(self):
        if core.find_dave() is None:
            self.skipTest("dave nao disponivel")
        original = core._run
        core._run = lambda *a, **k: (1, "morto pelo usuario")
        try:
            with tempfile.TemporaryDirectory() as td:
                ws = core.Workspace(Path(td))
                ws.assets_path.mkdir(parents=True)
                good = ws.base_path / "ASSETS.DAT"
                good.write_bytes(b"DAT bom da rodada anterior")
                with self.assertRaises(core.ToolError):
                    core.rebuild_assets_dat(ws)
                self.assertEqual(good.read_bytes(), b"DAT bom da rodada anterior")
                self.assertFalse((ws.base_path / core.REBUILD_SCRATCH).exists(),
                                 "o scratch tem de ser limpo no finally")
        finally:
            core._run = original


class BackupManifestBatching(unittest.TestCase):
    def test_backup_files_writes_manifest_once(self):
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            sources = []
            for i in range(5):
                f = ws.base_path / ("arquivo%d.play" % i)
                f.write_text("num_songs: 0" + chr(10), encoding="utf-8")
                sources.append(f)
            sources.append(ws.base_path / "nunca_existiu.play")
            backup = core.create_backup_session(ws, "lote")

            writes = []
            original = core._write_manifest

            def counting(root, manifest):
                writes.append(1)
                return original(root, manifest)

            core._write_manifest = counting
            try:
                copied = core.backup_files(ws, sources, backup)
            finally:
                core._write_manifest = original

            self.assertEqual(copied, 5)
            self.assertEqual(len(writes), 1, "um backup = uma gravacao de manifesto")
            manifest = _json.loads(
                (backup / "backup_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["files"]), 6)
            self.assertFalse(manifest["files"]["nunca_existiu.play"]["existed"])

    def test_backup_file_still_works_alone(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            f = ws.base_path / "um.play"
            f.write_text("x", encoding="utf-8")
            backup = core.create_backup_session(ws, "unico")
            core.backup_file(ws, f, backup)
            self.assertTrue((backup / "um.play").is_file())


class DestructiveFailureCarriesBackup(unittest.TestCase):
    """Falhar no meio deixa estado PARCIAL; o caminho do backup viaja junto com o
    erro para a tela poder dizer como desfazer."""

    def test_add_failure_attaches_backup_path(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            (ws.streams_path / "Music" / "Rock").mkdir(parents=True)
            src = Path(td) / "Artista - Musica.mp3"
            src.write_bytes(b"x")
            spec = core.build_add_spec(ws, src, "Musica", "Artista", "Rock", "")
            spec["playlist_targets"] = []
            # sem mcstrings02.strtbl no workspace, decode_strings falha logo apos o backup
            with self.assertRaises(core.ToolError) as ctx:
                core.add_songs(ws, [spec])
            backup = getattr(ctx.exception, core.BACKUP_ATTR, None)
            self.assertTrue(backup and Path(backup).is_dir(),
                            "o erro devia carregar o backup, veio %r" % (backup,))

    def test_remove_failure_attaches_backup_path(self):
        with tempfile.TemporaryDirectory() as td:
            ws = core.Workspace(Path(td))
            (ws.streams_path / "Music" / "Techno").mkdir(parents=True)

            def boom(*a, **k):
                raise core.ToolError("strtbl sumiu")

            original = core.decode_strings
            core.decode_strings = boom
            try:
                with self.assertRaises(core.ToolError) as ctx:
                    core.remove_songs(ws, [{"genre": "Techno", "asset_name": "Foo"}],
                                      remove_audio=False, remove_playlists=False,
                                      remove_strings=True)
            finally:
                core.decode_strings = original
            backup = getattr(ctx.exception, core.BACKUP_ATTR, None)
            self.assertTrue(backup and Path(backup).is_dir(),
                            "o erro devia carregar o backup, veio %r" % (backup,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
