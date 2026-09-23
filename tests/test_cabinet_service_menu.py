"""Cabinet-button service-menu protocol contracts."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pygame

from protocol import parse_line
from service_menu import ServiceMenuController


class CabinetServiceMenuTests(unittest.TestCase):
    def test_protocol_preserves_cabinet_service_events(self):
        for command in (
            "SERVICE_MENU_ENTER", "SERVICE_LEFT", "SERVICE_RIGHT",
            "SERVICE_CONFIRM", "SERVICE_BACK",
        ):
            event = parse_line(command)
            self.assertIsNotNone(event)
            self.assertEqual(event.kind, command)

    def test_left_and_right_navigate_existing_main_menu(self):
        menu = object.__new__(ServiceMenuController)
        menu.screen = "main"
        menu.cursor = 0
        menu.status_message = ""
        menu.should_exit = False

        menu.handle_cabinet_input("SERVICE_RIGHT")
        self.assertEqual(menu.cursor, 1)
        menu.handle_cabinet_input("SERVICE_LEFT")
        self.assertEqual(menu.cursor, 0)

    def test_mapping_is_explicit(self):
        source = ServiceMenuController.handle_cabinet_input.__doc__ or ""
        self.assertIn("Bal/Jobb", source)
        self.assertIn("Zold Shoot = Enter", source)
        self.assertIn("piros Start = Esc", source)

    def test_adjustable_submenu_uses_flippers_horizontally_and_green_for_next_row(self):
        menu = object.__new__(ServiceMenuController)
        menu.screen = "minigame_difficulty"
        captured = []
        menu.handle_pygame_events = lambda events: captured.extend(events)

        menu.handle_cabinet_input("SERVICE_LEFT")
        menu.handle_cabinet_input("SERVICE_RIGHT")
        menu.handle_cabinet_input("SERVICE_BACK")

        self.assertEqual(
            [event.key for event in captured],
            [pygame.K_LEFT, pygame.K_RIGHT, pygame.K_RETURN],
        )
        self.assertTrue(all(getattr(event, "cabinet", False) for event in captured))


if __name__ == "__main__":
    unittest.main()
