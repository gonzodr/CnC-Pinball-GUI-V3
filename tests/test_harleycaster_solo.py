"""Harleycaster chart, gameplay and state-machine integration tests."""

import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import wave

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pygame

from harleycaster_solo import HarleycasterSoloGame, load_chart
from mock_input import MockInputController
from state_machine import AppState, StateMachine


def make_song(directory, notes):
    directory = Path(directory)
    audio = directory / "song.wav"
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(8000)
        target.writeframes(b"\0\0" * 24_000)
    chart = directory / "song.chart.json"
    chart.write_text(json.dumps({
        "format_version": 1,
        "title": "Test Solo",
        "audio": audio.name,
        "notes": notes,
    }), encoding="utf-8")
    return chart


class HarleycasterSoloTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((640, 480))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_editor_chart_resolves_audio_and_wav_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [
                {"time_ms": 1000, "lane": 2, "duration_ms": 250},
            ])
            _data, notes, audio, duration = load_chart(chart)
            self.assertEqual(notes[0].at, 1.0)
            self.assertEqual(notes[0].duration, .25)
            self.assertEqual(audio.name, "song.wav")
            self.assertAlmostEqual(duration, 3.0)

    def test_hit_scores_and_normal_miss_budget_unplugs_amp(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [
                {"time_ms": 1000, "lane": 0, "duration_ms": 0},
                {"time_ms": 2000, "lane": 1, "duration_ms": 0},
            ])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game.press(0, 1.0)
            self.assertEqual(game.hits, 1)
            self.assertEqual(game.score, 1000)
            for _ in range(50):
                game._miss()
            self.assertEqual(game.outcome, "UNPLUGGED")
            self.assertEqual(game.misses, 50)

    def test_hardware_mask_maps_left_shoot_right_on_rising_edges(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [
                {"time_ms": 1000, "lane": 0},
                {"time_ms": 1125, "lane": 1},
                {"time_ms": 1250, "lane": 2},
            ])
            game = HarleycasterSoloGame(chart, difficulty=3)
            game.song_time = 1.0
            game.set_hardware_input(0x01)
            game.song_time = 1.125
            game.set_hardware_input(0)
            game.set_hardware_input(0x04)
            game.song_time = 1.25
            game.set_hardware_input(0)
            game.set_hardware_input(0x02)
            self.assertEqual(game.hits, 3)

    def test_normal_chart_thins_close_attacks_and_hit_sets_visible_flash(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [
                {"time_ms": 1000, "lane": 0},
                {"time_ms": 1125, "lane": 1},
                {"time_ms": 1250, "lane": 2},
                {"time_ms": 1750, "lane": 1},
            ])
            game = HarleycasterSoloGame(chart, difficulty=0)
            self.assertEqual([(note.at, note.lane) for note in game.notes], [
                (1.0, 0), (1.75, 1),
            ])
            # Normal accepts a deliberately early, party-player press.
            game.song_time = .70
            game.press(0, .70)
            self.assertEqual(game.hit_lane, 0)
            self.assertGreater(game.hit_flash_until, game.song_time)
            self.assertEqual(game.last_judgement, "GOOD!")

    def test_runtime_thinning_keeps_sustain_notes_and_their_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [
                {"time_ms": 1000, "lane": 2},
                {"time_ms": 1125, "lane": 1, "duration_ms": 500},
            ])
            game = HarleycasterSoloGame(chart, difficulty=0)
            sustains = [note for note in game.notes if note.duration > 0]
            self.assertEqual(len(sustains), 1)
            self.assertEqual(sustains[0].at, 1.125)
            self.assertEqual(sustains[0].duration, .5)

    def test_normal_ghost_tap_does_not_count_as_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 0}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game.press(2, .25)
            self.assertEqual(game.misses, 0)
            self.assertFalse(game.notes[0].judged)

    def test_successful_note_starts_matching_lane_hit_animation(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 1}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game.press(1, 1.0)
            self.assertEqual(game._hit_animation_elapsed, [-1.0, 0.0, -1.0])
            game._update_scene_animation(.1)
            self.assertAlmostEqual(game._hit_animation_elapsed[1], .1)
            game._update_scene_animation(1.0)
            self.assertAlmostEqual(
                game._hit_animation_elapsed[1],
                len(game.art["hit_feedback"][1]) / 30.0,
            )

    def test_long_note_scores_only_after_button_is_held_to_the_end(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [
                {"time_ms": 1000, "lane": 0, "duration_ms": 500},
            ])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game._held_lanes[0] = True
            game.press(0, 1.0)
            self.assertTrue(game.notes[0].holding)
            self.assertFalse(game.notes[0].judged)
            self.assertEqual(game.hits, 0)
            game._audio_started = True
            game.song_time = 1.49
            game.update(0.0)
            self.assertEqual(game.hits, 0)
            game.song_time = 1.5
            game.update(0.0)
            self.assertEqual(game.hits, 1)
            self.assertTrue(game.notes[0].judged)

    def test_releasing_long_note_early_counts_as_one_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [
                {"time_ms": 1000, "lane": 0, "duration_ms": 500},
            ])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game._held_lanes[0] = True
            game.press(0, 1.0)
            game._held_lanes[0] = False
            game._audio_started = True
            game.song_time = 1.2
            game.update(0.0)
            self.assertEqual(game.hits, 0)
            self.assertEqual(game.misses, 1)
            self.assertTrue(game.notes[0].judged)

    def test_ae_anchor_math_preserves_amp_and_fret_hand_pivots(self):
        amp_left, amp_top = HarleycasterSoloGame._ae_top_left(
            (193.5, 240.0), (554.5, 373.0), (44.2, 44.2))
        self.assertAlmostEqual(amp_left, 468.973)
        self.assertAlmostEqual(amp_top, 266.92)
        parent = {
            "anchor": (298.0, 317.272727272727),
            "position": (295.0, 214.0),
            "scale": (100.0, 88.0),
        }
        hand = {
            "position": (407.25, 306.5),
            "scale": (34.5745856353592, 33.4235727440147),
        }
        position, scale = HarleycasterSoloGame._child_transform(parent, hand)
        self.assertAlmostEqual(position[0], 404.25)
        self.assertAlmostEqual(position[1], 204.52)
        self.assertAlmostEqual(scale[0], 34.5745856353592)
        self.assertAlmostEqual(scale[1], 29.4127440147329)

    def test_each_miss_starts_one_non_looping_crawl_step(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 0}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game._miss()
            self.assertTrue(game._crawl_active)
            self.assertEqual(game._crawl_queue, 0)
            for _ in range(24):
                game._update_scene_animation(.1)
            self.assertFalse(game._crawl_active)
            self.assertEqual(game._crawl_frame_index, len(game.art["crawl"]) - 1)
            self.assertAlmostEqual(game._crawl_display_progress, 1 / 50)

    def test_fret_hand_lane_poses_use_purple_as_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 0}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game._set_fret_hand_lane(2)
            self.assertEqual((game._fret_hand_angle, game._fret_hand_y_offset),
                             (0.0, 0.0))
            game._set_fret_hand_lane(1)
            self.assertEqual((game._fret_hand_angle, game._fret_hand_y_offset),
                             (15.0, 15.0))
            game._set_fret_hand_lane(0)
            self.assertEqual((game._fret_hand_angle, game._fret_hand_y_offset),
                             (30.0, 20.0))

    def test_outer_note_paths_converge_and_head_changes_every_three_seconds(self):
        self.assertEqual(HarleycasterSoloGame._x_for_lane(0, 161), 308)
        self.assertEqual(HarleycasterSoloGame._x_for_lane(2, 161), 332)
        self.assertEqual(HarleycasterSoloGame._x_for_lane(0, 339), 209)
        self.assertEqual(HarleycasterSoloGame._x_for_lane(2, 339), 431)
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 0}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            screen = pygame.Surface((640, 480))
            expected = ("head4", "head1", "head2", "head3")
            for visual_time, head in zip((0.0, 3.0, 6.0, 9.0), expected):
                game.visual_time = visual_time
                game._draw_chong(screen)
                self.assertEqual(game.active_head, head)

    def test_complete_chong_comp_sways_with_one_second_easy_ease_legs(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 0}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            expected = (
                (0.0, -5.0),
                (0.25, -3.4375),
                (0.5, 0.0),
                (0.75, 3.4375),
                (1.0, 5.0),
                (1.5, 0.0),
                (2.0, -5.0),
            )
            for visual_time, angle in expected:
                game.visual_time = visual_time
                self.assertAlmostEqual(game._chong_sway_angle(), angle)

    def test_combo_drives_upper_camera_zoom_wiggle_and_red_light(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 0}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game.visual_time = .37
            game._camera_combo_visual = 0.0
            base = game._upper_camera_effect()
            game._camera_combo_visual = 32.0
            maximum = game._upper_camera_effect()
            self.assertEqual(base, (1.0, (0.0, 0.0), 0.0, 0))
            self.assertAlmostEqual(maximum[0], 1.24)
            self.assertGreater(abs(maximum[1][0]) + abs(maximum[1][1]), 0.0)
            self.assertGreater(abs(maximum[2]), 0.0)
            self.assertEqual(maximum[3], 62)

    def test_miss_retracts_upper_camera_zoom_over_one_second(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 0}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game.combo = 32
            game._camera_combo_visual = 32.0
            game._miss()
            self.assertEqual(game.combo, 0)
            self.assertGreater(game._camera_combo_visual, 0.0)
            game._update_scene_animation(.5)
            self.assertAlmostEqual(game._camera_combo_visual, 16.0)
            game._update_scene_animation(.5)
            self.assertAlmostEqual(game._camera_combo_visual, 0.0)

    def test_upper_camera_tint_drifts_hue_while_combo_is_high(self):
        with tempfile.TemporaryDirectory() as directory:
            chart = make_song(directory, [{"time_ms": 1000, "lane": 0}])
            game = HarleycasterSoloGame(chart, difficulty=0)
            game._camera_combo_visual = 32.0
            game.visual_time = 0.0
            first = game._upper_camera_tint()
            game.visual_time = 8.0
            second = game._upper_camera_tint()
            self.assertEqual(first[3], 62)
            self.assertEqual(second[3], 62)
            self.assertNotEqual(first[:3], second[:3])

    def test_u_key_requests_harleycaster_not_munchies(self):
        controller = MockInputController()
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_u, mod=0)
        kinds = [item.kind for item in controller.poll_events([event])]
        self.assertIn("GUITAR_SOLO_START", kinds)
        self.assertNotIn("MUNCHIES_START", kinds)

    def test_state_machine_returns_solo_bonus_to_score(self):
        state = StateMachine()

        class FinishedSolo:
            finished = True
            def update(self, _dt): pass
            def result_dict(self): return {"total_bonus": 1234}
            def prepare_for_replay(self): self.prepared = True

        state.state = AppState.MINIGAME
        state.minigame = FinishedSolo()
        state._active_minigame_kind = "harleycaster_solo"
        state._minigame_last_tick = time.monotonic()
        state.tick()
        self.assertEqual(state.players[state.current_player], 1234)
        self.assertEqual(state.state, AppState.SCORE)
        self.assertIsNone(state.minigame)


if __name__ == "__main__":
    unittest.main()
