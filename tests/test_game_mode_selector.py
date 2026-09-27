import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import pygame


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from game_modes import GAME_MODE_MASK_ONE_PLAYER, step_game_mode
from protocol import GameEvent, parse_line
from score_gui import ScoreGUI
from state_machine import AppState, StateMachine


class GameModeProtocolTests(unittest.TestCase):
    def test_mode_step_skips_coop_for_one_player_and_wraps(self):
        self.assertEqual(step_game_mode(0, GAME_MODE_MASK_ONE_PLAYER, 1), 2)
        self.assertEqual(step_game_mode(0, GAME_MODE_MASK_ONE_PLAYER, -1), 4)

    def test_valid_selector_snapshot(self):
        self.assertEqual(
            parse_line("GAME_MODE,2,29"),
            GameEvent("GAME_MODE_STATE", (2, 29)),
        )

    def test_unknown_or_unavailable_mode_falls_back_to_standard(self):
        self.assertEqual(
            parse_line("GAME_MODE,99,31"),
            GameEvent("GAME_MODE_STATE", (0, 31)),
        )
        self.assertEqual(
            parse_line("GAME_MODE,1,29"),
            GameEvent("GAME_MODE_STATE", (0, 29)),
        )

    def test_start_validates_player_count_and_one_player_coop(self):
        self.assertEqual(
            parse_line("GAME_START,1,1"), GameEvent("GAME_START", (0, 1))
        )
        self.assertEqual(
            parse_line("GAME_START,4,4"), GameEvent("GAME_START", (4, 4))
        )
        self.assertIsNone(parse_line("GAME_START,2,5"))


