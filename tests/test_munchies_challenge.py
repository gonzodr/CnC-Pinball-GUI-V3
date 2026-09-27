import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from game_modes import GAME_MUNCHIES
from protocol import GameEvent, parse_line
from state_machine import AppState, StateMachine

MUNCHIES_SOURCE = (SRC / "munchies_abduction.py").read_text(encoding="utf-8")


class MunchiesChallengeProtocolTests(unittest.TestCase):
    def test_challenge_messages_parse(self):
        self.assertEqual(
            parse_line("MUNCHIES_PLAYER,2"),
            GameEvent("MUNCHIES_PLAYER", (2,)),
        )
        self.assertEqual(
            parse_line("MUNCHIES_RESULT,2,42000"),
            GameEvent("MUNCHIES_RESULT", (2, 42000)),
        )
        self.assertEqual(
            parse_line("MUNCHIES_FINISH,2"),
            GameEvent("MUNCHIES_FINISH", (2,)),
        )

    def test_invalid_challenge_messages_are_rejected(self):
        for line in (
            "MUNCHIES_PLAYER,0", "MUNCHIES_PLAYER,5",
            "MUNCHIES_RESULT,5,1", "MUNCHIES_RESULT,1,-1",
            "MUNCHIES_FINISH,0",
        ):
            self.assertIsNone(parse_line(line), line)


class MunchiesChallengeStateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        names = ("FILE_PATH", "TEAM_FILE_PATH", "QUICK_FILE_PATH", "MAYHEM_FILE_PATH", "MUNCHIES_FILE_PATH")
        self.patchers = [
            patch(
                f"score_manager.ScoreManager.{name}",
                os.path.join(self.tempdir.name, f"{name}.json"),
            )
            for name in names
        ]
        for item in self.patchers:
            item.start()
        self.state = StateMachine()

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.tempdir.cleanup()

    def test_multiplayer_results_check_only_firmware_selected_winner(self):
        self.state.handle_event(GameEvent("GAME_START", (GAME_MUNCHIES, 3)))
        self.assertIs(self.state.highscore_manager, self.state.munchies_score_manager)
        self.state.handle_event(GameEvent("MUNCHIES_PLAYER", (1,)))
        self.assertEqual(self.state.current_player, 1)
        self.state.handle_event(GameEvent("MUNCHIES_RESULT", (1, 10000)))
        self.state.handle_event(GameEvent("MUNCHIES_RESULT", (2, 50000)))
        self.state.handle_event(GameEvent("MUNCHIES_RESULT", (3, 30000)))
        self.state.handle_event(GameEvent("MUNCHIES_FINISH", (2,)))
        self.assertEqual(self.state.state, AppState.FINAL_SCORES)
        self.assertEqual(self.state.final_scores_title, "MUNCHIES RESULTS")
        self.assertEqual(self.state._pending_highscore_check, 50000)
        self.assertEqual(self.state.pending_highscore_player, 2)

    def test_pc_mock_start_launches_preloaded_game_immediately(self):
        class FakeGame:
            finished = False

            def __init__(self):
                self.activated = False
                self.challenge_player = None

            def set_difficulty(self, _difficulty):
                pass

            def activate(self):
                self.activated = True

            def set_challenge_player(self, player_num):
                self.challenge_player = player_num

            def update(self, _dt):
                pass

        game = FakeGame()
        self.state.state = AppState.PLAYER_SELECT
        self.state.active_player_count = 2
        self.state.selected_game_mode = GAME_MUNCHIES
        self.state._preloaded_minigame = game
        self.state.handle_event(GameEvent("START"))
        self.assertTrue(self.state._mock_munchies_challenge)
        self.assertEqual(self.state.state, AppState.MINIGAME)
        self.assertTrue(game.activated)
        self.assertEqual(game.challenge_player, 1)

    def test_player_label_is_part_of_the_minigame_countdown(self):
        self.assertIn("def set_challenge_player(self, player_num):", MUNCHIES_SOURCE)
        self.assertIn("self._countdown_player_label", MUNCHIES_SOURCE)
        self.assertIn('f"PLAYER {self.challenge_player}"', MUNCHIES_SOURCE)


if __name__ == "__main__":
    unittest.main()
