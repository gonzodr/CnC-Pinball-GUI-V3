import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from contextlib import ExitStack

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pygame
from gui_update import GuiUpdateWorker
from service_menu import ServiceMenuController
from score_gui import ScoreGUI
import pi_shutdown


class ServiceSystemMenuTests(unittest.TestCase):
    def menu(self):
        return ServiceMenuController(None, None, [])

    def test_f6_opens_updates_instead_of_flashing(self):
        menu = self.menu()
        menu.handle_fkey(pygame.K_F6)
        self.assertEqual(menu.screen, "updates")
        self.assertFalse(menu.should_launch_firmware_update)
        self.assertEqual([i[0] for i in menu.UPDATE_ITEMS],
                         ["gui_update", "firmware_update", "find_arduino"])

    def test_cabinet_navigation_and_arduino_update(self):
        menu = self.menu()
        menu.handle_fkey(pygame.K_F6)
        menu.handle_cabinet_input("SERVICE_RIGHT")
        menu.handle_cabinet_input("SERVICE_BACK")  # Green Enter
        self.assertTrue(menu.should_launch_firmware_update)
        self.assertEqual(menu.screen, "updates")
        menu.handle_cabinet_input("SERVICE_CONFIRM")  # Red Esc
        self.assertEqual(menu.screen, "main")

    def test_search_is_in_updates(self):
        menu = self.menu()
        menu.handle_fkey(pygame.K_F6)
        menu.cursor = 2
        with patch.object(menu, "_handle_find_arduino") as find:
            menu.handle_cabinet_input("SERVICE_BACK")
        find.assert_called_once()

    def test_f12_requires_explicit_yes_and_defaults_to_cancel_every_time(self):
        menu = self.menu()
        menu.handle_fkey(pygame.K_F12)
        self.assertEqual(menu.screen, "shutdown_confirm")
        self.assertFalse(menu.should_shutdown)
        menu.handle_cabinet_input("SERVICE_BACK")
        self.assertEqual(menu.screen, "main")
        self.assertFalse(menu.should_shutdown)
        menu.handle_fkey(pygame.K_F12)
        menu.handle_cabinet_input("SERVICE_RIGHT")
        menu.handle_cabinet_input("SERVICE_BACK")
        self.assertTrue(menu.should_shutdown)
        menu.reset()
        menu.handle_fkey(pygame.K_F12)
        self.assertEqual(menu.shutdown_cursor, 0)
        self.assertFalse(menu.should_shutdown)

    def test_update_start_busy_and_restart_require_confirmations(self):
        menu = self.menu()
        menu.handle_fkey(pygame.K_F6)
        menu.handle_cabinet_input("SERVICE_BACK")
        self.assertEqual(menu.screen, "gui_update")
        self.assertIsNone(menu.gui_update_worker)
        worker = SimpleNamespace(done=False, success=False, start=Mock())
        with patch("service_menu.GuiUpdateWorker", return_value=worker):
            menu.handle_cabinet_input("SERVICE_BACK")
        worker.start.assert_called_once()
        menu.handle_fkey = Mock()
        menu.handle_pygame_events([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F12)])
        menu.handle_fkey.assert_not_called()
        self.assertFalse(menu.should_restart_gui)
        worker.done = True
        worker.success = True
        menu.handle_cabinet_input("SERVICE_BACK")
        self.assertTrue(menu.should_restart_gui)

    def test_new_screens_render_without_hardware(self):
        pygame.font.init()
        gui = object.__new__(ScoreGUI)
        gui.active = True
        gui.screen = pygame.Surface((gui.SCREEN_W, gui.SCREEN_H))
        gui.font_service_title = pygame.font.Font(None, 30)
        gui.font_service_item = pygame.font.Font(None, 24)
        gui.font_service_hint = pygame.font.Font(None, 18)
        menu = self.menu()
        for screen in ("main", "updates", "gui_update", "shutdown_confirm"):
            menu.screen = screen
            gui.render_service_menu(menu)
        menu.screen = "gui_update"
        menu.gui_update_worker = SimpleNamespace(
            get_lines=lambda: ["Frissites..."], success=False, done=False,
        )
        gui.render_service_menu(menu)