class GameModeStateTests(unittest.TestCase):
    def test_mode_background_fade_duration_and_endpoints(self):
        self.assertGreaterEqual(ScoreGUI.MODE_BACKGROUND_FADE_SEC, 1.0)
        self.assertLessEqual(ScoreGUI.MODE_BACKGROUND_FADE_SEC, 1.5)

        gui = ScoreGUI.__new__(ScoreGUI)
        gui.screen = pygame.Surface((2, 2))
        gui.mode_backgrounds = {
            0: pygame.Surface((2, 2)),
            1: pygame.Surface((2, 2)),
        }
        gui.mode_backgrounds[0].fill((255, 0, 0))
        gui.mode_backgrounds[1].fill((0, 0, 255))
        gui._mode_background_current_id = 0
        gui._mode_background_previous_id = None
        gui._mode_background_fade_start = 0.0

        with patch("score_gui.time.time", return_value=10.0):
            gui._draw_mode_background(1)
        self.assertEqual(gui.screen.get_at((0, 0))[:3], (255, 0, 0))

        with patch(
            "score_gui.time.time",
            return_value=10.01 + ScoreGUI.MODE_BACKGROUND_FADE_SEC,
        ):
            gui._draw_mode_background(1)
        self.assertEqual(gui.screen.get_at((0, 0))[:3], (0, 0, 255))
        self.assertIsNone(gui._mode_background_previous_id)

    def test_mode_art_slides_with_cubic_ease_out(self):
        gui = ScoreGUI.__new__(ScoreGUI)
        gui.SCREEN_W = 20
        gui.SCREEN_H = 20
        gui.screen = pygame.Surface((20, 20), pygame.SRCALPHA)
        gui.mode_art = {
            0: pygame.Surface((20, 20), pygame.SRCALPHA),
            1: pygame.Surface((20, 20), pygame.SRCALPHA),
        }
        gui.mode_art[0].fill((255, 0, 0, 255))
        gui.mode_art[1].fill((0, 0, 255, 255))
        gui._mode_background_previous_id = 0
        gui._mode_background_fade_start = 10.0
        gui._mode_art_slide_direction = 1

        with patch("score_gui.time.time", return_value=10.0):
            gui._draw_mode_art(1)
        self.assertEqual(gui.screen.get_at((0, 0))[:3], (255, 0, 0))

        gui.screen.fill((0, 0, 0, 0))
        with patch(
            "score_gui.time.time",
            return_value=10.01 + ScoreGUI.MODE_ART_SLIDE_SEC,
        ):
            gui._draw_mode_art(1)
        self.assertEqual(gui.screen.get_at((0, 0))[:3], (0, 0, 255))

    def test_mode_art_is_scaled_down_fifteen_percent(self):
        self.assertEqual(ScoreGUI.MODE_ART_SCALE, 0.85)
        self.assertEqual(
            set(ScoreGUI.MODE_ART_Y_OFFSETS),
            {0, 1, 2, 3, 4},
        )
        self.assertEqual(
            (
                ScoreGUI.MODE_ART_Y_STANDARD,
                ScoreGUI.MODE_ART_Y_COOP,
                ScoreGUI.MODE_ART_Y_QUICK,
                ScoreGUI.MODE_ART_Y_MUNCHIES,
                ScoreGUI.MODE_ART_Y_MAYHEM,
            ),
            (-25, -25, -25, -25, -25),
        )
        self.assertTrue(all(y < 0 for y in ScoreGUI.MODE_ART_Y_OFFSETS.values()))

    def test_all_mode_background_assets_are_exact_display_size(self):
        score_assets = SRC / "assets" / "SCORE"
        for filename in (
            "BACKGROUND.png",
            "BACKGROUND_COOP.png",
            "BACKGROUND_QUICK.png",
            "BACKGROUND_MUNCHIES.png",
            "BACKGROUND_MAYHEM.png",
        ):
            with self.subTest(filename=filename):
                self.assertEqual(
                    pygame.image.load(score_assets / filename).get_size(),
                    (640, 480),
                )

    def test_all_mode_art_assets_are_transparent_and_exact_display_size(self):
        mode_art_dir = SRC / "assets" / "SCORE" / "MODE_ART"
        for filename in (
            "MODE_ART_STANDARD.png",
            "MODE_ART_COOP.png",
            "MODE_ART_QUICK.png",
            "MODE_ART_MUNCHIES.png",
            "MODE_ART_MAYHEM.png",
        ):
            with self.subTest(filename=filename):
                art = pygame.image.load(mode_art_dir / filename)
                self.assertEqual(art.get_size(), (640, 480))
                self.assertTrue(art.get_flags() & pygame.SRCALPHA)
                self.assertEqual(art.get_at((0, 0)).a, 0)

    def test_pc_mock_can_select_players_modes_and_start(self):
        state = StateMachine()
        state.state = AppState.PLAYER_SELECT

        state.handle_event(GameEvent("FLIPPER_RIGHT"))
        self.assertEqual(state.selected_game_mode, 2)

        state.handle_event(GameEvent("SCORE_UPDATE", (0, 2, 1, 1, 0, 0)))
        state.handle_event(GameEvent("FLIPPER_LEFT"))
        self.assertEqual(state.selected_game_mode, 1)

        state.handle_event(GameEvent("START"))
        self.assertEqual(state.state, AppState.SCORE)
        self.assertEqual(state.running_game_mode, 1)
        self.assertEqual(state.active_player_count, 2)

    def test_pc_mock_falls_back_from_coop_when_players_wrap_to_one(self):
        state = StateMachine()
        state.state = AppState.PLAYER_SELECT
        state.handle_event(GameEvent("SCORE_UPDATE", (0, 2, 1, 1, 0, 0)))
        state.selected_game_mode = 1

        state.handle_event(GameEvent("SCORE_UPDATE", (0, 1, 1, 1, 0, 0)))
        self.assertEqual(state.selected_game_mode, 0)
        self.assertEqual(state.game_mode_availability_mask, GAME_MODE_MASK_ONE_PLAYER)

    def test_snapshot_enters_player_select_and_score_updates_do_not_exit_it(self):
        state = StateMachine()
        state.handle_event(GameEvent("GAME_MODE_STATE", (2, 29)))
        self.assertEqual(state.state, AppState.PLAYER_SELECT)
        self.assertEqual(state.selected_game_mode, 2)

        state.handle_event(GameEvent("SCORE_UPDATE", (0, 3, 1, 1, 0, 0)))
        self.assertEqual(state.state, AppState.PLAYER_SELECT)
        self.assertEqual(state.active_player_count, 3)

    def test_game_start_enters_score_with_validated_mode(self):
        state = StateMachine()
        state.game_mode_availability_mask = GAME_MODE_MASK_ONE_PLAYER
        state.handle_event(GameEvent("GAME_START", (1, 1)))
        self.assertEqual(state.state, AppState.SCORE)
        self.assertEqual(state.running_game_mode, 0)
        self.assertEqual(state.active_player_count, 1)

        state.handle_event(GameEvent("GAME_START", (1, 4)))
        self.assertEqual(state.running_game_mode, 1)
        self.assertEqual(state.active_player_count, 4)

    def test_player_select_reuses_score_layout_without_menu_panel(self):
        gui_source = (SRC / "score_gui.py").read_text(encoding="utf-8")
        main_source = (SRC / "main.py").read_text(encoding="utf-8")
        self.assertIn("def render_player_select(self, state):", gui_source)
        self.assertIn("self.render(state)", gui_source)
        self.assertNotIn('"SELECT GAME MODE"', gui_source)
        self.assertNotIn("mode_select_panel", gui_source)
        self.assertIn("gui.render_player_select(state)", main_source)


if __name__ == "__main__":
    unittest.main()
