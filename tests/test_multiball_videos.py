"""Regression coverage for weed-multiball firmware video triggers."""

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from png_video_player import PngSequencePlayer
from protocol import GameEvent, parse_line
from video_catalog import resolve_serial_video_name


MULTIBALL_CLIPS = {
    "Multiball1": "MICHOAKAN_MULTIBALL_640x480",
    "Multiball2": "ACAPULCO_GOLD_MULTIBALL_640x480",
    "Multiball3": "THAI_STICK_MULTIBALL_640x480",
    "Multiball4": "LABRADOR_MULTIBALL",
}


class MichoakanMultiballVideoTests(unittest.TestCase):
    def test_firmware_multiball_lines_resolve_to_final_sequences(self):
        available = tuple(MULTIBALL_CLIPS.values())
        for trigger, clip in MULTIBALL_CLIPS.items():
            with self.subTest(trigger=trigger):
                self.assertEqual(parse_line(trigger), GameEvent("VIDEO", (trigger,)))
                self.assertEqual(resolve_serial_video_name(trigger, available), clip)

    def test_final_sequences_are_complete_and_indexable(self):
        for clip in MULTIBALL_CLIPS.values():
            with self.subTest(clip=clip):
                sequence = SRC / "assets" / "Videos" / clip
                frames = PngSequencePlayer._collect_frames(sequence)
                self.assertEqual(len(frames), 150)
                self.assertEqual(frames[0].name, f"{clip}_00000.jpg")
                self.assertEqual(frames[-1].name, f"{clip}_00149.jpg")

    def test_firmware_still_emits_multiball1_at_mode_start(self):
        firmware = ROOT.parent / "CnC_firmware4" / "CnC_firmware4.ino"
        source = firmware.read_text(encoding="utf-8")
        self.assertIn('Serial.print("Multiball");', source)
        self.assertIn('Serial.println(lvl + 1); // Multiball1..Multiball4', source)


if __name__ == "__main__":
    unittest.main()
