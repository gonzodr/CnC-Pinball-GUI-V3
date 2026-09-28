import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import pygame


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from game_modes import (
    GAME_COOP,
    GAME_QUICK,
    GAME_MODE_MASK_ONE_PLAYER,
    step_game_mode,
)
from mock_input import MockInputController
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

    def test_mode_confirmation_protocol(self):
        self.assertEqual(
            parse_line("GAME_MODE_CONFIRM,2"),
            GameEvent("GAME_MODE_CONFIRM", (2,)),
        )
        self.assertIsNone(parse_line("GAME_MODE_CONFIRM,5"))

    def test_score_snapshot_accepts_latched_running_mode(self):
        self.assertEqual(
            parse_line("score,42000,2,2,1,0,0,1"),
            GameEvent("SCORE_UPDATE", (42000, 2, 2, 1, 0, 0, 1)),
        )
        self.assertIsNone(parse_line("score,42000,2,2,1,0,0,99"))


class GameModeStateTests(unittest.TestCase):
    @staticmethod
    def _keydown(key):
        return pygame.event.Event(pygame.KEYDOWN, key=key, mod=0)

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

    def test_coop_and_quick_gameplay_fade_back_to_standard_background(self):
        self.assertEqual(ScoreGUI._gameplay_background_mode(GAME_COOP), 0)
        self.assertEqual(ScoreGUI._gameplay_background_mode(GAME_QUICK), 0)

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
            tuple(ScoreGUI.MODE_ART_Y_OFFSETS.values()),
            (
                ScoreGUI.MODE_ART_Y_STANDARD,
                ScoreGUI.MODE_ART_Y_COOP,
                ScoreGUI.MODE_ART_Y_QUICK,
                ScoreGUI.MODE_ART_Y_MUNCHIES,
                ScoreGUI.MODE_ART_Y_MAYHEM,
            ),
        )
        self.assertTrue(
            all(isinstance(y, int) for y in ScoreGUI.MODE_ART_Y_OFFSETS.values())
        )

    def test_confirmed_mode_art_has_light_wiggle_and_fades_out(self):
        self.assertEqual(
            ScoreGUI.MODE_CONFIRM_DURATION_SEC,
            StateMachine.MODE_CONFIRM_DURATION_SEC,
        )
        gui = ScoreGUI.__new__(ScoreGUI)
        gui.SCREEN_W = 20
        gui.SCREEN_H = 20
        gui.screen = pygame.Surface((20, 20), pygame.SRCALPHA)
        gui.mode_art = {0: pygame.Surface((20, 20), pygame.SRCALPHA)}
        gui._mode_background_previous_id = None
        gui.mode_art[0].fill((0, 0, 0, 0))
        pygame.draw.rect(gui.mode_art[0], (255, 0, 0, 255), (5, 5, 10, 10))

        gui._draw_mode_art(0, 0.0)
        self.assertEqual(gui.screen.get_at((10, 10)).a, 255)

        gui.screen.fill((0, 0, 0, 0))
        gui._draw_mode_art(0, ScoreGUI.MODE_CONFIRM_WIGGLE_SEC / 8.0)
        self.assertEqual(gui.screen.get_at((0, 10)).a, 0)
        self.assertEqual(gui.screen.get_at((10, 10)).a, 255)

        gui.screen.fill((0, 0, 0, 0))
        gui._draw_mode_art(0, 1.3)
        self.assertEqual(gui.screen.get_at((10, 10)).a, 0)

        # Ujranyitaskor a PNG atlatszo resze tovabbra is atlatszo maradjon;
        # a regi set_alpha(None) itt fekete, atlatszatlan teglalapot okozott.
        gui.screen.fill((0, 0, 0, 0))
        gui._draw_mode_art(0)
        self.assertEqual(gui.screen.get_at((0, 0)).a, 0)
        self.assertEqual(gui.screen.get_at((10, 10))[:3], (255, 0, 0))

    def test_mode_navigation_is_locked_during_confirmation(self):
        state = StateMachine()
        state.state = AppState.PLAYER_SELECT
        state.selected_game_mode = GAME_QUICK
        state.handle_event(GameEvent("GAME_MODE_CONFIRM", (GAME_QUICK,)))
        state.handle_event(GameEvent("FLIPPER_RIGHT"))
        self.assertEqual(state.selected_game_mode, GAME_QUICK)

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
        class FakeModeAudio:
            def __init__(self):
                self.played = []
                self.navigated = []

            def start_selector(self):
                pass

            def stop_selector(self):
                pass

            def navigate(self, direction):
                self.navigated.append(direction)

            def play(self, mode_id):
                self.played.append(mode_id)

        state = StateMachine()
        state._mock_mode_audio = FakeModeAudio()
        state.state = AppState.PLAYER_SELECT

        state.handle_event(GameEvent("FLIPPER_RIGHT"))
        self.assertEqual(state.selected_game_mode, 2)
        self.assertEqual(state._mock_mode_audio.navigated, [1])

        state.handle_event(GameEvent("SCORE_UPDATE", (0, 2, 1, 1, 0, 0)))
        state.handle_event(GameEvent("FLIPPER_LEFT"))
        self.assertEqual(state.selected_game_mode, 1)
        self.assertEqual(state._mock_mode_audio.navigated, [1, -1])

        state.handle_event(GameEvent("START"))
        self.assertEqual(state.state, AppState.PLAYER_SELECT)
        self.assertTrue(state.mode_confirm_active)
        self.assertEqual(state._mock_mode_audio.played, [GAME_COOP])

        with patch(
            "state_machine.time.monotonic",
            return_value=state.mode_confirm_started_at + state.MODE_CONFIRM_DURATION_SEC,
        ):
            state.tick()
        self.assertEqual(state.state, AppState.SCORE)
        self.assertEqual(state.running_game_mode, 1)
        self.assertEqual(state.active_player_count, 2)

    def test_pc_mock_coop_score_survives_player_round_trip(self):
        mock = MockInputController()
        mock._num_players = 2
        mock.set_game_mode(GAME_COOP)

        with patch("mock_input.random.random", return_value=0.0):
            p1_score = mock.poll_events([self._keydown(pygame.K_w)])[-1]
            p2_start = mock.poll_events([self._keydown(pygame.K_b)])[-1]
            p2_score = mock.poll_events([self._keydown(pygame.K_w)])[-1]
            p1_return = mock.poll_events([self._keydown(pygame.K_b)])[-1]

        self.assertEqual((p1_score.args[0], p1_score.args[2]), (1500, 1))
        self.assertEqual((p2_start.args[0], p2_start.args[2]), (1750, 2))
        self.assertEqual((p2_score.args[0], p2_score.args[2]), (3250, 2))
        self.assertEqual((p1_return.args[0], p1_return.args[2]), (3500, 1))
        self.assertTrue(all(event.args[6] == GAME_COOP for event in (
            p1_score, p2_start, p2_score, p1_return
        )))

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

    def test_coop_mirrors_team_score_and_progress_to_every_player(self):
        state = StateMachine()
        state.handle_event(GameEvent("GAME_START", (GAME_COOP, 3)))

        state.handle_event(GameEvent("SCORE_UPDATE", (12345, 3, 2, 1, 50, 1)))
        self.assertEqual(
            {p: state.players[p] for p in range(1, 4)},
            {1: 12345, 2: 12345, 3: 12345},
        )

        state.handle_event(GameEvent("PARTY_STATE", (2, 3, 2, 3, True)))
        for player in range(1, 4):
            self.assertEqual(
                state.party_progress[player],
                {"beers": 3, "joints": 2, "ufo_tier": 3, "weed_ready": True},
            )

    def test_score_snapshot_repairs_running_mode_before_player_switch(self):
        state = StateMachine()
        state.state = AppState.SCORE

        state.handle_event(
            GameEvent("SCORE_UPDATE", (12000, 2, 1, 1, 0, 0, GAME_COOP))
        )
        state.handle_event(
            GameEvent("SCORE_UPDATE", (17000, 2, 2, 1, 0, 0, GAME_COOP))
        )

        self.assertEqual(state.running_game_mode, GAME_COOP)
        self.assertIs(state.highscore_manager, state.team_score_manager)
        self.assertEqual(state.players[1], 17000)
        self.assertEqual(state.players[2], 17000)

    def test_coop_uses_separate_team_highscores_and_eight_character_name(self):
        state = StateMachine()
        state.handle_event(GameEvent("GAME_START", (GAME_COOP, 2)))
        state.handle_event(GameEvent("SCORE_UPDATE", (50000, 2, 2, 3, 0, 0)))
        state.team_score_manager.is_highscore = lambda score: True

        state.handle_event(GameEvent("GAMEOVER"))
        self.assertIs(state.highscore_manager, state.team_score_manager)
        self.assertEqual(state.highscore_title, "TEAM HIGH SCORES")
        self.assertEqual(state._pending_highscore_check, 50000)

        state._resolve_after_summary()
        self.assertEqual(state.state, AppState.NAME_ENTRY)
        self.assertEqual(len(state.name_entry.get_chars()), 8)
        self.assertIn(" ", state.name_entry.alphabet)
        self.assertEqual(state.name_entry_title, "TEAM NAME")

    def test_quick_uses_its_own_highscore_table(self):
        state = StateMachine()
        state.handle_event(GameEvent("GAME_START", (GAME_QUICK, 2)))

        self.assertIs(state.highscore_manager, state.quick_score_manager)
        self.assertEqual(state.highscore_title, "QUICK GAME HIGH SCORES")
        self.assertNotEqual(
            state.quick_score_manager.file_path,
            state.score_manager.file_path,
        )

        state.handle_event(
            GameEvent("SCORE_UPDATE", (40000, 2, 1, 3, 0, 0, GAME_QUICK))
        )
        state.handle_event(
            GameEvent("SCORE_UPDATE", (55000, 2, 2, 3, 0, 0, GAME_QUICK))
        )
        state.handle_event(GameEvent("GAMEOVER"))

        self.assertIs(state.highscore_manager, state.quick_score_manager)
        self.assertEqual(state._pending_highscore_check, 55000)
        self.assertEqual(state.pending_highscore_player, 2)

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
