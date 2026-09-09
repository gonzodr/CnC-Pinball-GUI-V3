"""Regression coverage for Hurry Up-specific score animations."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from png_video_player import PngSequencePlayer
from video_catalog import resolve_serial_video_name


HURRY_SCORE_CLIPS = {
    "Point5": "HURRY_SCORE_15000_MASTER",
    "Point7": "HURRY_SCORE_25000_MASTER",
    "Point8": "HURRY_SCORE_30000_MASTER",
}


class HurryScoreVideoTests(unittest.TestCase):
    def test_hurry_score_triggers_prefer_mode_specific_sequences(self):
        available = tuple(HURRY_SCORE_CLIPS.values()) + ("15000", "25000", "30000")
        for trigger, clip in HURRY_SCORE_CLIPS.items():
            with self.subTest(trigger=trigger):
                self.assertEqual(resolve_serial_video_name(trigger, available), clip)

    def test_hurry_score_sequences_are_complete(self):
        for clip in HURRY_SCORE_CLIPS.values():
            with self.subTest(clip=clip):
                frames = PngSequencePlayer._collect_frames(SRC / "assets" / "Videos" / clip)
                self.assertEqual(len(frames), 150)


if __name__ == "__main__":
    unittest.main()
