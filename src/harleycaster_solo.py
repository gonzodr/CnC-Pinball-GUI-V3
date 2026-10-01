"""Harleycaster Solo: three-lane rhythm minigame for the 640x480 GUI.

Charts are produced by ``guitar_chart_editor.py`` and live beside their audio
in ``assets/GuitarHero/Songs``.  The newest valid chart is selected whenever
the minigame starts, so an editor save is visible without restarting the GUI.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import platform
from pathlib import Path
import time
import wave
import colorsys

import pygame


ROOT = Path(__file__).resolve().parent
SONGS_DIR = ROOT / "assets" / "GuitarHero" / "Songs"
ART_DIR = ROOT / "assets" / "GuitarHero" / "GameScene"
LAYOUT_PATH = ART_DIR / "scene_layout.json"
FONT_PATH = ROOT / "assets" / "Modak.ttf"
WIDTH, HEIGHT = 640, 480
SPAWN_Y, HIT_Y = 161, 339
TRAVEL_TIME = 1.8
RESULT_SECONDS = 2.8
UPPER_CAMERA_COMBO_CAP = 32.0
LANE_COLORS = ((82, 224, 76), (255, 86, 69), (189, 86, 250))

# The chart stays identical across difficulty levels.  Timing tolerance and
# Cheech's travel budget change, so even the current single auto-chart is
# playable at several cabinet service-menu settings.
DIFFICULTY = {
    # early/late are intentionally asymmetric. Human reaction anticipates a
    # falling target, and the cabinet/audio path can add tens of ms latency.
    -3: {"perfect": .150, "early": .480, "late": .380, "misses": 180,
         "gap": 1.00, "chord": 1, "repeat": 1, "ghost": True},
    -2: {"perfect": .140, "early": .430, "late": .350, "misses": 120,
         "gap": .90, "chord": 1, "repeat": 1, "ghost": True},
    -1: {"perfect": .125, "early": .380, "late": .310, "misses": 80,
         "gap": .80, "chord": 1, "repeat": 2, "ghost": True},
     0: {"perfect": .110, "early": .330, "late": .270, "misses": 50,
         "gap": .70, "chord": 1, "repeat": 2, "ghost": True},
     1: {"perfect": .090, "early": .260, "late": .210, "misses": 32,
         "gap": .50, "chord": 1, "repeat": 3, "ghost": False},
     2: {"perfect": .070, "early": .190, "late": .160, "misses": 20,
         "gap": .34, "chord": 2, "repeat": 4, "ghost": False},
     3: {"perfect": .050, "early": .125, "late": .105, "misses": 12,
         "gap": .09, "chord": 3, "repeat": 99, "ghost": False},
}


class HarleycasterAssetError(RuntimeError):
    pass


@dataclass
class RhythmNote:
    at: float
    lane: int
    duration: float = 0.0
    judged: bool = False
    holding: bool = False
    hit_offset: float = 0.0


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as source:
            return source.getnframes() / float(source.getframerate())
    except (OSError, wave.Error, ZeroDivisionError):
        return 0.0


def load_chart(chart_path: Path):
    """Load editor JSON (milliseconds) and the old prototype format."""
    chart_path = Path(chart_path).resolve()
    try:
        data = json.loads(chart_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise HarleycasterAssetError(f"Chart nem olvashato: {exc}") from exc
    raw_notes = data.get("notes")
    if not isinstance(raw_notes, list):
        raise HarleycasterAssetError("A chart notes mezoje nem lista")
    generated_format = any("time_ms" in note for note in raw_notes)
    notes = []
    last_time = -1.0
    for source in raw_notes:
        try:
            at = (float(source["time_ms"]) / 1000.0
                  if generated_format else float(source["time"]))
            duration = (float(source.get("duration_ms", 0)) / 1000.0
                        if generated_format else float(source.get("duration", 0)))
            lane = int(source["lane"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HarleycasterAssetError(f"Hibas note a chartban: {source!r}") from exc
        if at < 0 or at < last_time or lane not in (0, 1, 2) or duration < 0:
            raise HarleycasterAssetError("Chart idorend/lane/duration hiba")
        notes.append(RhythmNote(at, lane, duration))
        last_time = at
    audio_name = data.get("audio")
    if audio_name:
        audio_path = chart_path.parent / Path(str(audio_name)).name
    else:
        matches = [path for path in chart_path.parent.glob(chart_path.stem.replace(
            ".chart", "") + ".*") if path.suffix.lower() in (".wav", ".ogg", ".mp3")]
        audio_path = matches[0] if matches else None
    if audio_path is None or not audio_path.is_file():
        raise HarleycasterAssetError(f"A chart zeneje nem talalhato: {audio_name!r}")
    duration = float(data.get("duration", 0) or 0)
    if duration <= 0 and audio_path.suffix.lower() == ".wav":
        duration = _wav_duration(audio_path)
    if duration <= 0:
        duration = max((note.at + note.duration for note in notes), default=0) + 2.0
    if not notes or duration <= 0:
        raise HarleycasterAssetError("A chart ures vagy nincs ervenyes hossza")
    return data, notes, audio_path.resolve(), duration


def discover_chart(songs_dir: Path = SONGS_DIR) -> Path:
    candidates = sorted(songs_dir.glob("*.chart.json"),
                        key=lambda path: path.stat().st_mtime, reverse=True)
    errors = []
    for candidate in candidates:
        try:
            load_chart(candidate)
            return candidate
        except HarleycasterAssetError as exc:
            errors.append(f"{candidate.name}: {exc}")
    detail = "; ".join(errors) if errors else "nincs .chart.json"
    raise HarleycasterAssetError(f"Nincs jatszhato Harleycaster chart: {detail}")

class HarleycasterSoloGame:
    """State-machine compatible rhythm game with pygame and hardware input."""

    _ART_CACHE = None

    def __init__(self, chart_path=None, difficulty=0):
        self.chart_path = Path(chart_path) if chart_path else discover_chart()
        self.chart, self._source_notes, self.audio_path, self.duration = load_chart(
            self.chart_path)
        self.title = str(self.chart.get("title") or self.audio_path.stem)
        self.set_difficulty(difficulty)
        self._load_art()
        self._hardware_mask = 0
        self._held_lanes = [False, False, False]
        self._audio_started = False
        self._audio_clock_start = None
        self._music_active = False
        self.reset()

    def set_difficulty(self, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        self.difficulty_level = max(-3, min(3, value))
        self.rules = DIFFICULTY[self.difficulty_level]
        if hasattr(self, "_source_notes"):
            self.notes = self._thin_notes(self._source_notes)

    def _thin_notes(self, source_notes):
        """Keep musical time groups while removing unplayably close attacks."""
        groups = []
        for note in source_notes:
            if groups and abs(groups[-1][0].at - note.at) < .0005:
                groups[-1].append(note)
            else:
                groups.append([note])
        selected = []
        last_group_time = -999.0
        last_lane = None
        repeat_count = 0
        for group in groups:
            at = group[0].at
            has_sustain = any(note.duration > 0.0 for note in group)
            if at - last_group_time < self.rules["gap"] and not has_sustain:
                continue
            ordered = list(group)
            if self.rules["chord"] == 1:
                alternatives = [note for note in ordered if note.lane != last_lane]
                if repeat_count >= self.rules["repeat"]:
                    if not alternatives:
                        continue
                    ordered = alternatives
            chosen = ordered[:self.rules["chord"]]
            for note in chosen:
                selected.append(RhythmNote(note.at, note.lane, note.duration))
            primary_lane = chosen[0].lane
            if primary_lane == last_lane:
                repeat_count += 1
            else:
                last_lane = primary_lane
                repeat_count = 1
            last_group_time = at
        return selected

    @staticmethod
    def _scale(surface, size):
        smooth_ok = platform.machine().lower() not in ("armv7l", "armv6l")
        scaler = pygame.transform.smoothscale if smooth_ok else pygame.transform.scale
        return scaler(surface, size)

    def _load_image(self, path, alpha=True):
        path = Path(path)
        if not path.is_file():
            raise HarleycasterAssetError(f"Hianyzo Harleycaster kep: {path}")
        try:
            loaded = pygame.image.load(str(path))
            loaded = loaded.convert_alpha() if alpha else loaded.convert()
            return loaded
        except pygame.error as exc:
            raise HarleycasterAssetError(f"Kep nem toltheto be: {path.name}: {exc}") from exc

    def _load_sequence(self, directory):
        directory = Path(directory)
        paths = sorted(directory.glob("*.png"))
        if not paths:
            raise HarleycasterAssetError(f"Ures Harleycaster szekvencia: {directory}")
        return tuple(self._load_image(path) for path in paths)

    def _load_prefixed_sequence(self, prefix):
        prefix = Path(prefix)
        paths = sorted(prefix.parent.glob(prefix.name + "*.png"))
        if not paths:
            raise HarleycasterAssetError(
                f"Ures Harleycaster hit feedback szekvencia: {prefix}")
        return tuple(self._load_image(path) for path in paths)

    def _font(self, size):
        return pygame.font.Font(str(FONT_PATH) if FONT_PATH.is_file() else None, size)

    def _load_art(self):
        try:
            self.scene_layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise HarleycasterAssetError(f"Scene layout nem olvashato: {exc}") from exc
        if self.scene_layout.get("canvas") != [WIDTH, HEIGHT]:
            raise HarleycasterAssetError("A GAME_SCENE nem 640x480-as")

        if HarleycasterSoloGame._ART_CACHE is None:
            layers = self.scene_layout["layers"]
            chong = self.scene_layout["chong_comp"]
            heads = {
                name: self._load_image(ART_DIR / spec["file"])
                for name, spec in chong["heads"].items()
            }
            HarleycasterSoloGame._ART_CACHE = {
                "base": self._load_image(
                    ART_DIR / self.scene_layout["fallback_background"]),
                "bgr_upper": self._load_image(
                    ART_DIR / layers["BGR_Upper"]["file"]),
                "bgr_lower": self._load_image(
                    ART_DIR / layers["BGR_UNTER"]["file"]),
                "amp": self._load_image(ART_DIR / layers["Amp"]["file"]),
                "highway": self._load_image(
                    ART_DIR / layers["NoteHighway"]["file"]),
                "noise": self._load_sequence(
                    ART_DIR / layers["Ricsaj"]["directory"]),
                "sound_wave": self._load_sequence(
                    ART_DIR / layers["Hang"]["directory"]),
                "crawl": self._load_sequence(
                    ART_DIR / layers["Crawl"]["directory"]),
                "chong_body": self._load_sequence(
                    ART_DIR / chong["body"]["directory"]),
                "chong_hand": self._load_image(
                    ART_DIR / chong["left_hand"]["file"]),
                "chong_heads": heads,
                "hit_feedback": tuple(
                    self._load_prefixed_sequence(ART_DIR / prefix)
                    for prefix in self.scene_layout["hit_feedback"]["directories"]
                ),
            }
        self.art = HarleycasterSoloGame._ART_CACHE
        self._chong_comp_surface = pygame.Surface(
            (WIDTH, HEIGHT), pygame.SRCALPHA).convert_alpha()
        self._upper_camera_surface = pygame.Surface(
            (WIDTH, HEIGHT), pygame.SRCALPHA).convert_alpha()
        self.font_hud = self._font(21)
        self.font_judgement = self._font(31)
        self.font_result = self._font(40)
        self.result_overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        self.result_overlay.fill((8, 6, 15, 177))

    def reset(self):
        self._stop_music()
        for note in self.notes:
            note.judged = False
        self.score = 0
        self.combo = 0
        self._camera_combo_visual = 0.0
        self.best_combo = 0
        self.hits = 0
        self.misses = 0
        self.cheech = 0.0
        self.outcome = None
        self.last_judgement = ""
        self.judgement_until = 0.0
        self.judgement_color = (255, 246, 205)
        self.hit_lane = None
        self.hit_flash_until = 0.0
        self._hit_animation_elapsed = [-1.0, -1.0, -1.0]
        self.song_time = -TRAVEL_TIME
        self.visual_time = 0.0
        self._crawl_active = False
        self._crawl_elapsed = 0.0
        self._crawl_frame_index = 0
        self._crawl_queue = 0
        self._crawl_display_progress = 0.0
        self._crawl_step_from = 0.0
        self._crawl_step_to = 0.0
        self._fret_hand_angle = 0.0
        self._fret_hand_y_offset = 0.0
        self._fret_hand_lane = 2
        self.active_head = self.scene_layout["chong_comp"]["active_head"]
        self.result_elapsed = 0.0
        self.finished = False
        self._audio_started = False
        self._audio_clock_start = None
        self._hardware_mask = 0
        self._held_lanes = [False, False, False]

    def activate(self):
        # The pre-roll lets the time-zero note travel down the highway before
        # audio starts.  This also makes editor charts with a note at 0 fair.
        self.reset()

    def _start_music(self):
        if self._audio_started:
            return
        self._audio_started = True
        self._audio_clock_start = time.monotonic()
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            pygame.mixer.music.load(str(self.audio_path))
            pygame.mixer.music.set_volume(.90)
            pygame.mixer.music.play(loops=0)
            self._music_active = True
            print(f"[harleycaster] playing {self.audio_path.name} / {self.chart_path.name}")
        except pygame.error as exc:
            # Keep the visual/chart clock alive for diagnostics, but make the
            # missing device visible in the log instead of crashing the GUI.
            self._music_active = False
            print(f"[harleycaster] audio unavailable: {exc}")

    def _stop_music(self):
        if getattr(self, "_music_active", False):
            try:
                pygame.mixer.music.stop()
            except pygame.error:
                pass
        self._music_active = False

    def _playback_time(self):
        if self._audio_clock_start is None:
            return max(0.0, self.song_time)
        fallback = time.monotonic() - self._audio_clock_start
        try:
            position_ms = pygame.mixer.music.get_pos()
        except pygame.error:
            position_ms = -1
        return position_ms / 1000.0 if position_ms >= 0 else fallback

    def _feedback(self, label, lane=None, color=(255, 246, 205)):
        self.last_judgement = label
        self.judgement_until = self.song_time + .55
        self.judgement_color = color
        if lane is not None:
            self.hit_lane = lane
            self.hit_flash_until = self.song_time + .24
            self._hit_animation_elapsed[lane] = 0.0

    def _finish(self, outcome):
        if self.outcome is not None:
            return
        self.outcome = outcome
        self.result_elapsed = 0.0
        if outcome == "UNPLUGGED":
            self.cheech = 1.0
            self._crawl_display_progress = 1.0
            self._crawl_active = False
            self._crawl_queue = 0
            self._crawl_frame_index = len(self.art["crawl"]) - 1
            self._stop_music()

    def _start_crawl_step(self):
        if self._crawl_active or self._crawl_queue <= 0:
            return
        self._crawl_queue -= 1
        self._crawl_active = True
        self._crawl_elapsed = 0.0
        self._crawl_frame_index = 0
        self._crawl_step_from = self._crawl_display_progress
        step = 1.0 / float(self.rules["misses"])
        self._crawl_step_to = min(1.0, self._crawl_step_from + step)

    def _queue_crawl_step(self):
        self._crawl_queue += 1
        self._start_crawl_step()

    def _update_scene_animation(self, dt):
        self.visual_time += dt
        camera_target = max(0.0, min(UPPER_CAMERA_COMBO_CAP,
                                     float(self.combo)))
        if camera_target >= self._camera_combo_visual:
            self._camera_combo_visual = camera_target
        else:
            # A miss drops the gameplay combo immediately, while the camera
            # takes exactly one second to pull back from its current zoom.
            self._camera_combo_visual = max(
                camera_target,
                self._camera_combo_visual - UPPER_CAMERA_COMBO_CAP * dt,
            )
        hit_fps = float(self.scene_layout["hit_feedback"]["fps"])
        for lane, elapsed in enumerate(self._hit_animation_elapsed):
            if elapsed >= 0.0:
                duration = len(self.art["hit_feedback"][lane]) / hit_fps
                self._hit_animation_elapsed[lane] = min(duration, elapsed + dt)
        if not self._crawl_active:
            self._start_crawl_step()
            return
        crawl = self.scene_layout["layers"]["Crawl"]
        frame_count = len(self.art["crawl"])
        duration = frame_count / float(crawl["fps"])
        self._crawl_elapsed = min(duration, self._crawl_elapsed + dt)
        progress = min(1.0, self._crawl_elapsed / duration)
        self._crawl_display_progress = (
            self._crawl_step_from
            + (self._crawl_step_to - self._crawl_step_from) * progress
        )
        self._crawl_frame_index = min(
            frame_count - 1, int(self._crawl_elapsed * crawl["fps"])
        )
        if progress >= 1.0:
            self._crawl_active = False
            self._crawl_display_progress = self._crawl_step_to
            self._crawl_frame_index = frame_count - 1
            self._start_crawl_step()

    def _miss(self, lane=None):
        self.combo = 0
        self.misses += 1
        self.cheech = min(
            1.0, self.cheech + 1.0 / float(self.rules["misses"])
        )
        self._queue_crawl_step()
        self._feedback("MISS!", color=(255, 86, 69))
        if self.misses >= self.rules["misses"]:
            self._finish("UNPLUGGED")

    def _complete_note(self, note):
        """Resolve a tap or fully-held note and award its score/combo."""
        note.judged = True
        note.holding = False
        perfect = abs(note.hit_offset) <= self.rules["perfect"]
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        self.hits += 1
        self._set_fret_hand_lane(note.lane)
        multiplier = min(5, 1 + self.combo // 10)
        self.score += (1000 if perfect else 500) * multiplier
        if self.combo % 8 == 0 and self.misses > 0:
            # A visual retreat never erases recorded misses; it only gives the
            # player a small comeback on Cheech's position.
            self.cheech = max(0.0, self.cheech - .055)
        self._feedback(
            "PERFECT!" if perfect else "GOOD!",
            lane=note.lane,
            color=(255, 245, 110) if perfect else (126, 255, 150),
        )

    def press(self, lane, song_time=None):
        if self.outcome is not None or lane not in (0, 1, 2):
            return
        now = self._playback_time() if song_time is None and self._audio_started \
            else (self.song_time if song_time is None else float(song_time))
        self.song_time = now
        candidates = [note for note in self.notes
                      if note.lane == lane and not note.judged
                      and not note.holding
                      and -self.rules["late"] <= note.at - now
                      <= self.rules["early"]]
        if not candidates:
            # Easy/Normal party play uses ghost tapping: an enthusiastic or
            # very early press does not move Cheech. The note can still be hit
            # on the next press and is penalised only after passing the lane.
            if not self.rules["ghost"]:
                self._miss(lane)
            return
        note = min(candidates, key=lambda item: abs(item.at - now))
        note.hit_offset = now - note.at
        if note.duration > 0.0:
            note.holding = True
            self._set_fret_hand_lane(lane)
            return
        self._complete_note(note)

    def update(self, dt):
        dt = max(0.0, min(float(dt), .1))
        if self.finished:
            return
        self._update_scene_animation(dt)
        if self.outcome is not None:
            self.result_elapsed += dt
            self.finished = self.result_elapsed >= RESULT_SECONDS
            return
        if not self._audio_started:
            self.song_time += dt
            if self.song_time >= 0.0:
                self.song_time = 0.0
                self._start_music()
        else:
            self.song_time = self._playback_time()
        for note in self.notes:
            if note.holding:
                if not self._held_lanes[note.lane]:
                    note.holding = False
                    note.judged = True
                    self._miss(note.lane)
                    if self.outcome is not None:
                        return
                elif self.song_time >= note.at + note.duration:
                    self._complete_note(note)
                continue
            if (not note.judged
                    and self.song_time > note.at + self.rules["late"]):
                note.judged = True
                self._miss()
                if self.outcome is not None:
                    return
        if self.song_time >= self.duration:
            self._finish("SOLO COMPLETE")

    def handle_event(self, event):
        if hasattr(event, "kind"):
            kind = event.kind
            if kind.endswith("_UP"):
                lane = self._lane_for_input_kind(kind)
                if lane is not None:
                    self._held_lanes[lane] = False
                return
            if "FLIPPER_LEFT" in kind:
                self._held_lanes[0] = True
                self.press(0)
            elif "FLIPPER_RIGHT" in kind:
                self._held_lanes[2] = True
                self.press(2)
            elif "PLUNGER" in kind or kind == "PLAYER_PRESS":
                self.press(1)
            return
        keys = {
            pygame.K_LEFT: 0, pygame.K_a: 0,
            pygame.K_SPACE: 1, pygame.K_DOWN: 1, pygame.K_p: 1,
            pygame.K_RIGHT: 2, pygame.K_d: 2,
        }
        if event.type == pygame.KEYUP:
            lane = keys.get(event.key)
            if lane is not None:
                self._held_lanes[lane] = False
            return
        if event.type != pygame.KEYDOWN or getattr(event, "repeat", False):
            return
        if event.key in keys:
            lane = keys[event.key]
            self._held_lanes[lane] = True
            self.press(lane)
        elif event.key == pygame.K_r:
            self.activate()

    @staticmethod
    def _lane_for_input_kind(kind):
        if "FLIPPER_LEFT" in kind:
            return 0
        if "FLIPPER_RIGHT" in kind:
            return 2
        if "PLUNGER" in kind:
            return 1
        return None

    def set_hardware_input(self, mask):
        try:
            mask = int(mask) & 0x07
        except (TypeError, ValueError):
            return
        rising = mask & ~self._hardware_mask
        self._hardware_mask = mask
        self._held_lanes = [
            bool(mask & 0x01),
            bool(mask & 0x04),
            bool(mask & 0x02),
        ]
        if rising & 0x01:
            self.press(0)
        if rising & 0x04:
            self.press(1)
        if rising & 0x02:
            self.press(2)

    def result_dict(self):
        return {
            "total_bonus": int(self.score),
            "score": int(self.score),
            "hits": self.hits,
            "misses": self.misses,
            "best_combo": self.best_combo,
            "outcome": self.outcome,
            "chart": self.chart_path.name,
            "audio": self.audio_path.name,
        }

    def prepare_for_replay(self):
        self._stop_music()
        self.reset()

    @staticmethod
    def _x_for_lane(lane, y):
        progress = max(0.0, min(1.0, (y - SPAWN_Y) / (HIT_Y - SPAWN_Y)))
        spread = 12 + progress * 99
        return 320 + (lane - 1) * spread

    def _set_fret_hand_lane(self, lane):
        poses = {
            0: (30.0, 20.0),
            1: (15.0, 15.0),
            2: (0.0, 0.0),
        }
        if lane not in poses:
            return
        self._fret_hand_lane = lane
        self._fret_hand_angle, self._fret_hand_y_offset = poses[lane]

    @staticmethod
    def _ae_top_left(anchor, position, scale=(100.0, 100.0)):
        return (
            float(position[0]) - float(anchor[0]) * float(scale[0]) / 100.0,
            float(position[1]) - float(anchor[1]) * float(scale[1]) / 100.0,
        )

    @staticmethod
    def _child_transform(parent, child):
        parent_scale = (
            float(parent["scale"][0]) / 100.0,
            float(parent["scale"][1]) / 100.0,
        )
        position = (
            float(parent["position"][0])
            + (float(child["position"][0]) - float(parent["anchor"][0]))
            * parent_scale[0],
            float(parent["position"][1])
            + (float(child["position"][1]) - float(parent["anchor"][1]))
            * parent_scale[1],
        )
        scale = (
            float(child["scale"][0]) * parent_scale[0],
            float(child["scale"][1]) * parent_scale[1],
        )
        return position, scale

    def _blit_anchored(self, target, surface, position, anchor,
                       scale=(100.0, 100.0), angle=0.0):
        scale_x = float(scale[0]) / 100.0
        scale_y = float(scale[1]) / 100.0
        scaled = surface
        if abs(scale_x - 1.0) > .0001 or abs(scale_y - 1.0) > .0001:
            size = (
                max(1, int(round(surface.get_width() * scale_x))),
                max(1, int(round(surface.get_height() * scale_y))),
            )
            scaled = self._scale(surface, size)
        scaled_anchor = pygame.Vector2(
            float(anchor[0]) * scale_x,
            float(anchor[1]) * scale_y,
        )
        if abs(angle) < .0001:
            destination = (
                int(round(float(position[0]) - scaled_anchor.x)),
                int(round(float(position[1]) - scaled_anchor.y)),
            )
            target.blit(scaled, destination)
            return scaled.get_rect(topleft=destination)
        rotated = pygame.transform.rotate(scaled, float(angle))
        center_to_anchor = scaled_anchor - pygame.Vector2(
            scaled.get_width() / 2.0, scaled.get_height() / 2.0)
        rotated_offset = center_to_anchor.rotate(-float(angle))
        center = pygame.Vector2(position) - rotated_offset
        rect = rotated.get_rect(center=(round(center.x), round(center.y)))
        target.blit(rotated, rect)
        return rect

    def _draw_layer(self, screen, surface, spec, position=None, angle=0.0):
        return self._blit_anchored(
            screen,
            surface,
            spec["position"] if position is None else position,
            spec["anchor"],
            spec.get("scale", (100.0, 100.0)),
            angle,
        )

    def _loop_frame(self, art_key, layer_spec):
        sequence = self.art[art_key]
        index = int(self.visual_time * float(layer_spec["fps"])) % len(sequence)
        return sequence[index]

    def _chong_sway_angle(self):
        """Rock the complete Chong precomp between -5 and +5 degrees."""
        phase = self.visual_time % 2.0
        progress = phase if phase <= 1.0 else 2.0 - phase
        eased = progress * progress * (3.0 - 2.0 * progress)
        return -5.0 + 10.0 * eased

    def _upper_camera_effect(self):
        """Return combo-driven zoom, wiggle and red-light intensity."""
        intensity = max(0.0, min(1.0,
                                  self._camera_combo_visual
                                  / UPPER_CAMERA_COMBO_CAP))
        time_value = self.visual_time
        # Several low-amplitude frequencies keep this organic instead of
        # looking like a single mechanical sine-wave shake.
        wiggle_x = intensity * (
            math.sin(time_value * 17.0) * 2.4
            + math.sin(time_value * 29.0) * 1.1
        )
        wiggle_y = intensity * (
            math.sin(time_value * 19.0 + .7) * 1.7
            + math.sin(time_value * 31.0) * .7
        )
        wiggle_angle = intensity * (
            math.sin(time_value * 15.0) * 1.15
            + math.sin(time_value * 23.0 + 1.2) * .55
        )
        zoom = 1.0 + intensity * .24
        red_alpha = int(round(intensity * 62.0))
        return zoom, (wiggle_x, wiggle_y), wiggle_angle, red_alpha

    def _upper_camera_tint(self):
        """Return a slowly cycling psychedelic tint for the upper camera."""
        intensity = max(0.0, min(1.0,
                                  self._camera_combo_visual
                                  / UPPER_CAMERA_COMBO_CAP))
        # Start at red and make the hue drift only as the combo effect builds.
        # One full cycle takes about 16 seconds at maximum intensity.
        hue = (0.01 + self.visual_time * .06 * intensity) % 1.0
        red, green, blue = colorsys.hsv_to_rgb(hue, .92, .75)
        _, _, _, alpha = self._upper_camera_effect()
        return (round(red * 255), round(green * 255), round(blue * 255), alpha)

    def _upper_camera_pivot(self):
        """Find Chong's current head center in GAME_SCENE coordinates."""
        parent = self.scene_layout["layers"]["CHONGGITARREN"]
        comp = self.scene_layout["chong_comp"]
        head_spec = comp["heads"].get(
            self.active_head, comp["heads"][comp["active_head"]])
        position, _ = self._child_transform(parent, head_spec)
        pivot = pygame.Vector2(position)
        parent_position = pygame.Vector2(parent["position"])
        # The Chong comp itself is also rotated by the independent sway.
        pivot = parent_position + (pivot - parent_position).rotate(
            -self._chong_sway_angle())
        return pivot

    def _draw_chong(self, screen):
        parent = self.scene_layout["layers"]["CHONGGITARREN"]
        comp = self.scene_layout["chong_comp"]
        target = self._chong_comp_surface
        target.fill((0, 0, 0, 0))

        body_spec = comp["body"]
        body = self._loop_frame("chong_body", body_spec)
        self._blit_anchored(
            target,
            body,
            body_spec["position"],
            body_spec["anchor"],
            body_spec["scale"],
        )

        hand_spec = comp["left_hand"]
        hand_position = (
            hand_spec["position"][0],
            hand_spec["position"][1] + self._fret_hand_y_offset,
        )
        self._blit_anchored(
            target,
            self.art["chong_hand"],
            hand_position,
            hand_spec["anchor"],
            hand_spec["scale"],
            self._fret_hand_angle,
        )

        head_cycle = ("head4", "head1", "head2", "head3")
        self.active_head = head_cycle[
            int(self.visual_time // 3.0) % len(head_cycle)]
        head_spec = comp["heads"].get(
            self.active_head, comp["heads"][comp["active_head"]])
        self._blit_anchored(
            target,
            self.art["chong_heads"].get(
                self.active_head,
                self.art["chong_heads"][comp["active_head"]],
            ),
            head_spec["position"],
            head_spec["anchor"],
            head_spec["scale"],
        )
        self._draw_layer(
            screen,
            target,
            parent,
            angle=self._chong_sway_angle(),
        )

    def _draw_scene(self, screen):
        layers = self.scene_layout["layers"]
        screen.blit(self.art["base"], (0, 0))
        upper = self._upper_camera_surface
        upper.fill((0, 0, 0, 0))
        self._draw_layer(upper, self.art["bgr_upper"], layers["BGR_Upper"])
        self._draw_chong(upper)
        zoom, wiggle, camera_angle, _ = self._upper_camera_effect()
        pivot = self._upper_camera_pivot()
        camera_position = pivot + pygame.Vector2(wiggle)
        camera_spec = {
            "anchor": (pivot.x, pivot.y),
            "position": (camera_position.x, camera_position.y),
            "scale": (zoom * 100.0, zoom * 100.0),
        }
        self._draw_layer(screen, upper, camera_spec, angle=camera_angle)
        red, green, blue, alpha = self._upper_camera_tint()
        if alpha:
            red_overlay = pygame.Surface((WIDTH, 260), pygame.SRCALPHA)
            red_overlay.fill((red, green, blue, alpha))
            screen.blit(red_overlay, (0, 0))
        self._draw_layer(screen, self.art["bgr_lower"], layers["BGR_UNTER"])
        self._draw_layer(
            screen,
            self._loop_frame("noise", layers["Ricsaj"]),
            layers["Ricsaj"],
        )
        self._draw_layer(screen, self.art["amp"], layers["Amp"])

        crawl = layers["Crawl"]
        start = crawl["start_position"]
        end = crawl["end_position"]
        crawl_position = (
            start[0] + (end[0] - start[0]) * self._crawl_display_progress,
            start[1] + (end[1] - start[1]) * self._crawl_display_progress,
        )
        self._blit_anchored(
            screen,
            self.art["crawl"][self._crawl_frame_index],
            crawl_position,
            crawl["anchor"],
            crawl["scale"],
        )
        self._draw_layer(
            screen,
            self._loop_frame("sound_wave", layers["Hang"]),
            layers["Hang"],
        )
        self._draw_layer(screen, self.art["highway"], layers["NoteHighway"])

    @staticmethod
    def _draw_text(screen, face, label, pos, color=(255, 246, 205), center=False):
        shadow = face.render(label, True, (23, 15, 19))
        text = face.render(label, True, color)
        rect = text.get_rect(center=pos) if center else text.get_rect(topleft=pos)
        screen.blit(shadow, rect.move(2, 2))
        screen.blit(text, rect)

    @staticmethod
    def _mix_color(first, second, amount):
        return tuple(round(a + (b - a) * amount)
                     for a, b in zip(first, second))

    def _draw_sustain_body(self, screen, lane, head, tail, head_half_width):
        """Draw a tapered neon ribbon joining a sustain gem to its tail."""
        head_x, head_y = head
        tail_x, tail_y = tail
        tail_progress = max(
            0.0, min(1.0, (tail_y - SPAWN_Y) / (HIT_Y - SPAWN_Y)))
        body_head_half = max(9, int(head_half_width * .82))
        body_tail_half = max(6, int(6 + tail_progress * 6))

        def ribbon(extra=0):
            return (
                (int(tail_x - body_tail_half - extra), int(tail_y)),
                (int(tail_x + body_tail_half + extra), int(tail_y)),
                (int(head_x + body_head_half + extra), int(head_y)),
                (int(head_x - body_head_half - extra), int(head_y)),
            )

        color = LANE_COLORS[lane]
        light = self._mix_color(color, (255, 255, 240), .58)
        deep = self._mix_color(color, (22, 10, 28), .34)
        pygame.draw.polygon(screen, (12, 8, 18), ribbon(4))
        pygame.draw.polygon(screen, deep, ribbon(1))
        pygame.draw.polygon(screen, color, ribbon())
        pygame.draw.aaline(
            screen, light,
            (int(tail_x - body_tail_half + 1), int(tail_y)),
            (int(head_x - body_head_half + 1), int(head_y)),
        )
        pygame.draw.aaline(
            screen, light,
            (int(tail_x + body_tail_half - 1), int(tail_y)),
            (int(head_x + body_head_half - 1), int(head_y)),
        )
        tail_cap = pygame.Rect(
            int(tail_x - body_tail_half), int(tail_y - body_tail_half * .42),
            body_tail_half * 2, max(4, int(body_tail_half * .84)),
        )
        pygame.draw.ellipse(screen, color, tail_cap)
        pygame.draw.ellipse(screen, light, tail_cap, 2)

    def draw(self, screen):
        now = self.song_time
        self._draw_scene(screen)

        for note in self.notes:
            if note.judged:
                continue
            progress = 1 - (note.at - now) / TRAVEL_TIME
            if (progress < 0
                    or (note.duration <= 0.0 and progress > 1.08)
                    or (note.duration > 0.0
                        and now > note.at + note.duration)):
                continue
            visual_progress = max(0.0, min(1.0, progress))
            raw_y = SPAWN_Y + progress * (HIT_Y - SPAWN_Y)
            y = (min(raw_y, HIT_Y)
                 if note.duration > 0.0 else raw_y)
            x = int(self._x_for_lane(note.lane, y))
            half_width = int(8 + 10 * visual_progress)
            half_height = int(4 + 5 * visual_progress)
            if note.duration > 0:
                tail_start = max(
                    SPAWN_Y,
                    min(HIT_Y, raw_y - note.duration / TRAVEL_TIME
                        * (HIT_Y - SPAWN_Y)),
                )
                tail_x = int(self._x_for_lane(note.lane, tail_start))
                self._draw_sustain_body(
                    screen, note.lane,
                    (x, int(y)), (tail_x, int(tail_start)), half_width,
                )
            shadow = pygame.Rect(
                x - half_width - 3,
                int(y) - half_height + 1,
                (half_width + 3) * 2,
                (half_height + 3) * 2,
            )
            note_rect = pygame.Rect(
                x - half_width,
                int(y) - half_height,
                half_width * 2,
                half_height * 2,
            )
            pygame.draw.ellipse(screen, (13, 10, 20), shadow)
            pygame.draw.ellipse(screen, LANE_COLORS[note.lane], note_rect)
            pygame.draw.ellipse(screen, (255, 249, 209), note_rect, 2)

        hit_fps = float(self.scene_layout["hit_feedback"]["fps"])
        highway_spec = self.scene_layout["layers"]["NoteHighway"]
        for lane, elapsed in enumerate(self._hit_animation_elapsed):
            if elapsed < 0.0:
                continue
            sequence = self.art["hit_feedback"][lane]
            frame_index = min(len(sequence) - 1, int(elapsed * hit_fps))
            self._draw_layer(screen, sequence[frame_index], highway_spec)

        self._draw_text(screen, self.font_hud, f"SCORE {self.score:,}", (10, 5))
        self._draw_text(screen, self.font_hud,
                        f"MISS {self.misses}/{self.rules['misses']}",
                        (320, 16), center=True)
        self._draw_text(screen, self.font_hud, f"COMBO x{self.combo}", (480, 5))
        if (self.last_judgement == "MISS!"
                and now < self.judgement_until):
            self._draw_text(screen, self.font_judgement,
                            self.last_judgement, (320, 310),
                            color=self.judgement_color, center=True)
        if now < 0 and self.outcome is None:
            count = max(1, int(math.ceil(-now)))
            self._draw_text(screen, self.font_judgement,
                            str(count), (320, 111), center=True)
        if self.outcome:
            screen.blit(self.result_overlay, (0, 0))
            self._draw_text(screen, self.font_result,
                            self.outcome, (320, 211), center=True)
            detail = f"{self.score:,} PONT   {self.hits} HIT   {self.misses} MISS"
            self._draw_text(screen, self.font_hud,
                            detail, (320, 260), center=True)
            self._draw_text(screen, self.font_hud,
                            "R = RESTART", (320, 292), center=True)
