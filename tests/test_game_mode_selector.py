import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from game_modes import GAME_MODE_MASK_ONE_PLAYER
from protocol import GameEvent, parse_line
from state_machine import AppState, StateMachine


class GameModeProtocolTests(unittest.TestCase):
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

    def test_wireframe_renderer_is_wired_into_main_dispatch(self):
        gui_source = (SRC / "score_gui.py").read_text(encoding="utf-8")
        main_source = (SRC / "main.py").read_text(encoding="utf-8")
        self.assertIn("def render_player_select(self, state):", gui_source)
        self.assertIn("gui.render_player_select(state)", main_source)


if __name__ == "__main__":
    unittest.main()
