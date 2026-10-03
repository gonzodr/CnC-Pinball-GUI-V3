"""Quantization and chart persistence contracts."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from guitar_chart_editor import (
    ChartDocument,
    GuitarChartEditor,
    build_activity_envelope,
    candidates_from_guitar_heatmap,
    detect_note_candidates,
    ensure_guitar_stem,
    ensure_playback_stems,
    quantize_time_ms,
)


class GuitarChartEditorTests(unittest.TestCase):
    def test_quantize_uses_bpm_subdivision_and_beat_offset(self):
        # 120 BPM, sixteenth grid = 125 ms.  A 1088 ms take becomes 1125 ms.
        self.assertEqual(quantize_time_ms(1088, 120, 0, 4), 1125)
        self.assertEqual(quantize_time_ms(1188, 120, 63, 4), 1188)

    def test_input_latency_is_removed_before_quantizing(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "song.ogg"
            document = ChartDocument(audio)
            document.input_latency_ms = 40
            note_id = document.add_note(1, 1160)
            self.assertEqual(document.find_note(note_id)["time_ms"], 1125)
            self.assertEqual(document.find_note(note_id)["raw_time_ms"], 1160)

    def test_short_press_is_tap_and_long_press_is_quantized_sustain(self):
        with tempfile.TemporaryDirectory() as directory:
            document = ChartDocument(Path(directory) / "song.ogg")
            tap = document.add_note(0, 1002)
            document.finish_note(tap, 1090)
            self.assertEqual(document.find_note(tap)["duration_ms"], 0)
            sustain = document.add_note(2, 2002)
            document.finish_note(sustain, 2510)
            self.assertEqual(document.find_note(sustain)["duration_ms"], 500)

    def test_save_load_preserves_raw_and_corrected_times(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "solo.ogg"
            document = ChartDocument(audio)
            document.bpm = 100
            document.beat_offset_ms = 37
            note_id = document.add_note(2, 1000)
            document.finish_note(note_id, 1450)
            document.save()
            payload = json.loads(document.chart_path.read_text(encoding="utf-8"))
            self.assertNotIn("_id", payload["notes"][0])
            loaded = ChartDocument(audio)
            self.assertEqual(loaded.notes[0]["raw_time_ms"], 1000)
            self.assertEqual(loaded.notes[0]["time_ms"], document.notes[0]["time_ms"])
            self.assertEqual(loaded.bpm, 100)

    def test_note_edges_can_be_resized_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            document = ChartDocument(Path(directory) / "solo.ogg")
            document.quantize_enabled = False
            note_id = document.add_note(1, 1000)
            document.resize_note_end(note_id, 1600)
            note = document.find_note(note_id)
            self.assertEqual((note["time_ms"], note["duration_ms"]), (1000, 600))

            document.resize_note_start(note_id, 800)
            note = document.find_note(note_id)
            self.assertEqual((note["time_ms"], note["duration_ms"]), (800, 800))
            self.assertEqual(note["raw_time_ms"] + note["raw_duration_ms"], 1600)

    def test_auto_candidates_detect_separated_attacks(self):
        sample_rate = 4000
        samples = [0] * (sample_rate * 2)
        for start in (800, 2400, 5200):
            for index in range(start, start + 180):
                samples[index] = 12_000 if index % 2 else -12_000
        candidates = detect_note_candidates(samples, sample_rate, min_gap_ms=90)
        times = [time_ms for time_ms, _lane in candidates]
        self.assertGreaterEqual(len(times), 2)
        self.assertTrue(any(abs(value - 200) < 60 for value in times))
        self.assertTrue(any(abs(value - 600) < 60 for value in times))

    def test_generated_notes_are_one_undo_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            document = ChartDocument(Path(directory) / "solo.ogg")
            added = document.add_generated_notes(((1000, 0), (1500, 2)))
            self.assertEqual(added, 2)
            self.assertEqual(len(document.notes), 2)
            self.assertTrue(document.undo())
            self.assertEqual(document.notes, [])

    def test_guitar_heatmap_generates_timed_multilane_sequence(self):
        heatmap = [(0.05, 0.05, 0.05) for _index in range(101)]
        heatmap[20] = (0.95, 0.08, 0.06)
        heatmap[50] = (0.08, 0.95, 0.06)
        heatmap[80] = (0.08, 0.06, 0.95)
        candidates = candidates_from_guitar_heatmap(
            heatmap, duration_ms=1000, min_gap_ms=80)
        self.assertEqual([lane for _time, lane in candidates], [0, 1, 2])
        self.assertEqual(
            [time_ms for time_ms, _lane in candidates],
            [200, 500, 800],
        )

    def test_companion_guitar_stem_is_preferred_without_running_ai(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "song.wav"
            companion = Path(directory) / "song.guitar.wav"
            audio.touch()
            companion.touch()
            self.assertEqual(ensure_guitar_stem(audio), companion)

    def test_portable_playback_stem_pair_is_preferred_without_running_ai(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "song.wav"
            guitar = Path(directory) / "song.guitar.wav"
            backing = Path(directory) / "song.no_guitar.wav"
            for path in (audio, guitar, backing):
                path.touch()
            self.assertEqual(
                ensure_playback_stems(audio),
                (guitar.resolve(), backing.resolve()),
            )

    def test_quiet_stem_leakage_cannot_create_notes(self):
        heatmap = [(0.05, 0.05, 0.05) for _index in range(101)]
        heatmap[20] = (0.95, 0.08, 0.06)  # loud guitar attack
        heatmap[50] = (0.08, 0.95, 0.06)  # spectral leak in a guitar gap
        activity = [0.0] * 101
        activity[19:22] = (0.3, 0.9, 0.5)
        activity[49:52] = (0.01, 0.03, 0.01)
        candidates = candidates_from_guitar_heatmap(
            heatmap, 1000, min_gap_ms=80, activity=activity)
        self.assertEqual(candidates, [(200, 0)])

    def test_activity_envelope_preserves_loud_and_quiet_regions(self):
        samples = [0] * 100 + [10_000] * 100
        activity = build_activity_envelope(samples, 10)
        self.assertLess(max(activity[:3]), 0.05)
        self.assertGreater(min(activity[-3:]), 0.9)

    def test_ctrl_s_is_save_not_middle_lane_note(self):
        editor = object.__new__(GuitarChartEditor)
        with tempfile.TemporaryDirectory() as directory:
            document = ChartDocument(Path(directory) / "solo.ogg")
            editor.document = document
            editor.held_notes = {}
            editor.set_status = lambda *args, **kwargs: None
            event = type("KeyEvent", (), {
                "key": __import__("pygame").K_s,
                "mod": __import__("pygame").KMOD_CTRL,
            })()
            editor.handle_editor_keydown(event)
            self.assertTrue(document.chart_path.is_file())
            self.assertEqual(document.notes, [])


if __name__ == "__main__":
    unittest.main()
