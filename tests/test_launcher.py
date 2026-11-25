import hashlib
import json
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import requests

from ely_account import ElyAccountMenu, SignInError
from game_files import GameFiles, download_verified
from horizon_mods import HorizonMods
from launcher_log import BuildLog


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = SimpleNamespace(page=SimpleNamespace(overlay=[], update=Mock()), events=Queue(),
                                   busy=False, selected_profile="OfflinePlayer",
                                   offline_profiles_file=self.root / "profiles.json",
                                   _activate_ely_profile=Mock(), _render_avatar=Mock(),
                                   _start_task=lambda task, *args: task())

    def test_catalog_pagination_cache_and_refresh(self):
        mods = HorizonMods(self.app, self.root)
        mods.index_path.parent.mkdir()
        projects = [{"id": f"native{i}", "kind": "native-mod"} for i in range(45)]
        projects += [{"id": "lamp", "kind": "mod"}]
        index = json.dumps({"projects": projects})
        mods.index_path.write_text(index, encoding="utf-8")

        def command(*args, cached_index=None, search_home=None):
            if cached_index is None:
                raise RuntimeError("offline")
            self.assertNotEqual(search_home, mods.game)
            candidates = json.loads(cached_index.read_bytes())["projects"]
            return json.dumps([{"id": p["id"]} for p in candidates[:20]])

        mods.command = Mock(side_effect=command)
        mods.show = Mock()
        mods.search()
        self.assertEqual(mods.search_results["mod", ""][0][0]["id"], "lamp")
        mods.kind = "native"
        mods.search()
        for expected in (20, 40):
            rows, more = mods.search_results["native", ""]
            self.assertTrue(more)
            self.assertEqual(len(rows), expected)
            mods.search(more=True)
        rows, more = mods.search_results["native", ""]
        self.assertFalse(more)
        self.assertEqual(len({r["id"] for r in rows}), 45)
        self.assertTrue(all(r["cached"] for r in rows))
        self.assertEqual(mods.index_path.read_text(encoding="utf-8"), index)
        mods.command.reset_mock()
        for category in ("mod", "native", "mod"):
            mods.kind = category
            mods.search()
        mods.command.assert_not_called()
        mods.search(refresh=True)
        self.assertEqual(mods.command.call_count, 2)
        self.assertNotIn(("native", ""), mods.search_results)

    def test_hidden_log_renders_only_new_visible_lines(self):
        log = BuildLog("mono", "gray", "green", "red")
        for i in range(2010):
            log.append(str(i))
        self.assertFalse(log.view.controls)
        log.render()
        retained = log.view.controls[1]
        log.append("client stopped", "error")
        log.render()
        self.assertIs(log.view.controls[0], retained)
        self.assertEqual(log.view.controls[-1].color, "red")
        self.assertEqual(log.text().splitlines(), [control.value for control in log.view.controls])
        log.clear()
        self.assertEqual(log.text(), "")
        self.assertFalse(log.view.controls)

    def test_account_restore_keeps_play_and_selected_profile_available(self):
        menu = ElyAccountMenu(self.app, self.root)
        self.app._start_task = Mock(side_effect=AssertionError("restore used the foreground worker"))
        entered, resume = Event(), Event()
        session = {"accessToken": "test", "clientToken": "test-client"}
        def validate(_):
            entered.set()
            self.assertTrue(resume.wait(5))
            return "ElyPlayer", session
        texture = self.root / "skin.png"
        texture.touch()
        with patch("ely_account.ely_session.read", return_value={**session, "name": "ElyPlayer"}), \
             patch("ely_account.restore_account", side_effect=validate), \
             patch("ely_account.time", return_value=texture.stat().st_mtime + 60), \
             patch("ely_account.cached_skin", return_value=texture), \
             patch("ely_account.ely_session.save") as save, patch("ely_account.download_skin") as download:
            menu.restore()
            self.assertTrue(entered.wait(5))
            self.assertFalse(self.app.busy)
            self.assertTrue(menu.controls.disabled)
            resume.set()
            while menu.checking:
                event, action = self.app.events.get(timeout=5)
                if event == "ui":
                    action()
            save.assert_not_called()
            download.assert_not_called()
        self.assertEqual(menu.session, session)
        self.assertFalse(menu.controls.disabled)
        self.app._activate_ely_profile.assert_not_called()

    def test_offline_account_retries_and_expired_account_can_sign_in(self):
        menu = ElyAccountMenu(self.app, self.root)
        menu.ui = lambda fn: fn()
        with patch("ely_account.ely_session.read", return_value={
                "accessToken": "test", "clientToken": "test-client", "name": "ElyPlayer"}), \
             patch("ely_account.restore_account", side_effect=[
                 requests.ConnectionError(), SignInError("expired")]):
            menu.restore_saved()
            self.assertTrue(menu.reconnect)
            self.assertTrue(menu.sign_out.visible)
            menu.restore_saved()
            self.assertFalse(menu.reconnect)
            self.assertFalse(menu.sign_out.visible)
            self.assertIn("Sign in", menu.action.text)

    def test_interrupted_or_corrupt_download_preserves_existing_jar(self):
        target = self.root / "server.jar"
        target.write_bytes(b"original")
        def interrupted():
            yield b"partial"
            raise requests.ConnectionError()
        response = MagicMock()
        response.__enter__.return_value = response
        with patch("game_files.requests.get", return_value=response):
            expected = hashlib.sha256(b"new jar").hexdigest()
            for chunks, error in ((interrupted(), requests.ConnectionError), ([b"bad"], ValueError)):
                response.iter_content.return_value = chunks
                with self.assertRaises(error):
                    download_verified("url", target, "sha256", expected)
                self.assertEqual(target.read_bytes(), b"original")
                self.assertEqual(list(self.root.iterdir()), [target])
            response.iter_content.return_value = [b"new ", b"jar"]
            download_verified("url", target, "sha256", expected)
            self.assertEqual(target.read_bytes(), b"new jar")

    def test_preparation_reuses_pinned_files_and_java(self):
        def sha(data):
            return hashlib.sha1(data).hexdigest()
        version = "test"
        manifest = json.dumps({"javaVersion": {"majorVersion": 25, "component": "test-runtime"},
                               "downloads": {"server": {"sha1": sha(b"server"), "url": "fixture"}}}).encode()
        lock = {"minecraft": {"version": version, "manifest_sha1": sha(manifest), "java_major": 25,
                              "client_sha1": sha(b"client"), "server_sha1": sha(b"server")}}
        files = GameFiles(self.root, lock, Queue())
        mc = self.root / "minecraft"
        versions = mc / "versions" / version
        versions.mkdir(parents=True)
        (versions / "test.json").write_bytes(manifest)
        (versions / "test.jar").write_bytes(b"client")
        server = self.root / ".local/minecraft/test/jars/server.jar"
        server.parent.mkdir(parents=True)
        server.write_bytes(b"server")
        runtime = mc / "runtime/test-runtime/platform"
        java = runtime / "test-runtime/bin/java"
        java.parent.mkdir(parents=True)
        java.touch()
        (runtime / ".version").touch()
        with patch("game_files.download_verified") as download, \
             patch("minecraft_launcher_lib.install.install_minecraft_version") as install, \
             patch("minecraft_launcher_lib.runtime.get_executable_path", return_value=str(java)), \
             patch("minecraft_launcher_lib.runtime.install_jvm_runtime") as jvm:
            self.assertEqual(files.ensure_minecraft(mc), server)
            files.ensure_java(mc)
            download.assert_not_called()
            install.assert_not_called()
            jvm.assert_not_called()
            server.write_bytes(b"broken")
            files.ensure_minecraft(mc)
            download.assert_called_once_with("fixture", server, "sha1", sha(b"server"))
            (runtime / ".version").unlink()
            files.ensure_java(mc)
            self.assertEqual(jvm.call_args.args, ("test-runtime", mc))
