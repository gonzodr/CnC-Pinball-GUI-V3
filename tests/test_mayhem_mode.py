import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from game_modes import GAME_MULTIBALL_MAYHEM
from protocol import GameEvent, parse_line
from state_machine import AppState, StateMachine


class MayhemProtocolTests(unittest.TestCase):
    def test_valid_messages(self):
        self.assertEqual(parse_line("MAYHEM_READY,2,5"), GameEvent("MAYHEM_READY", (2, 5)))
        self.assertEqual(
            parse_line("MAYHEM_STAGE,2,3,29,5"),
            GameEvent("MAYHEM_STAGE", (2, 3, 29, 5)),
        )
        self.assertEqual(
            parse_line("MAYHEM_PROGRESS,3,6,5,1"),
            GameEvent("MAYHEM_PROGRESS", (3, 6, 5, True)),
        )
        self.assertEqual(
            parse_line("MAYHEM_STAGE_END,3,8,3"),
            GameEvent("MAYHEM_STAGE_END", (3, 8, 3)),
        )
        self.assertEqual(parse_line("MAYHEM_RESULT,2,123456"), GameEvent("MAYHEM_RESULT", (2, 123456)))
        self.assertEqual(parse_line("MAYHEM_FINISH,2"), GameEvent("MAYHEM_FINISH", (2,)))

    def test_invalid_ranges_are_rejected(self):
        for line in (
            "MAYHEM_READY,0,5", "MAYHEM_READY,1,6",
            "MAYHEM_STAGE,1,5,25,3", "MAYHEM_PROGRESS,1,2,3,2",
            "MAYHEM_STAGE_END,2,5,11", "MAYHEM_FINISH,5",
        ):
            self.assertIsNone(parse_line(line), line)


class MayhemStateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        patches = [
            patch("score_manager.ScoreManager.FILE_PATH", os.path.join(self.tempdir.name, "normal.json")),
            patch("score_manager.ScoreManager.TEAM_FILE_PATH", os.path.join(self.tempdir.name, "team.json")),
            patch("score_manager.ScoreManager.QUICK_FILE_PATH", os.path.join(self.tempdir.name, "quick.json")),
            patch("score_manager.ScoreManager.MAYHEM_FILE_PATH", os.path.join(self.tempdir.name, "mayhem.json")),
        ]
        self.patchers = patches
        for item in patches:
            item.start()
        self.state = StateMachine()

    def tearDown(self):
        for item in reversed(self.patchers):
            item.stop()
        self.tempdir.cleanup()

    def test_full_run_uses_winner_only_and_separate_leaderboard(self):
        self.state.handle_event(GameEvent("GAME_START", (GAME_MULTIBALL_MAYHEM, 2)))
        self.assertIs(self.state.highscore_manager, self.state.mayhem_score_manager)
        self.state.handle_event(GameEvent("MAYHEM_READY", (1, 5)))
        self.state.handle_event(GameEvent("MAYHEM_STAGE", (1, 1, 25, 3)))
        self.state.handle_event(GameEvent("MAYHEM_PROGRESS", (1, 3, 3, True)))
        self.assertTrue(self.state.mayhem_super_lit)
        self.state.handle_event(GameEvent("MAYHEM_RESULT", (1, 10000)))
        self.state.handle_event(GameEvent("MAYHEM_RESULT", (2, 20000)))
        self.state.handle_event(GameEvent("MAYHEM_FINISH", (2,)))
        self.assertEqual(self.state.state, AppState.FINAL_SCORES)
        self.assertEqual(self.state.final_scores_title, "MULTIBALL MAYHEM RESULTS")
        self.assertEqual(self.state._pending_highscore_check, 20000)
        self.assertEqual(self.state.pending_highscore_player, 2)


if __name__ == "__main__":
    unittest.main()
