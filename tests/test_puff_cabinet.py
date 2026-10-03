"""Cabinet Puff lifecycle without decoding large assets or touching real scores."""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from game_modes import GAME_ARCADE, ARCADE_PUFF_N_RIFF
from harleycaster_solo import HarleycasterAssetError
from protocol import GameEvent, parse_line
from state_machine import AppState, StateMachine


class Reader:
    def __init__(self):
        self.lines = []
        self.heartbeat = None

    def send_line(self, text):
        self.lines.append(text)
        return True

    def send_raw(self, text):
        return self.send_line(text.strip())

    def start_minigame_heartbeat(self, session):
        self.heartbeat = session

    def stop_minigame_heartbeat(self):
        self.heartbeat = None


class Game:
    def __init__(self):
        self.finished = False
        self.masks = []
        self.dts = []
        self.player = None
        self.prepared = False

    def set_difficulty(self, difficulty):
        pass

    def set_challenge_player(self, player):
        self.player = player

    def activate(self):
        self.finished = False
        self.masks = []

    def update(self, dt):
        self.dts.append(dt)

    def set_hardware_input(self, mask):
        self.masks.append(mask)

    def result_dict(self):
        return {"total_bonus": 42000}

    def prepare_for_replay(self):
        self.prepared = True


class PuffCabinetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name in ("FILE_PATH", "TEAM_FILE_PATH", "QUICK_FILE_PATH",
                     "MAYHEM_FILE_PATH", "MUNCHIES_FILE_PATH", "PUFF_FILE_PATH"):
            p = patch(f"score_manager.ScoreManager.{name}",
                      os.path.join(self.temp.name, name + ".json"))
            p.start()
            self.addCleanup(p.stop)
        self.reader = Reader()
        self.state = StateMachine(serial_reader=self.reader)
        self.state.selected_arcade_game = ARCADE_PUFF_N_RIFF
        self.state.handle_event(GameEvent("GAME_START", (GAME_ARCADE, 2)))
        self.game = Game()
        self.state._preloaded_harleycaster = self.game

    def load(self, sid=7):
        self.state.handle_event(parse_line(f"GUITAR_SOLO_START,{sid}"))
        self.state.mark_puff_loading_presented()
        self.state._puff_loading_started_at -= 2
        self.state.tick()

    def test_parser_supports_session_and_rejects_invalid_ids(self):
        self.assertEqual(parse_line("GUITAR_SOLO_START,65535"),
                         GameEvent("GUITAR_SOLO_START", (65535,)))
        for value in ("0", "65536", "nope", "1,2"):
            self.assertIsNone(parse_line("GUITAR_SOLO_START," + value))
        self.assertEqual(parse_line("GUITAR_SOLO_START"), GameEvent("GUITAR_SOLO_START"))

    def test_loading_heartbeat_and_duplicate_start_do_not_ack_early(self):
        self.state.handle_event(parse_line("GUITAR_SOLO_START,7"))
        self.assertEqual(self.reader.heartbeat, 7)
        self.state.handle_event(parse_line("GUITAR_SOLO_START,7"))
        self.assertEqual(self.state.state, AppState.PUFF_LOADING)
        self.assertNotIn("MG_READY,7", self.reader.lines)
        self.state._minigame_next_heartbeat = 0
        self.state.tick()
        self.assertEqual(self.reader.lines[-1], "MG_ALIVE,7")
        self.load()
        self.assertIn("MG_READY,7", self.reader.lines)
        self.assertEqual(self.state._minigame_session, 7)
        self.assertTrue(all(dt >= 0 for dt in self.game.dts))
        self.state.handle_event(parse_line("GUITAR_SOLO_START,7"))
        self.assertIs(self.state.minigame, self.game)

    def test_button_snapshots_include_release_and_reject_old_packets(self):
        self.load()
        for line in ("MG_INPUT,7,65535,1", "MG_INPUT,7,0,5",
                     "MG_INPUT,7,1,0", "MG_INPUT,7,1,7", "MG_INPUT,6,2,7"):
            self.state.handle_event(parse_line(line))
        self.assertEqual(self.game.masks, [1, 5, 0])

    def test_two_players_complete_with_exact_scores_separate_highscores_and_ack(self):
        self.load()
        self.game.finished = True
        self.state.tick()
        self.assertIn("MG_DONE,7,42000", self.reader.lines)
        self.assertIsNone(self.reader.heartbeat)
        self.assertTrue(self.game.prepared)
        self.state.handle_event(parse_line("GUITAR_SOLO_START,7"))
        self.assertEqual(self.state.state, AppState.SCORE)
        self.assertIsNone(self.state.minigame)
        self.state.handle_event(parse_line("MG_ACK,7"))
        self.assertIsNone(self.state._minigame_pending_done)
        self.state.handle_event(parse_line("MUNCHIES_RESULT,1,42000"))
        self.state.handle_event(parse_line("MUNCHIES_PLAYER,2"))
        self.load(8)
        self.assertEqual(self.game.player, 2)
        self.game.finished = True
        self.state.tick()
        self.state.handle_event(parse_line("MG_ACK,8"))
        self.state.handle_event(parse_line("MUNCHIES_RESULT,2,42000"))
        self.state.handle_event(parse_line("MUNCHIES_FINISH,1"))
        self.assertEqual(self.state.final_scores[1], 42000)
        self.assertEqual(self.state.final_scores[2], 42000)
        self.assertEqual(self.state.state, AppState.FINAL_SCORES)
        self.assertEqual(self.state.final_scores_title, "PUFF 'N' RIFF RESULTS")
        self.assertIs(self.state.highscore_manager, self.state.puff_score_manager)
        self.assertEqual(self.state.pending_highscore_player, 1)

    def test_abort_cleans_loading_and_service_cleans_active_music_session(self):
        self.state.handle_event(parse_line("GUITAR_SOLO_START,7"))
        self.state.handle_event(parse_line("MG_ABORT,6,LINK_TIMEOUT"))
        self.assertEqual(self.state.state, AppState.PUFF_LOADING)
        self.state.handle_event(parse_line("MG_ABORT,7,LINK_TIMEOUT"))
        self.assertEqual(self.state.state, AppState.SCORE)
        self.assertIsNone(self.reader.heartbeat)
        self.load(8)
        self.state.handle_event(GameEvent("SERVICE_MENU_ENTER"))
        self.assertEqual(self.state.state, AppState.SERVICE_MENU)
        self.assertTrue(self.game.prepared)
        self.assertIsNone(self.state._minigame_session)
        self.assertIsNone(self.state._minigame_pending_done)

    def test_asset_error_reports_busy_and_stops_heartbeat(self):
        self.state._preloaded_harleycaster = None
        with patch("state_machine.HarleycasterSoloGame",
                   side_effect=HarleycasterAssetError("test missing stem")):
            self.load()
        self.assertIn("MG_BUSY,7", self.reader.lines)
        self.assertEqual(self.state.state, AppState.SCORE)
        self.assertIsNone(self.reader.heartbeat)


if __name__ == "__main__":
    unittest.main()