class GuiUpdateTests(unittest.TestCase):
    def result(self, code=0, out="", err=""):
        return SimpleNamespace(returncode=code, stdout=out, stderr=err)

    def test_local_code_changes_are_never_stashed_or_overwritten(self):
        worker = GuiUpdateWorker()
        with patch.object(worker, "_git", return_value=self.result(out="src/main.py\n")) as git:
            worker.run()
        git.assert_called_once_with("diff", "HEAD", "--name-only")
        self.assertTrue(worker.done)
        self.assertFalse(worker.success)

    def test_runtime_data_is_allowed_and_pull_is_ff_only(self):
        worker = GuiUpdateWorker()
        with patch.object(worker, "_git", side_effect=[
            self.result(out="src/hiscores.json\nsrc/thanks_names.json\n"),
            self.result(out="Already up to date."),
        ]) as git:
            worker.run()
        self.assertEqual(git.call_args_list[-1].args, ("pull", "--ff-only"))
        self.assertTrue(worker.success)
        self.assertTrue(worker.done)

    def test_network_failure_and_timeout_do_not_request_restart(self):
        for response in (self.result(1, err="network failure"),
                         subprocess.TimeoutExpired("git", 60)):
            worker = GuiUpdateWorker()
            with patch.object(worker, "_git", side_effect=[self.result(), response]):
                worker.run()
            self.assertFalse(worker.success)
            self.assertTrue(worker.done)

    def test_real_local_git_fast_forward(self):
        # Exercise real Git without network or any user repository writes.
        with tempfile.TemporaryDirectory(prefix="cnc-gui-update-test-") as temporary:
            root = Path(temporary)
            origin, client, publisher = root / "origin.git", root / "client", root / "publisher"
            def git(*args, cwd=root):
                return subprocess.run(["git", *map(str, args)], cwd=cwd,
                                      check=True, capture_output=True, text=True).stdout.strip()
            git("init", "--bare", origin)
            git("init", "-b", "main", client)
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "commit", "--allow-empty", "-m", "initial", cwd=client)
            git("remote", "add", "origin", origin, cwd=client)
            git("push", "-u", "origin", "main", cwd=client)
            git("clone", "--branch", "main", origin, publisher)
            git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                "commit", "--allow-empty", "-m", "update", cwd=publisher)
            git("push", cwd=publisher)
            expected = git("rev-parse", "HEAD", cwd=publisher)
            worker = GuiUpdateWorker(client)
            worker.run()
            self.assertTrue(worker.done)
            self.assertTrue(worker.success)
            self.assertEqual(git("rev-parse", "HEAD", cwd=client), expected)


class ShutdownTests(unittest.TestCase):
    def test_systemd_sigterm_exits_main_with_cleanup_not_as_sdl_quit(self):
        import main
        with ExitStack() as stack:
            dependencies = {
                name: stack.enter_context(patch.object(main, name))
                for name in ("SerialReader", "StateMachine", "ScoreGUI",
                             "PngSequencePlayer", "MockInputController")
            }
            def send_stop(signum, handler):
                self.assertEqual(signum, main.signal.SIGTERM)
                handler(signum, None)
            stack.enter_context(patch.object(main.signal, "signal", side_effect=send_stop))
            with self.assertRaises(SystemExit) as result:
                main.main()
            self.assertEqual(result.exception.code, 0)
            dependencies["SerialReader"].return_value.stop.assert_called_once()
            dependencies["ScoreGUI"].return_value.release_display.assert_called_once()
            dependencies["PngSequencePlayer"].return_value.close.assert_called_once()
            dependencies["ScoreGUI"].return_value.poll_pygame_events.assert_not_called()

    def test_non_pi_never_invokes_shutdown(self):
        with patch("pi_shutdown.sys.platform", "win32"), patch("pi_shutdown.subprocess.run") as run:
            self.assertFalse(pi_shutdown.request_shutdown(SimpleNamespace(status_message="")))
        run.assert_not_called()

    @patch("pi_shutdown.sys.platform", "linux")
    @patch("pi_shutdown.os.access", return_value=True)
    def test_fixed_command_and_failed_shutdown_keeps_gui_alive(self, _access):
        for code in (0, 1):
            with patch("pi_shutdown.subprocess.run", return_value=SimpleNamespace(
                returncode=code, stderr="test",
            )) as run:
                self.assertEqual(pi_shutdown.request_shutdown(
                    SimpleNamespace(status_message="")), code == 0)
            run.assert_called_once_with(
                ["sudo", "-n", pi_shutdown.HELPER], capture_output=True, text=True, timeout=10,
            )


if __name__ == "__main__":
    unittest.main()
