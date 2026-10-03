import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pygame
import raspi_config
from score_gui import ScoreGUI
from service_menu import ServiceMenuController


class RaspiConfigTests(unittest.TestCase):
    def test_quit_requires_alt_keydown(self):
        gui = object.__new__(ScoreGUI)
        for mod in (pygame.KMOD_LALT, pygame.KMOD_RALT):
            self.assertTrue(gui.has_quit_key_event([
                pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q, mod=mod),
            ]))
        for event in (
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q),
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q, mod=pygame.KMOD_CTRL),
            pygame.event.Event(pygame.KEYUP, key=pygame.K_q, mod=pygame.KMOD_ALT),
        ):
            self.assertFalse(gui.has_quit_key_event([event]))

    def test_f5_launches_configuration(self):
        menu = ServiceMenuController(None, None, [])
        menu.handle_fkey(pygame.K_F5)
        self.assertTrue(menu.should_launch_raspi_config)
        self.assertEqual(menu.screen, "main")
        menu.reset()
        self.assertFalse(menu.should_launch_raspi_config)

    def test_non_pi_does_not_release_display(self):
        gui, menu = Mock(), SimpleNamespace(status_message="")
        with patch.object(raspi_config.sys, "platform", "win32"):
            raspi_config.run_raspi_config(gui, menu)
        gui.release_display.assert_not_called()
        self.assertIn("Raspberry Pi", menu.status_message)

    @patch("raspi_config.pygame.event.clear")
    @patch("raspi_config.os.access", return_value=True)
    @patch("raspi_config.os.path.isfile", return_value=True)
    @patch("raspi_config.sys.platform", "linux")
    def test_display_restored_on_success_and_failure(self, _file, _access, clear):
        for result in (
            SimpleNamespace(returncode=0, stderr=""),
            SimpleNamespace(returncode=1, stderr="sudo error"),
            OSError("failed"),
        ):
            gui, menu = Mock(), SimpleNamespace(status_message="")
            kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with patch("raspi_config.subprocess.run", **kwargs) as run:
                raspi_config.run_raspi_config(gui, menu)
            run.assert_called_once_with(
                ["sudo", "-n", raspi_config.HELPER],
                stderr=raspi_config.subprocess.PIPE, text=True,
            )
            gui.release_display.assert_called_once()
            gui.acquire_display.assert_called_once()
            gui.cancel_fade_transition.assert_called_once()
        self.assertEqual(clear.call_count, 3)


if __name__ == "__main__":
    unittest.main()
