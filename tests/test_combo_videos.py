"""Regression tests for firmware video triggers and nested sequences."""

import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from png_video_player import PngSequencePlayer
from protocol import GameEvent, parse_line
from video_catalog import resolve_serial_video_name


class VideoCatalogTests(unittest.TestCase):
    def test_tilt_warning_triggers_resolve_to_separate_clips(self):
        available = ("Danger", "Danger2", "Tilt")
        for trigger in available:
            with self.subTest(trigger=trigger):
                self.assertEqual(
                    parse_line(trigger),
                    GameEvent("VIDEO", (trigger,)),
                )
                self.assertEqual(
                    resolve_serial_video_name(trigger, available),
                    trigger,
                )

    def test_extra_ball_lit_and_collect_triggers(self):
        self.assertEqual(
            parse_line("Ufo8"),
            GameEvent("VIDEO", ("Ufo8",)),
        )
        self.assertEqual(
            resolve_serial_video_name("Ufo8", ("Ufo8",)),
            "Ufo8",
        )
        self.assertEqual(
            parse_line("ExtraB"),
            GameEvent("VIDEO", ("ExtraB",)),
        )

    def test_firmware_combo_lines_are_video_events(self):
        self.assertEqual(
            parse_line("ComboCheech4"),
            GameEvent("VIDEO", ("ComboCheech4",)),
        )
        self.assertEqual(
            parse_line("ComboChong2"),
            GameEvent("VIDEO", ("ComboChong2",)),
        )

    def test_character_specific_triggers_resolve_to_nested_sequences(self):
        available = (
            "Combo/Combo_Cheech/Combo_2500",
            "Combo/Combo_Cheech/Combo_20000",
            "Combo/Combo_Chong/Combo_2500",
            "Combo/Combo_Chong/Combo_20000",
        )
        self.assertEqual(
            resolve_serial_video_name("ComboCheech1", available),
            "Combo/Combo_Cheech/Combo_2500",
        )
        self.assertEqual(
            resolve_serial_video_name("ComboCheech6", available),
            "Combo/Combo_Cheech/Combo_20000",
        )
        self.assertEqual(
            resolve_serial_video_name("ComboChong1", available),
            "Combo/Combo_Chong/Combo_2500",
        )
        self.assertEqual(
            resolve_serial_video_name("ComboChong6", available),
            "Combo/Combo_Chong/Combo_20000",
        )

    def test_player_indexes_top_level_and_nested_sequence_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            top_level = root / "Danger"
            nested = root / "Combo" / "Combo_Chong" / "Combo_2500"
            top_level.mkdir()
            nested.mkdir(parents=True)
            (top_level / "Danger_00001.png").touch()
            (nested / "Combo_2500_00002.png").touch()
            (nested / "Combo_2500_00001.png").touch()

            player = PngSequencePlayer.__new__(PngSequencePlayer)
            player.root = root
            clips = player._index_clips()

            self.assertEqual(
                tuple(clips),
                ("Combo/Combo_Chong/Combo_2500", "Danger"),
            )
            self.assertEqual(
                [path.name for path in clips["Combo/Combo_Chong/Combo_2500"]],
                ["Combo_2500_00001.png", "Combo_2500_00002.png"],
            )

    def test_tilt_loop_info_controls_fps_intro_and_loop_range(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tilt = root / "Tilt"
            tilt.mkdir()
            for index in range(180):
                (tilt / f"Tilt_{index:05}.png").touch()
            (tilt / "LOOP_INFO.txt").write_text(
                "Frame rate: 30 fps\n"
                "LOOP START (zero-based frame index): 60\n"
                "LOOP END (inclusive, zero-based): 179\n",
                encoding="utf-8",
            )

            player = PngSequencePlayer.__new__(PngSequencePlayer)
            player.root = root
            player.clips = player._index_clips()
            info = player.playback_info["Tilt"]
            self.assertEqual((info.fps, info.loop_start, info.loop_end), (30.0, 60, 179))

            # Minimal runtime state: update() must play the intro once, then
            # wrap the 96-frame loop without ever setting finished.
            player._consume_ready = lambda: None
            player._request_window = lambda: None
            player._active_name = "Tilt"
            player._started_at = 0.0
            player._current_index = 0
            player._last_requested_index = -1
            player._loop_has_wrapped = False
            player.active = True
            player.finished = False
            player.update(now=2.0)
            self.assertEqual(player._current_index, 60)
            player.update(now=6.0)
            self.assertEqual(player._current_index, 60)
            self.assertTrue(player.active)
            self.assertFalse(player.finished)


if __name__ == "__main__":
    unittest.main()
