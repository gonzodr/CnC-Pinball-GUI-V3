"""Amount-based jackpot serial -> catalog -> one-shot playback regressions."""

import sys
import tempfile
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from png_video_player import PngSequencePlayer
from protocol import GameEvent, parse_line
from state_machine import AppState, StateMachine
from video_catalog import resolve_serial_video_name


AMOUNTS = (10000, 15000, 20000, 25000, 30000, 50000, 100000)
CLIPS = tuple(f"JACKPOT_{amount}" for amount in AMOUNTS)


class FakePngPlayer:
    clip_names = CLIPS

    def __init__(self):
        self.started = []
        self.finished = False

    def start(self, name):
        self.started.append(name)
        self.finished = False
        return name in self.clip_names

    def update(self):
        pass


class JackpotVideoTests(unittest.TestCase):
    def test_all_amounts_parse_resolve_play_once_and_return_to_score(self):
        for amount in AMOUNTS:
            with self.subTest(amount=amount):
                trigger = f"Jackpot_{amount}"
                event = parse_line(trigger)
                self.assertEqual(event, GameEvent("VIDEO", (trigger,)))
                player = FakePngPlayer()
                state = StateMachine()
                state.state = AppState.SCORE
                state.png_video_player = player
                state.handle_event(event)
                self.assertEqual(player.started, [f"JACKPOT_{amount}"])
                self.assertEqual(state.state, AppState.PNG_VIDEO)
                player.finished = True
                state.tick()
                self.assertEqual(state.state, AppState.SCORE)
                self.assertEqual(len(player.started), 1)

    def test_exact_amount_not_legacy_mode_selects_the_clip(self):
        for amount in AMOUNTS:
            self.assertEqual(
                resolve_serial_video_name(f"jackpot_{amount}.mp4", CLIPS),
                f"JACKPOT_{amount}",
            )
        # Old mode IDs are ambiguous between the two bridges. Never guess.
        for mode in range(2, 7):
            self.assertIsNone(resolve_serial_video_name(f"Jackpot{mode}", CLIPS))
            self.assertEqual(
                resolve_serial_video_name(f"Jackpot{mode}", (f"Jackpot{mode}",)),
                f"Jackpot{mode}",
            )

    def test_unavailable_amount_does_not_show_a_wrong_award(self):
        for amount in (40000, 60000):
            self.assertIsNone(resolve_serial_video_name(f"Jackpot_{amount}", CLIPS))
        player = FakePngPlayer()
        state = StateMachine()
        state.state = AppState.SCORE
        state.png_video_player = player
        state.handle_event(parse_line("Jackpot_60000"))
        self.assertEqual(player.started, [])
        self.assertEqual(state.state, AppState.SCORE)

    def test_sequence_finishes_after_125_frames_at_30_fps_without_loop(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clip = root / "JACKPOT_10000"
            clip.mkdir()
            for index in range(1, 126):
                (clip / f"JACKPOT_10000_{index:05}.png").touch()
            player = PngSequencePlayer.__new__(PngSequencePlayer)
            player.root = root
            player.clips = player._index_clips()
            self.assertNotIn("JACKPOT_10000", player.playback_info)
            self.assertEqual(player.FPS, 30.0)
            player._consume_ready = lambda: None
            player._request_window = lambda: None
            player._log_stats = lambda *args: None
            player._cancel_generation = lambda: None
            player._cache = {}
            player._active_name = "JACKPOT_10000"
            player._started_at = 0.0
            player._current_index = 0
            player._last_requested_index = -1
            player._loop_has_wrapped = False
            player.active = True
            player.finished = False
            player.update(now=124 / 30)
            self.assertFalse(player.finished)
            player.update(now=126 / 30)
            self.assertTrue(player.finished)
            self.assertFalse(player.active)


if __name__ == "__main__":
    unittest.main()
