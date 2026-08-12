"""Munchies Abduction - lightweight 640x480 pygame pinball minigame.

The game is deliberately driven one frame at a time (`handle_event`, `update`,
`draw`) so it can live inside the pinball GUI's existing event loop.  `run_*`
at the bottom is a standalone development entry point.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math
import os
import queue
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# Headless Raspberry Pi OS does not always choose an SDL audio backend even
# though mpv uses ALSA successfully. This must be set before mixer.init().
if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    os.environ.setdefault("SDL_AUDIODRIVER", "alsa")

import pygame

# Configure the mixer before ScoreGUI's first pygame.init().  Without this,
# SDL opens ALSA with its small default buffer and the later minigame-specific
# mixer.init() calls are already too late to change it.  2048 samples at
# 44.1 kHz add roughly 46 ms of latency while giving the Pi 3 enough headroom
# for PNG decoding/rendering spikes without starving the audio callback.
MIXER_BUFFER_SAMPLES = 2048
pygame.mixer.pre_init(
    frequency=44100,
    size=-16,
    channels=2,
    buffer=MIXER_BUFFER_SAMPLES,
)


WIDTH, HEIGHT, FPS = 640, 480, 30
HORIZON_Y, ROAD_BOTTOM = 126, 480
GAME_SECONDS, RESULTS_SECONDS = 35.0, 4.5
TUTORIAL_SECONDS = 15.0
SPEED_RAMP_END_SECONDS = 45.0
TIME_REWARD_FADE_START = 50.0
TIME_REWARD_FADE_END = 220.0
TIME_BANK_FULL_REWARD_SECONDS = 15.0
TIME_BANK_DAMPED_REWARD_SECONDS = 30.0
TIME_BANK_MIN_REWARD_SCALE = .5
MISSION_SUCCESS_FOOD = 12
# The result screen normally lasts 4.5 seconds, but it may stay up a little
# longer so a mission-result voice and its optional tag are never cut short.
RESULTS_VOICE_MAX_SECONDS = 7.0
INTRO_TITLE_SECONDS = 1.0
INTRO_POST_AUDIO_SECONDS = 0.0
COUNTDOWN_SECONDS = 3.0
COUNTDOWN_SLOT_SECONDS = COUNTDOWN_SECONDS / 3.0
VOICE_DUCK_SFX_GAIN = .36
VOICE_DUCK_ATTACK_SECONDS = .055
VOICE_DUCK_RELEASE_SECONDS = .28
VOICE_LAUGH_SILENCE_SECONDS = 4.0
TRACK_LENGTH_METERS = 92.4
TRACK_FRAME_COUNT = 600.0
METERS_PER_SOURCE_FRAME = TRACK_LENGTH_METERS / TRACK_FRAME_COUNT  # 0.154 m
# Blender calibration fit from assets/Minigame/Calibration (1 m sphere,
# 96.4 m constant-speed travel over 600 frames). 563 unclipped frames give
# 0.66 px diameter RMSE and 0.43 px centre-Y RMSE.
CALIBRATION_DISTANCE_METERS = 96.4
CALIBRATION_DIAMETER_NUMERATOR = 10456.899551542067
CALIBRATION_VANISH_FRAME = 620.8421671083554
CALIBRATION_Y_PER_DIAMETER = 1.3041104390419893
CALIBRATION_HORIZON_Y = 77.85246225104041
ITEM_APPROACH_FRAMES = 580.0  # sphere centre reaches the UFO at about frame 580
ITEM_WORLD_SIZE_METERS = .275
ITEM_MIN_PIXELS, ITEM_MAX_PIXELS = 1, 71
ITEM_Y_OFFSET = 10
CAPTURE_DURATION = .38
ROAD_VANISH_X, ROAD_VANISH_Y = WIDTH / 2, 100
ROAD_PLAYER_LEFT, ROAD_PLAYER_RIGHT = 100, WIDTH - 100
ROAD_HALF_WIDTH_AT_UFO = (ROAD_PLAYER_RIGHT - ROAD_PLAYER_LEFT) / 2
BEAM_TOP_Y, BEAM_BASE_Y, BEAM_HALF_WIDTH = 286, 407, 62
BEAM_TOP_INSET = 6
BEAM_CAPTURE_LEAD = 24
ITEM_HORIZON_Y = 132
VOICE_CALLOUT_TARGET_Y = 205
VOICE_CALLOUT_HALF_WINDOW = 34
UFO_LIGHT_FPS = 15.0
UFO_SHADOW_Y_OFFSET = 46
UFO_VISUAL_Y_OFFSET = -10
UFO_BOB_AMPLITUDE = 3
UFO_BOB_HZ = .72

GOOD_VALUES = {
    "pizza": 2250, "burrito": 1600, "chips": 1100,
    "burger": 1900, "taco": 1600, "donut": 1400, "soda": 900,
}
BAD_KINDS = (
    "badge", "bat", "boot", "can", "policehat", "skateboard", "trash", "trashcan",
)
POLICE_BAD_KINDS = frozenset(("badge", "bat", "policehat"))
# Voice tiers follow the refreshed sheet (small at 3, large at 5). The extra
# x4/x5 scoring tiers keep a long clean run rewarding without inventing voice
# categories that are not present in the supplied recordings.
COMBO_TIERS = ((3, 2), (5, 3), (8, 4), (12, 5))
COMBO_VOICE_EVENTS = {3: "combo_small", 5: "combo_large"}
COMBO_TIMEOUT_SECONDS = 1.35
COLLECT_TARGET = 10
COLLECT_PANEL_SECONDS = 2.0
COLLECTION_BONUS_POINTS = 10_000
COLLECTION_TIME_BONUS_SECONDS = 5.0
FOOD_TIME_BONUS_SECONDS = 2.0
JUNK_SCORE_PENALTY = 1_500

# Seven service-menu positions.  NORMAL (0) is deliberately all 1.0/0.0 so
# selecting it preserves the exact pre-menu gameplay.  Harder modes add
# pressure through speed, clock economy, wider lanes and choice patterns --
# never by simply flooding the road with junk.  The -3 profile postpones the
# late reward fade far enough to behave as an "almost endless" chill mode.
DIFFICULTY_PROFILES = {
    -3: {"speed": .82, "clock": .75, "reward": 1.70, "fade": .40,
         "junk_time": .70, "combo_window": 1.35, "lane": -.10, "patterns": .65},
    -2: {"speed": .88, "clock": .84, "reward": 1.40, "fade": .60,
         "junk_time": .82, "combo_window": 1.20, "lane": -.06, "patterns": .78},
    -1: {"speed": .94, "clock": .92, "reward": 1.18, "fade": .80,
         "junk_time": .92, "combo_window": 1.10, "lane": -.03, "patterns": .90},
     0: {"speed": 1.0, "clock": 1.0, "reward": 1.0, "fade": 1.0,
         "junk_time": 1.0, "combo_window": 1.0, "lane": 0.0, "patterns": 1.0},
     1: {"speed": 1.06, "clock": 1.04, "reward": .93, "fade": 1.07,
         "junk_time": 1.08, "combo_window": .94, "lane": .02, "patterns": 1.08},
     2: {"speed": 1.13, "clock": 1.09, "reward": .85, "fade": 1.14,
         "junk_time": 1.16, "combo_window": .88, "lane": .04, "patterns": 1.17},
     3: {"speed": 1.20, "clock": 1.15, "reward": .75, "fade": 1.22,
         "junk_time": 1.25, "combo_window": .82, "lane": .06, "patterns": 1.28},
}

# Internal gameplay names -> (asset directory, filename stem).  The burrito
# files intentionally use the supplied single-r "Burito" spelling.
ITEM_SPRITE_LAYOUT = {
    "burger": ("Food", "burger"),
    "burrito": ("Food", "burito"),
    "chips": ("Food", "chips"),
    "donut": ("Food", "donut"),
    "pizza": ("Food", "pizza"),
    "soda": ("Food", "soda"),
    "taco": ("Food", "taco"),
    "badge": ("Junk", "badge"),
    "bat": ("Junk", "bat"),
    "boot": ("Junk", "boot"),
    "can": ("Junk", "can"),
    "policehat": ("Junk", "policehat"),
    "skateboard": ("Junk", "skateboard"),
    "trash": ("Junk", "trash"),
    "trashcan": ("Junk", "trashcan"),
}


def _voice_ids(first, last):
    return tuple(f"VL{number:03d}" for number in range(first, last + 1))


# Runtime pools distilled from munchies_abduction_voice_lines.xlsx. Audio is
# discovered by ID, so dropping a newly recorded line into Sound/Voices
# is enough to enable it; missing IDs are silently ignored.
VOICE_EVENT_IDS = {
    "game_start": _voice_ids(1, 8),
    "good_item_spawn": _voice_ids(9, 15),
    "food_collected": _voice_ids(16, 27),
    "food_collected:pizza": _voice_ids(28, 33),
    "food_collected:burrito": _voice_ids(34, 38),
    "food_collected:chips": _voice_ids(39, 43),
    "combo_small": _voice_ids(44, 48),
    "combo_large": _voice_ids(49, 54),
    "junk_collected": _voice_ids(55, 66),
    # VL069 literally says "Put the badge back", so it must never be chosen
    # for the hat or baton. Badge pickups keep the generic police reactions
    # too, with this one extra item-specific option.
    "police_item": ("VL067", "VL068", "VL070", "VL071", "VL072"),
    "police_item:badge": _voice_ids(67, 72),
    "valuable_missed": _voice_ids(73, 78),
    "idle": _voice_ids(79, 86),
    "time_10": _voice_ids(87, 90),
    "time_5": _voice_ids(91, 95),
    "time_over": ("VL099",),
    "mission_complete": ("VL100", "VL104", "VL105", "VL106"),
    "mission_failed": ("VL110", "VL112"),
    "silence_laugh": ("VL114", "VL115"),
    "collection_complete": _voice_ids(126, 135),
}
VOICE_ENDING_TAG_IDS = {
    "mission_complete": ("VL107",),
    "mission_failed": ("VL113",),
}


@dataclass(frozen=True)
class VoiceRule:
    chance: float
    cooldown: float
    priority: int
    interrupt: bool = False


VOICE_RULES = {
    "game_start": VoiceRule(1.0, 1.8, 2),
    "good_item_spawn": VoiceRule(.40, 2.5, 1),
    "food_collected": VoiceRule(.40, 2.5, 1),
    "food_collected:pizza": VoiceRule(.40, 2.5, 1),
    "food_collected:burrito": VoiceRule(.40, 2.5, 1),
    "food_collected:chips": VoiceRule(.40, 2.5, 1),
    "combo_small": VoiceRule(.40, 1.5, 2),
    "combo_large": VoiceRule(.40, 1.5, 2),
    "junk_collected": VoiceRule(.40, 1.6, 2),
    "police_item": VoiceRule(1.0, 1.2, 3, True),
    "police_item:badge": VoiceRule(1.0, 1.2, 3, True),
    # Every missed food gets one attempt. The cooldown and busy check provide
    # the anti-spam behaviour; stacking another random gate made this event
    # practically inaudible in a short arcade round.
    "valuable_missed": VoiceRule(.40, 3.5, 1),
    "idle": VoiceRule(.40, 5.0, 0),
    "time_10": VoiceRule(1.0, 0.0, 3, True),
    "time_5": VoiceRule(1.0, 0.0, 3, True),
    "time_over": VoiceRule(1.0, 0.0, 3, True),
    "mission_complete": VoiceRule(1.0, 0.0, 3, True),
    "mission_failed": VoiceRule(1.0, 0.0, 3, True),
    # Full-set lines are deliberately longer payoff moments. Priority 4 sits
    # above the ordinary critical tier, so warnings/police/random chatter
    # cannot cut one off after it has started.
    "collection_complete": VoiceRule(1.0, 0.0, 4, True),
    # Silence filler: never interrupts speech, ignores the ordinary chatter
    # budget, and fires only after the dedicated four-second silence timer.
    "silence_laugh": VoiceRule(1.0, 0.0, 3),
}
BANTER_CHANCE = .40


VOICE_ITEM_ALIASES = {
    "burger": "burger",
    "burito": "burrito",
    "burrito": "burrito",
    "chips": "chips",
    "donut": "donut",
    "pizza": "pizza",
    "pizzaslice": "pizza",
    "soda": "soda",
    "taco": "taco",
}


def _normalize_voice_item(value):
    if not value:
        return None
    compact = re.sub(r"[^a-z]", "", value.lower())
    return VOICE_ITEM_ALIASES.get(compact, compact or None)


def _fixed_voice_speaker(number):
    """Return the XLSX character for lines that are not marked Either."""
    ranges = (
        (1, 4, "Chong"), (5, 8, "Cheech"),
        (16, 21, "Chong"), (22, 27, "Cheech"),
        (28, 30, "Cheech"), (31, 33, "Chong"),
        (34, 36, "Cheech"), (37, 38, "Chong"),
        (39, 41, "Cheech"), (42, 42, "Chong"),
        (43, 44, "Cheech"), (45, 45, "Chong"),
        (46, 47, "Cheech"), (48, 48, "Chong"),
        (49, 49, "Cheech"), (50, 51, "Chong"),
        (52, 52, "Cheech"), (53, 54, "Chong"),
        (55, 60, "Cheech"), (61, 66, "Chong"),
        (79, 82, "Cheech"), (83, 86, "Chong"),
        (114, 117, "Cheech"), (118, 121, "Chong"),
        (122, 123, "Chong"), (124, 125, "Cheech"),
    )
    return next((speaker for first, last, speaker in ranges
                 if first <= number <= last), None)


def _is_raspberry_pi():
    override = os.environ.get("MUNCHIES_LOW_POWER")
    if override is not None:
        return override.strip().lower() not in ("", "0", "false", "no")
    try:
        return "raspberry pi" in Path("/proc/device-tree/model").read_text(
            encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False


def _font(size: int) -> pygame.font.Font:
    path = os.path.join(os.path.dirname(__file__), "assets", "Modak.ttf")
    return pygame.font.Font(path if os.path.isfile(path) else None, size)


def _text(font, value, color=(255, 255, 255), outline=(12, 5, 24), width=2):
    base = font.render(str(value), True, color)
    result = pygame.Surface((base.get_width() + width * 2, base.get_height() + width * 2), pygame.SRCALPHA)
    edge = font.render(str(value), True, outline)
    for dx, dy in ((-width, 0), (width, 0), (0, -width), (0, width),
                   (-width, -width), (width, -width), (-width, width), (width, width)):
        result.blit(edge, (width + dx, width + dy))
    result.blit(base, (width, width))
    return result


class StreamingBackground:
    """Asynchronously streams a long, numbered PNG sequence.

    Only the main thread calls ``convert_alpha`` and blits surfaces.  The
    workers perform file IO/PNG decoding and keep a bounded request queue;
    this avoids both display-thread races and loading hitches.  A 60-frame
    LRU window keeps memory usage independent from sequence length.
    """

    SOURCE_FPS = 30.0
    OUTPUT_FPS = 30.0
    # The old Pi profile deliberately held each road frame for two display
    # ticks. That protected a single PNG decoder, but the resulting 15 FPS road
    # remained visibly jerky even when the rest of the game held a perfect 30.
    LOW_POWER_OUTPUT_FPS = 30.0
    LOW_POWER_LOADER_WORKERS = 2
    DESKTOP_LOADER_WORKERS = 1
    CACHE_SIZE = 60
    LOOK_BEHIND = 5
    READY_SIZE = 8
    # At 30 FPS one conversion per tick can only feed the visible frame and can
    # never rebuild look-ahead after acceleration. The new frames are opaque
    # RGB, so a second display-format conversion is cheap enough to restore a
    # safety margin without the former full-screen RGBA blend spike.
    CONVERTS_PER_TICK = 2

    def __init__(self):
        base = Path(__file__).resolve().parent / "assets" / "Minigame"
        level_dir = base / "Level"
        self.paths = sorted(level_dir.glob("STREET_*.png"))
        if not self.paths:  # backwards-compatible name while assets migrate
            self.paths = sorted(level_dir.glob("Scrolling_background_*.png"))
        background_path = base / "UI" / "Level_BG.png"
        if not background_path.is_file():
            background_path = base / "Level_BG.png"
        self.static_background = self._load_static_background(background_path)
        self._low_power = _is_raspberry_pi()
        self.output_fps = (
            self.LOW_POWER_OUTPUT_FPS if self._low_power else self.OUTPUT_FPS
        )
        self.loader_worker_count = (
            self.LOW_POWER_LOADER_WORKERS
            if self._low_power else self.DESKTOP_LOADER_WORKERS
        )
        self.cache = OrderedDict()
        self.playhead = 0.0
        self.current_index = 0
        self._output_accumulator = 0.0
        self._last_speed = None
        self._speed_acceleration = 0.0
        self._requests = queue.Queue(maxsize=self.CACHE_SIZE * 2)
        # Decoded RGBA surfaces are roughly 1.2 MiB each. A 60-entry ready
        # queue duplicated the 60-frame display cache and could consume about
        # 145 MiB in total. A short queue still absorbs decoder jitter while
        # avoiding memory pressure and needless CPU competition on the Pi.
        self._ready = queue.Queue(maxsize=self.READY_SIZE)
        self._pending = set()
        self._desired = {0}
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self._workers = []
        self._draw_requests = 0
        self._cache_misses = 0
        self._fallback_gap_total = 0
        self._fallback_gap_max = 0
        self._started = False
        self._closed = False

        # Guarantee a drawable first frame.  Worker startup is deliberately
        # deferred until the countdown: on the 32-bit Pi, running
        # pygame.image.load() in these threads while the main thread creates
        # the sprite scale cache can crash inside SDL with a Bus Error.
        if self.paths:
            try:
                self.cache[0] = self._prepare_frame(pygame.image.load(str(self.paths[0])))
            except pygame.error as exc:
                print(f"[munchies] first street frame load failed: {exc}")

    def start(self):
        """Start asynchronous decoding after all setup-time transforms."""
        if self._started or self._closed or not self.paths:
            return
        self._started = True
        for worker_index in range(self.loader_worker_count):
            worker = threading.Thread(
                target=self._loader,
                name=f"munchies-bg-loader-{worker_index + 1}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)
        self._request_window(.6)

    @staticmethod
    def _load_static_background(path):
        canvas = pygame.Surface((WIDTH, HEIGHT)).convert()
        canvas.fill((4, 4, 24))
        if not path.is_file():
            return canvas
        try:
            image = pygame.image.load(str(path)).convert()
            if image.get_size() != (WIDTH, HEIGHT):
                print(
                    f"[munchies] Level_BG must be {WIDTH}x{HEIGHT}, "
                    f"got {image.get_width()}x{image.get_height()}"
                )
                return canvas
            canvas.blit(image, (0, 0))
        except pygame.error as exc:
            print(f"[munchies] Level_BG load failed: {exc}")
        return canvas

    def _prepare_frame(self, surface):
        if surface.get_size() != (WIDTH, HEIGHT):
            surface = pygame.transform.scale(surface, (WIDTH, HEIGHT))
        if surface.get_flags() & pygame.SRCALPHA:
            # Backwards-compatible path for an older transparent sequence:
            # pay for the alpha blend once as it enters the cache, never on
            # every display frame.
            composite = self.static_background.copy()
            composite.blit(surface.convert_alpha(), (0, 0))
            return composite
        return surface.convert()

    def _loader(self):
        while not self._stop.is_set():
            try:
                index = self._requests.get(timeout=.1)
            except queue.Empty:
                continue
            if index is None:
                break
            with self._pending_lock:
                if index not in self._desired:
                    self._pending.discard(index)
                    continue
            try:
                decoded = pygame.image.load(str(self.paths[index]))
                while not self._stop.is_set():
                    with self._pending_lock:
                        if index not in self._desired:
                            self._pending.discard(index)
                            break
                    try:
                        self._ready.put((index, decoded), timeout=.1)
                        break
                    except queue.Full:
                        continue
            except (pygame.error, OSError) as exc:
                print(f"[munchies] frame {index} load failed: {exc}")
                with self._pending_lock:
                    self._pending.discard(index)

    def _request_window(self, speed):
        if not self.paths:
            return
        # Predict the actual output indices, not every source frame. At higher
        # world speed the predictor naturally skips source PNGs, so
        # acceleration does not multiply decoder workload.
        # Keep the currently displayed frame pinned even between two output
        # ticks; otherwise the continuously moving playhead could invalidate
        # its request before the worker finishes decoding it.
        desired = [self.current_index]
        slots = list(range(0, self.CACHE_SIZE - self.LOOK_BEHIND))
        slots.extend(range(-1, -self.LOOK_BEHIND - 1, -1))
        for slot in slots:
            seconds_ahead = slot / self.output_fps
            # The street accelerates throughout the minute. Constant-speed
            # prediction drifts by several source frames at the far end of a
            # 60-frame window, invalidating useful prefetched images. A local
            # kinematic prediction tracks the curve closely enough to keep
            # the current playhead inside the warmed cache.
            source_offset = self.SOURCE_FPS * (
                speed * seconds_ahead
                + .5 * self._speed_acceleration * seconds_ahead ** 2
            )
            index = int(self.playhead + source_offset) % len(self.paths)
            if index not in desired:
                desired.append(index)
        with self._pending_lock:
            self._desired = set(desired)
            requested = [
                index for index in desired
                if index not in self.cache and index not in self._pending
            ]
            self._pending.update(requested)
        # Queue the near frames first. Batch bookkeeping avoids taking the
        # lock roughly 900 times per second while the road is moving.
        for position, index in enumerate(requested):
            try:
                self._requests.put_nowait(index)
            except queue.Full:
                with self._pending_lock:
                    self._pending.difference_update(requested[position:])
                break

    def _consume_ready(self, limit=None, scan_limit=8):
        if limit is None:
            limit = self.CONVERTS_PER_TICK
        converted = inspected = 0
        while converted < limit and inspected < scan_limit:
            try:
                index, decoded = self._ready.get_nowait()
            except queue.Empty:
                break
            inspected += 1
            with self._pending_lock:
                still_useful = index in self._desired or index == self.current_index
            if not still_useful:
                # Speed changes continuously, so a far-ahead decoded frame
                # can leave the prediction window before it reaches the main
                # thread. Do not pay for an unnecessary 640x480 conversion.
                with self._pending_lock:
                    self._pending.discard(index)
                continue
            try:
                self.cache[index] = self._prepare_frame(decoded)
                converted += 1
                self.cache.move_to_end(index)
                while len(self.cache) > self.CACHE_SIZE:
                    obsolete = next((key for key in self.cache if key not in self._desired), None)
                    if obsolete is not None:
                        del self.cache[obsolete]
                    else:
                        self.cache.popitem(last=False)
            finally:
                with self._pending_lock:
                    self._pending.discard(index)

    def prime(self):
        """Move one decoded frame into video memory without moving the road."""
        self._consume_ready()

    def update(self, dt, speed):
        self._consume_ready()
        if not self.paths:
            return
        if self._last_speed is not None and dt > 1e-6:
            measured = (speed - self._last_speed) / dt
            measured = max(-.25, min(.25, measured))
            self._speed_acceleration += (measured - self._speed_acceleration) * .25
        self._last_speed = speed
        self.playhead = (self.playhead + self.SOURCE_FPS * speed * dt) % len(self.paths)
        self._output_accumulator += dt
        output_period = 1.0 / self.output_fps
        if self._output_accumulator >= output_period:
            self._output_accumulator %= output_period
            self.current_index = int(self.playhead) % len(self.paths)
            self._request_window(speed)

    def _nearest_cached(self, wanted):
        self._draw_requests += 1
        if wanted in self.cache:
            self.cache.move_to_end(wanted)
            return self.cache[wanted]
        self._cache_misses += 1
        if not self.cache:
            return None
        # Prefer the closest frame behind the playhead, so a slow storage
        # device never makes the street jump forward unexpectedly.
        count = len(self.paths)
        index = min(self.cache, key=lambda i: (wanted - i) % count)
        fallback_gap = (wanted - index) % count
        self._fallback_gap_total += fallback_gap
        self._fallback_gap_max = max(self._fallback_gap_max, fallback_gap)
        self.cache.move_to_end(index)
        return self.cache[index]

    def draw(self, screen):
        frame = self._nearest_cached(self.current_index)
        if frame is not None:
            # New STREET frames already contain Level_BG and are opaque RGB.
            # One display-format blit replaces the old background blit plus a
            # full-screen per-pixel alpha blend.
            screen.blit(frame, (0, 0))
        else:
            screen.blit(self.static_background, (0, 0))

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        for _ in self._workers:
            try:
                self._requests.put_nowait(None)
            except queue.Full:
                break
        deadline = time.monotonic() + .5
        for worker in self._workers:
            if worker.is_alive():
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
        if self._draw_requests:
            hit_rate = 100.0 * (
                self._draw_requests - self._cache_misses
            ) / self._draw_requests
            average_gap = (
                self._fallback_gap_total / self._cache_misses
                if self._cache_misses else 0.0
            )
            print(
                "[munchies] background stream: "
                f"{hit_rate:.1f}% cache hits, {self._cache_misses} fallbacks, "
                f"average/max gap {average_gap:.1f}/{self._fallback_gap_max} frames, "
                f"{self.loader_worker_count} decoder(s) @ {self.output_fps:g} FPS"
            )


class AssetBank:
    """Loads both shadow and captured-glow variants into size caches."""

    # One cached sprite for every integer pixel size. The calibration formula
    # remains continuous; quantisation is now at most half a pixel instead of
    # the visibly chunky 4-9 px jumps of the old 16-step cache.
    STEPS = ITEM_MAX_PIXELS - ITEM_MIN_PIXELS + 1

    def __init__(self, progress_callback=None):
        report = progress_callback or (lambda _progress, _status: None)
        report(0.0, "ROAD CACHE")
        self.background = StreamingBackground()
        report(0.08, "UFO")
        self.ufo_body, self.ufo_shadow, self.ufo_light_frames = self._load_ufo_assets()
        report(0.15, "USER INTERFACE")
        (self.ui_frame_parts, self.ui_logo, self.ui_score_panel,
         self.ui_time_panel, self.ui_collect_panel, self.ui_collect_bars,
         self.ui_collect_icons) = self._load_ui_assets()
        self._sprite_files = self._index_sprite_files()
        self.item_base = {}
        self.item_scaled = {}
        # pygame-ce 2.5.7's ARM smoothscaler Bus Errors on very small RGBA
        # targets (the 100x100 -> 3x3 cache entry is enough to reproduce it
        # on the Pi 3).  These sprites move quickly and the result is cached,
        # so the safe scaler is visually adequate on Pi; desktop keeps the
        # higher-quality filter.
        self._item_scaler = (
            pygame.transform.scale
            if _is_raspberry_pi()
            else pygame.transform.smoothscale
        )
        item_kinds = (*GOOD_VALUES, *BAD_KINDS)
        for kind_index, kind in enumerate(item_kinds):
            shadow = self._load_item(kind, "shadow")
            glow = self._load_item(kind, "glow", fallback=shadow)
            self.item_base[kind] = {"shadow": shadow, "glow": glow}
            self.item_scaled[kind] = {
                variant: [
                    self._scale_item(sprite, self._item_cache_size(i))
                    for i in range(self.STEPS)
                ]
                for variant, sprite in self.item_base[kind].items()
            }
            report(
                0.20 + 0.80 * (kind_index + 1) / len(item_kinds),
                f"SPRITES {kind_index + 1}/{len(item_kinds)}",
            )

    @classmethod
    def _item_cache_size(cls, index):
        return ITEM_MIN_PIXELS + index

    def _scale_item(self, sprite, target_size):
        longest = max(sprite.get_width(), sprite.get_height())
        width = max(1, round(sprite.get_width() * target_size / longest))
        height = max(1, round(sprite.get_height() * target_size / longest))
        return self._item_scaler(sprite, (width, height))

    @staticmethod
    def _index_sprite_files():
        root = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sprites"
        files = {}
        for folder in ("Food", "Junk"):
            directory = root / folder
            if not directory.is_dir():
                continue
            for path in directory.glob("*.png"):
                # Lower-case lookup is deliberate: development happens on
                # case-insensitive Windows, deployment on case-sensitive Linux.
                files[(folder.lower(), path.stem.lower())] = path
        return files

    def _load_item(self, kind, variant, fallback=None):
        folder, stem = ITEM_SPRITE_LAYOUT[kind]
        # The requested convention is _Sw. The currently supplied Food set
        # uses _Sh for the same shadowed variant, so accept it as an alias.
        suffixes = ("sw", "sh") if variant == "shadow" else ("gl",)
        path = next((
            self._sprite_files.get((folder.lower(), f"{stem}_{suffix}".lower()))
            for suffix in suffixes
            if self._sprite_files.get((folder.lower(), f"{stem}_{suffix}".lower()))
        ), None)
        if path is not None:
            try:
                sprite = pygame.image.load(str(path)).convert_alpha()
                if sprite.get_size() != (100, 100):
                    print(f"[munchies] sprite must be 100x100 ({path.name}: {sprite.get_size()})")
                return sprite
            except pygame.error as exc:
                print(f"[munchies] sprite load failed ({kind}/{variant}): {exc}")
        else:
            print(f"[munchies] sprite missing ({kind}/{variant})")
        return fallback.copy() if fallback is not None else self._make_item(kind)

    @classmethod
    def _load_ufo_assets(cls):
        root = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sprites" / "UFO"

        def load(path, fallback=None):
            if path.is_file():
                try:
                    return pygame.image.load(str(path)).convert_alpha()
                except pygame.error as exc:
                    print(f"[munchies] UFO asset load failed ({path.name}): {exc}")
            if fallback is not None:
                return fallback
            return pygame.Surface((1, 1), pygame.SRCALPHA).convert_alpha()

        body = load(root / "ufo.png", cls._make_ufo(0))
        shadow = load(
            root / "ufo_SH.png",
            pygame.Surface((body.get_width(), 1), pygame.SRCALPHA).convert_alpha(),
        )
        light_frames = [load(path) for path in sorted((root / "LightSQ").glob("ufofeny_*.png"))]
        if not light_frames:
            light_frames = [
                pygame.Surface((body.get_width(), 1), pygame.SRCALPHA).convert_alpha()
            ]
        return body, shadow, light_frames

    @classmethod
    def _load_ui_assets(cls):
        root = Path(__file__).resolve().parent / "assets" / "Minigame"
        ui_root = root / "UI"

        def load(name, fallback_size):
            # Keep the legacy root as a fallback so older Pi deployments do
            # not lose their HUD while the reorganised asset tree is copied.
            paths = (ui_root / name, root / name)
            path = next((candidate for candidate in paths if candidate.is_file()), None)
            if path is not None:
                try:
                    return pygame.image.load(str(path)).convert_alpha()
                except pygame.error as exc:
                    print(f"[munchies] UI asset load failed ({name}): {exc}")
            print(f"[munchies] UI asset missing: {name}")
            return pygame.Surface(fallback_size, pygame.SRCALPHA).convert_alpha()

        def load_collect(path, fallback_size):
            if path.is_file():
                try:
                    surface = pygame.image.load(str(path)).convert_alpha()
                    if surface.get_size() != fallback_size:
                        print(
                            f"[munchies] Collect asset should be "
                            f"{fallback_size[0]}x{fallback_size[1]} "
                            f"({path.name}: {surface.get_size()})"
                        )
                        surface = pygame.transform.smoothscale(surface, fallback_size).convert_alpha()
                    return surface
                except pygame.error as exc:
                    print(f"[munchies] Collect asset load failed ({path.name}): {exc}")
            print(f"[munchies] Collect asset missing: {path.name}")
            return pygame.Surface(fallback_size, pygame.SRCALPHA).convert_alpha()

        frame = load("Frame.png", (WIDTH, HEIGHT))
        if frame.get_size() != (WIDTH, HEIGHT):
            print(f"[munchies] Frame.png should be {WIDTH}x{HEIGHT}: {frame.get_size()}")
            frame = pygame.transform.smoothscale(frame, (WIDTH, HEIGHT)).convert_alpha()

        # The centre of Frame.png is transparent. Split it into narrow edge
        # strips once so the Pi blends about 47k pixels instead of 307k every
        # game frame, while preserving the supplied artwork pixel-for-pixel.
        top_h, bottom_y, side_w = 24, 440, 8
        frame_parts = (
            (frame.subsurface((0, 0, WIDTH, top_h)).copy().convert_alpha(), (0, 0)),
            (frame.subsurface((0, bottom_y, WIDTH, HEIGHT - bottom_y)).copy().convert_alpha(),
             (0, bottom_y)),
            (frame.subsurface((0, top_h, side_w, bottom_y - top_h)).copy().convert_alpha(),
             (0, top_h)),
            (frame.subsurface((WIDTH - side_w, top_h, side_w, bottom_y - top_h)).copy().convert_alpha(),
             (WIDTH - side_w, top_h)),
        )
        collect_root = ui_root / "Collect"
        collect_panel = load_collect(collect_root / "Collect_00000.png", (250, 44))
        collect_bars = tuple(
            load_collect(collect_root / f"munchiebar_{index:05d}.png", (250, 44))
            for index in range(COLLECT_TARGET)
        )
        food_root = root / "Sprites" / "Food"
        food_paths = {
            path.stem.lower(): path for path in food_root.glob("*.png")
            if "_" not in path.stem
        }
        collect_icons = {}
        for kind in GOOD_VALUES:
            stem = ITEM_SPRITE_LAYOUT[kind][1].lower()
            path = food_paths.get(stem)
            try:
                icon = pygame.image.load(str(path)).convert_alpha() if path else cls._make_item(kind)
                if path and icon.get_size() != (300, 300):
                    print(
                        f"[munchies] Collect icon should be 300x300 "
                        f"({path.name}: {icon.get_size()})"
                    )
                collect_icons[kind] = pygame.transform.smoothscale(icon, (42, 42)).convert_alpha()
            except pygame.error as exc:
                print(f"[munchies] Collect icon load failed ({kind}): {exc}")
                collect_icons[kind] = pygame.transform.smoothscale(
                    cls._make_item(kind), (42, 42)
                ).convert_alpha()

        return (
            frame_parts,
            load("Muncies_logo.png", (220, 74)),
            load("Score.png", (134, 63)),
            load("Time.png", (118, 61)),
            collect_panel,
            collect_bars,
            collect_icons,
        )

    @staticmethod
    def _make_ufo(frame):
        surf = pygame.Surface((146, 72), pygame.SRCALPHA)
        bob = frame % 2
        pygame.draw.ellipse(surf, (7, 10, 12), (5, 25 + bob, 136, 42))
        pygame.draw.ellipse(surf, (65, 75, 82), (9, 23 + bob, 128, 37))
        pygame.draw.ellipse(surf, (109, 255, 43), (13, 42 + bob, 120, 15))
        pygame.draw.ellipse(surf, (9, 22, 27), (43, 7 + bob, 61, 43))
        pygame.draw.ellipse(surf, (70, 174, 134), (47, 9 + bob, 53, 35))
        pygame.draw.circle(surf, (116, 255, 65), (74, 27 + bob), 11)
        pygame.draw.ellipse(surf, (4, 16, 12), (67, 23 + bob, 5, 7))
        pygame.draw.ellipse(surf, (4, 16, 12), (78, 23 + bob, 5, 7))
        return surf

    @staticmethod
    def _make_item(kind):
        s = pygame.Surface((58, 58), pygame.SRCALPHA)
        good = kind in GOOD_VALUES
        edge, fill = (20, 8, 7), ((250, 171, 45) if good else (100, 108, 120))
        if kind == "pizza":
            pygame.draw.polygon(s, edge, ((8, 8), (52, 16), (17, 52)))
            pygame.draw.polygon(s, (255, 207, 79), ((12, 12), (48, 18), (18, 47)))
            for p in ((25, 20), (35, 27), (21, 34)): pygame.draw.circle(s, (201, 51, 30), p, 4)
        elif kind in ("burrito", "taco"):
            pygame.draw.ellipse(s, edge, (5, 14, 49, 31)); pygame.draw.ellipse(s, (229, 182, 93), (8, 17, 43, 25))
            pygame.draw.circle(s, (71, 145, 54), (17, 23), 4)
        elif kind == "chips":
            pygame.draw.polygon(s, edge, ((13, 7), (47, 10), (50, 50), (9, 47)))
            pygame.draw.polygon(s, (219, 55, 31), ((16, 11), (44, 13), (46, 46), (13, 44)))
            pygame.draw.circle(s, (255, 214, 44), (30, 29), 9)
        elif kind == "burger":
            pygame.draw.ellipse(s, edge, (5, 10, 50, 40)); pygame.draw.ellipse(s, (244, 174, 52), (8, 12, 44, 21))
            pygame.draw.rect(s, (72, 145, 45), (10, 29, 40, 7)); pygame.draw.rect(s, (105, 48, 25), (11, 35, 38, 10))
        elif kind == "donut":
            pygame.draw.circle(s, edge, (29, 29), 24); pygame.draw.circle(s, (238, 102, 181), (29, 29), 20); pygame.draw.circle(s, (61, 29, 25), (29, 29), 7)
        elif kind == "soda":
            pygame.draw.rect(s, edge, (15, 7, 28, 47)); pygame.draw.rect(s, (213, 47, 39), (18, 10, 22, 41)); pygame.draw.line(s, (250, 241, 225), (20, 42), (39, 18), 5)
        else:
            pygame.draw.circle(s, edge, (29, 29), 24); pygame.draw.circle(s, fill, (29, 29), 19)
            pygame.draw.line(s, (45, 45, 50), (17, 17), (42, 41), 6)
        return s


class CharacterPortraits:
    """Two corner portraits with cheap blink and alternating mouth animation."""

    def __init__(self, rng):
        self.rng = rng
        root = (Path(__file__).resolve().parent / "assets" / "Minigame" /
                "Sprites" / "Characters")
        # Match the supplied reference: Chong (headband/blue vest) stands on
        # the left, Cheech (red beanie/suspenders) on the right.
        directories = {
            "Cheech": root / "Cheech" / "Cheech Lipsync",
            "Chong": root / "Chong" / "Chong Lipsync",
        }
        self.frames = {
            speaker: self._load_frames(speaker, directory)
            for speaker, directory in directories.items()
        }
        self.reset()

    def reset(self):
        """Reset animation state while retaining decoded portrait frames."""
        self.frame_index = {speaker: 0 for speaker in self.frames}
        self.frame_timer = {speaker: 0.0 for speaker in self.frames}
        self.blink_wait = {
            speaker: self.rng.uniform(1.8, 3.3) for speaker in self.frames
        }
        self.last_mouth = {speaker: None for speaker in self.frames}
        self.was_speaking = {speaker: False for speaker in self.frames}

    @classmethod
    def _load_frames(cls, speaker, directory):
        frames = []
        for path in sorted(directory.glob("*.png")) if directory.is_dir() else ():
            try:
                frames.append(pygame.image.load(str(path)).convert_alpha())
            except pygame.error as exc:
                print(f"[munchies] portrait frame unavailable ({path.name}): {exc}")
        if not frames:
            frames = [pygame.Surface((1, 1), pygame.SRCALPHA).convert_alpha()]
        if len(frames) > 1:
            base_pixels = pygame.image.tobytes(frames[0], "RGBA")
            if pygame.image.tobytes(frames[1], "RGBA") == base_pixels:
                frames[1] = cls._make_blink_frame(speaker, frames[0])
            # Do not let accidental duplicate base frames become "mouth"
            # poses. Cheech currently ships with an extra identical 00002.
            frames = frames[:2] + [
                frame for frame in frames[2:]
                if pygame.image.tobytes(frame, "RGBA") != base_pixels
            ]
        return frames

    @staticmethod
    def _make_blink_frame(speaker, base):
        blink = base.copy()
        if speaker == "Cheech" and base.get_size() == (124, 152):
            skin = (238, 143, 55, 255)
            eyelid = (42, 22, 8, 255)
            for rect, start, end in (
                (pygame.Rect(21, 46, 15, 11), (22, 52), (34, 52)),
                (pygame.Rect(44, 47, 15, 11), (45, 53), (57, 53)),
            ):
                pygame.draw.ellipse(blink, skin, rect)
                pygame.draw.line(blink, eyelid, start, end, 2)
        return blink.convert_alpha()

    def _next_mouth(self, speaker):
        mouth_indices = list(range(2, len(self.frames[speaker])))
        if not mouth_indices:
            return 0
        previous = self.last_mouth[speaker]
        choices = [index for index in mouth_indices if index != previous] or mouth_indices
        chosen = self.rng.choice(choices)
        self.last_mouth[speaker] = chosen
        return chosen

    def update(self, dt, active_speaker):
        for speaker, frames in self.frames.items():
            speaking = speaker == active_speaker and len(frames) > 2
            if speaking:
                if not self.was_speaking[speaker]:
                    self.frame_index[speaker] = 0
                    self.frame_timer[speaker] = self.rng.uniform(.045, .075)
                self.frame_timer[speaker] -= dt
                if self.frame_timer[speaker] <= 0.0:
                    if self.frame_index[speaker] == 0:
                        self.frame_index[speaker] = self._next_mouth(speaker)
                        self.frame_timer[speaker] = self.rng.uniform(.070, .115)
                    else:
                        # Every mouth pose is separated by the base frame:
                        # idle -> random mouth -> idle -> another mouth.
                        self.frame_index[speaker] = 0
                        self.frame_timer[speaker] = self.rng.uniform(.045, .080)
                self.blink_wait[speaker] = self.rng.uniform(1.8, 3.3)
            else:
                if self.was_speaking[speaker] or self.frame_index[speaker] >= 2:
                    self.frame_index[speaker] = 0
                    self.frame_timer[speaker] = 0.0
                self.blink_wait[speaker] -= dt
                if self.frame_index[speaker] == 1:
                    self.frame_timer[speaker] -= dt
                    if self.frame_timer[speaker] <= 0.0:
                        self.frame_index[speaker] = 0
                        self.blink_wait[speaker] = self.rng.uniform(1.8, 3.3)
                elif len(frames) > 1 and self.blink_wait[speaker] <= 0.0:
                    self.frame_index[speaker] = 1
                    self.frame_timer[speaker] = self.rng.uniform(.16, .24)
            self.was_speaking[speaker] = speaking

    def draw(self, screen):
        chong = self.frames["Chong"][self.frame_index["Chong"]]
        cheech = self.frames["Cheech"][self.frame_index["Cheech"]]
        # Direct display-surface blits avoid the full-screen SRCALPHA
        # composition pattern that causes a Bus Error on the 32-bit Pi.
        screen.blit(chong, chong.get_rect(bottomleft=(0, HEIGHT)))
        screen.blit(cheech, cheech.get_rect(bottomright=(WIDTH, HEIGHT)))


@dataclass(frozen=True)
class VoiceClip:
    line_id: str
    speaker: Optional[str]
    item_kind: Optional[str]
    variant_key: str
    sound: pygame.mixer.Sound


class CharacterVoiceDirector:
    """Priority/cooldown-aware character voice player driven by XLSX IDs."""

    # A brutal run can now reach roughly three minutes. Retain the anti-spam
    # cooldowns, but keep enough dialogue budget that the extended skill-run
    # does not collapse into silence long before its soft ceiling.
    MAX_LINES = 90
    SAME_LINE_LOCKOUT = 24.0
    BANTER_PAIRS = (
        ("VL116", "VL120"), ("VL117", "VL121"),
    )

    def __init__(self, portraits, rng, progress_callback=None):
        self.portraits, self.rng = portraits, rng
        self.clips = {}
        self.clock = 0.0
        self.voice_silence_elapsed = 0.0
        self.cooldown_until = 0.0
        self.played_count = 0
        self.last_played_at = {}
        self.last_speaker = None
        self.active_speaker = None
        self.active_line_id = None
        self.active_item_kind = None
        self.active_priority = 0
        self.channel = None
        self.voice_channel = None
        self.queued_reply = None
        self._ducked_music_volume = None
        self._load_clips(progress_callback)

    def _load_clips(self, progress_callback=None):
        directory = (Path(__file__).resolve().parent / "assets" / "Minigame" /
                     "Sound" / "Voices")
        paths = sorted(path for path in directory.iterdir()
                       if path.is_file() and path.suffix.lower() in (".wav", ".ogg")) \
            if directory.is_dir() else []
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=MIXER_BUFFER_SAMPLES)
            pygame.mixer.set_num_channels(max(16, pygame.mixer.get_num_channels()))
            # Keep channel 0 exclusively for dialogue. Sound.play() effects
            # cannot steal it, so a pickup burst never cuts a voice line off.
            pygame.mixer.set_reserved(1)
            self.voice_channel = pygame.mixer.Channel(0)
            self.voice_channel.set_volume(1.0, 1.0)
            for path_index, path in enumerate(paths):
                parts = path.stem.split("_")
                # Accept both canonical VL067 and the supplied VL67 spelling.
                # Normalising here keeps the event tables consistently 3-digit.
                match = re.fullmatch(r"VL(\d{1,3})", parts[0], flags=re.IGNORECASE)
                if match is None:
                    continue
                line_id = f"VL{int(match.group(1)):03d}"
                attributes = parts[1:]
                speaker_aliases = {
                    "cheech": "Cheech",
                    "chech": "Cheech",
                    "chong": "Chong",
                }
                speaker = next(
                    (speaker_aliases[part.lower()] for part in attributes
                     if part.lower() in speaker_aliases),
                    None,
                )
                item_parts = [
                    part for part in attributes
                    if part.lower() not in speaker_aliases
                ]
                item_kind = _normalize_voice_item("".join(item_parts))
                if speaker is None:
                    speaker = _fixed_voice_speaker(int(line_id[2:]))
                sound = pygame.mixer.Sound(str(path))
                sound.set_volume(.88)
                self.clips.setdefault(line_id, []).append(
                    VoiceClip(line_id, speaker, item_kind, path.stem.lower(), sound)
                )
                if progress_callback is not None and (
                        path_index % 8 == 7 or path_index + 1 == len(paths)):
                    progress_callback(
                        (path_index + 1) / max(1, len(paths)),
                        f"VOICES {path_index + 1}/{len(paths)}",
                    )
            if self.clips:
                variants = sum(len(clips) for clips in self.clips.values())
                print(f"[munchies] character voices loaded: {variants} files / {len(self.clips)} IDs")
        except pygame.error as exc:
            self.clips.clear()
            print(f"[munchies] character voices unavailable: {exc}")

    def _sync_finished_channel(self):
        if self.channel is not None and not self.channel.get_busy():
            self.channel = None
            self.active_speaker = None
            self.active_line_id = None
            self.active_item_kind = None
            self.active_priority = 0

    @property
    def busy(self):
        self._sync_finished_channel()
        return self.channel is not None or self.queued_reply is not None

    def _pool_clips(self, line_ids, item_kind=None):
        candidates = [clip for line_id in line_ids for clip in self.clips.get(line_id, ())]
        if item_kind is not None:
            normalized_item = _normalize_voice_item(item_kind)
            exact = [clip for clip in candidates if clip.item_kind == normalized_item]
            generic = [clip for clip in candidates if clip.item_kind is None]
            return exact or generic
        generic = [clip for clip in candidates if clip.item_kind is None]
        return generic or candidates

    def has_clips(self, event_key, item_kind=None):
        return bool(self._pool_clips(VOICE_EVENT_IDS[event_key], item_kind))

    def _candidate_clips(self, line_ids, priority, item_kind=None):
        candidates = self._pool_clips(line_ids, item_kind)
        unlocked = [
            clip for clip in candidates
            if self.clock - self.last_played_at.get(clip.variant_key, -1e9)
            >= self.SAME_LINE_LOCKOUT
        ]
        if unlocked:
            candidates = unlocked
        elif priority < 3:
            return []
        # Prefer alternating speakers when both recorded variants exist.
        wanted = "Chong" if self.last_speaker == "Cheech" else "Cheech"
        preferred = [clip for clip in candidates if clip.speaker == wanted]
        return preferred or candidates

    def _duck_music(self):
        try:
            if self._ducked_music_volume is None and pygame.mixer.music.get_busy():
                self._ducked_music_volume = pygame.mixer.music.get_volume()
                pygame.mixer.music.set_volume(min(.34, self._ducked_music_volume))
        except pygame.error:
            pass

    def _restore_music(self):
        if self._ducked_music_volume is None:
            return
        try:
            pygame.mixer.music.set_volume(self._ducked_music_volume)
        except pygame.error:
            pass
        self._ducked_music_volume = None

    def _play_clip(self, clip, cooldown, priority):
        try:
            channel = self.voice_channel or pygame.mixer.find_channel(True)
            if channel is None:
                return False
            speaker = clip.speaker
            if speaker is None:
                speaker = "Chong" if self.last_speaker == "Cheech" else "Cheech"
            channel.play(clip.sound)
            self.channel = channel
            self.active_speaker = speaker
            self.active_line_id = clip.line_id
            self.active_item_kind = clip.item_kind
            self.active_priority = priority
            self.last_speaker = speaker
            self.last_played_at[clip.variant_key] = self.clock
            self.voice_silence_elapsed = 0.0
            self.played_count += 1
            self.cooldown_until = max(
                self.cooldown_until,
                self.clock + clip.sound.get_length() + cooldown,
            )
            self._duck_music()
            return True
        except pygame.error as exc:
            print(f"[munchies] voice playback failed ({clip.line_id}): {exc}")
            return False

    def trigger(self, event_key, item_kind=None, ignore_cooldown=False,
                chance_already_passed=False):
        rule = VOICE_RULES[event_key]
        candidates = self._candidate_clips(
            VOICE_EVENT_IDS[event_key], rule.priority, item_kind
        )
        if not candidates:
            return False
        if not chance_already_passed and self.rng.random() > rule.chance:
            return False
        if self.busy:
            if not rule.interrupt:
                return False
            blocking_priority = (
                self.active_priority
                if self.channel is not None
                else self.queued_reply[2]
            )
            if rule.priority < blocking_priority:
                return False
            if self.channel is not None:
                self.channel.stop()
            self.channel = None
            self.active_speaker = None
            self.active_line_id = None
            self.active_item_kind = None
            self.active_priority = 0
            self.queued_reply = None
        if rule.priority < 3:
            if ((not ignore_cooldown and self.clock < self.cooldown_until)
                    or self.played_count >= self.MAX_LINES):
                return False
        return self._play_clip(
            self.rng.choice(candidates), rule.cooldown, rule.priority
        )

    def trigger_ending(self, success):
        event_key = "mission_complete" if success else "mission_failed"
        if not self.trigger(event_key):
            return False
        # Keep the return-to-pinball line out of the main random pool and
        # always append it after the result, preferably with the other
        # character. The paired exchange gives the summary screen its payoff.
        tag_candidates = self._candidate_clips(
            VOICE_ENDING_TAG_IDS[event_key], priority=3
        )
        if tag_candidates:
            self.queued_reply = [.18, self.rng.choice(tag_candidates), 3]
        return True

    def trigger_banter(self):
        if self.busy or self.clock < self.cooldown_until or self.played_count > self.MAX_LINES - 2:
            return False
        if self.rng.random() > BANTER_CHANCE:
            return False
        available_pairs = [
            pair for pair in self.BANTER_PAIRS
            if self._candidate_clips((pair[0],), 1)
            and self._candidate_clips((pair[1],), 1)
        ]
        if not available_pairs:
            return False
        first_id, reply_id = self.rng.choice(available_pairs)
        first = self.rng.choice(self._candidate_clips((first_id,), 1))
        reply = self.rng.choice(self._candidate_clips((reply_id,), 1))
        if not self._play_clip(first, 0.0, 1):
            return False
        self.queued_reply = [.2, reply, 1]
        return True

    def update(self, dt):
        self.clock += dt
        self._sync_finished_channel()
        if self.channel is None and self.queued_reply is not None:
            self.queued_reply[0] -= dt
            if self.queued_reply[0] <= 0.0:
                _, reply, priority = self.queued_reply
                self.queued_reply = None
                self._play_clip(reply, 8.0, priority)
        if self.channel is None and self.queued_reply is None:
            self.voice_silence_elapsed += dt
            self._restore_music()
        else:
            self.voice_silence_elapsed = 0.0
        self.portraits.update(dt, self.active_speaker)

    def stop(self, release_reservation=True):
        if self.channel is not None:
            self.channel.stop()
        self.channel = None
        self.active_speaker = None
        self.active_line_id = None
        self.active_item_kind = None
        self.active_priority = 0
        self.queued_reply = None
        self.voice_silence_elapsed = 0.0
        self._restore_music()
        if release_reservation:
            try:
                pygame.mixer.set_reserved(0)
            except pygame.error:
                pass

    def reset(self):
        """Re-arm dialogue state without reloading the voice assets."""
        self.stop(release_reservation=False)
        self.clock = 0.0
        self.voice_silence_elapsed = 0.0
        self.cooldown_until = 0.0
        self.played_count = 0
        self.last_played_at.clear()
        self.last_speaker = None
        self.active_speaker = None
        self.active_line_id = None
        self.active_item_kind = None
        self.active_priority = 0
        self.channel = None
        self.queued_reply = None
        self._ducked_music_volume = None
        self.portraits.reset()
        try:
            pygame.mixer.set_reserved(1)
            self.voice_channel = pygame.mixer.Channel(0)
            self.voice_channel.set_volume(1.0, 1.0)
        except pygame.error:
            self.voice_channel = None


@dataclass
class RoadItem:
    kind: str
    world_x: float
    depth: float = 0.0
    optional_choice: bool = False
    captured: bool = False
    travelled_frames: float = 0.0
    visual_pixels: float = 0.0
    capture_elapsed: float = 0.0
    capture_start_x: float = 0.0
    capture_start_y: float = 0.0
    capture_start_pixels: float = 0.0
    capture_x: float = 0.0
    capture_y: float = 0.0
    voice_spawn_checked: bool = False

    @property
    def good(self): return self.kind in GOOD_VALUES

    def calibration_diameter(self):
        frame = max(0.0, min(TRACK_FRAME_COUNT, self.travelled_frames))
        return CALIBRATION_DIAMETER_NUMERATOR / (CALIBRATION_VANISH_FRAME - frame)

    def sprite_pixels(self):
        return self.calibration_diameter() * ITEM_WORLD_SIZE_METERS

    def projected_depth(self):
        near = CALIBRATION_DIAMETER_NUMERATOR / (CALIBRATION_VANISH_FRAME - ITEM_APPROACH_FRAMES)
        far = CALIBRATION_DIAMETER_NUMERATOR / CALIBRATION_VANISH_FRAME
        return max(0.0, min(1.0, (self.calibration_diameter() - far) / (near - far)))

    def position(self):
        if self.captured:
            return self.capture_x, self.capture_y
        diameter = self.calibration_diameter()
        y = CALIBRATION_HORIZON_Y + CALIBRATION_Y_PER_DIAMETER * diameter + ITEM_Y_OFFSET
        # Exact perspective lane projection: every fixed world-X line passes
        # through the calibrated vanishing point. This removes the sideways
        # "creep" caused by the old arbitrary 42..247 road-width mapping.
        calibrated_vanish_y = CALIBRATION_HORIZON_Y + ITEM_Y_OFFSET
        perspective = (y - calibrated_vanish_y) / (402 - calibrated_vanish_y)
        road_half = ROAD_HALF_WIDTH_AT_UFO * max(0.0, perspective)
        return WIDTH / 2 + self.world_x * road_half, y


class PlayerUFO:
    def __init__(self):
        self.x, self.y = WIDTH / 2, 402
        self.left = self.right = self.beam = False
        self.stun = 0.0
        self._left_pulse = self._right_pulse = self._beam_pulse = 0.0

    def update(self, dt):
        self.stun = max(0.0, self.stun - dt)
        self._left_pulse = max(0.0, self._left_pulse - dt)
        self._right_pulse = max(0.0, self._right_pulse - dt)
        self._beam_pulse = max(0.0, self._beam_pulse - dt)
        if not self._left_pulse and self.left == "pulse": self.left = False
        if not self._right_pulse and self.right == "pulse": self.right = False
        if not self._beam_pulse and self.beam == "pulse": self.beam = False
        direction = int(bool(self.right)) - int(bool(self.left))
        self.x = max(ROAD_PLAYER_LEFT, min(ROAD_PLAYER_RIGHT,
                                           self.x + direction * 245 * dt * (0.35 if self.stun else 1)))

    @staticmethod
    def _road_parallel_x(bottom_x, y):
        """Project a road-parallel line through the common vanishing point."""
        ratio = (y - ROAD_VANISH_Y) / (BEAM_BASE_Y - ROAD_VANISH_Y)
        return ROAD_VANISH_X + (bottom_x - ROAD_VANISH_X) * ratio

    def beam_polygon(self):
        bottom_left = max(ROAD_PLAYER_LEFT, self.x - BEAM_HALF_WIDTH)
        bottom_right = min(ROAD_PLAYER_RIGHT, self.x + BEAM_HALF_WIDTH)
        top_left = self._road_parallel_x(bottom_left, BEAM_TOP_Y)
        top_right = self._road_parallel_x(bottom_right, BEAM_TOP_Y)
        return ((top_left, BEAM_TOP_Y), (top_right, BEAM_TOP_Y),
                (bottom_right, self.y + 5), (bottom_left, self.y + 5))

    def beam_contains(self, x, y, margin=0.0):
        if not self.beam or y < BEAM_TOP_Y - BEAM_CAPTURE_LEAD or y > self.y + 5 + margin:
            return False
        polygon = self.beam_polygon()
        t = max(0.0, min(1.0, (y - BEAM_TOP_Y) / (self.y + 5 - BEAM_TOP_Y)))
        left = polygon[0][0] + (polygon[3][0] - polygon[0][0]) * t
        right = polygon[1][0] + (polygon[2][0] - polygon[1][0]) * t
        return left - margin <= x <= right + margin


class HUD:
    """Top overlay composed from the supplied score/time/logo artwork."""

    def __init__(self, logo, score_panel, time_panel, collect_panel,
                 collect_bars, collect_icons):
        self.logo, self.score_panel, self.time_panel = logo, score_panel, time_panel
        self.collect_pos = ((WIDTH - collect_panel.get_width()) // 2, 78)
        # Match the reference composition: the counters flank the title
        # instead of hugging the cabinet frame's outer corners.
        self.score_pos = (61, 8)
        self.time_pos = (445, 9)
        self.logo_pos = ((WIDTH - logo.get_width()) // 2, 3)
        self.score_center_x = self.score_pos[0] + score_panel.get_width() // 2
        self.time_center_x = self.time_pos[0] + time_panel.get_width() // 2
        self.score_fonts = tuple(_font(size) for size in (22, 20, 18, 16, 14))
        self.time_font = _font(25)
        self.combo_font, self.flash_font = _font(16), _font(23)
        self.collect_font = _font(16)
        self._score_key = self._time_key = None
        self._score_surface = self._time_surface = None
        self._combo_key = None
        self._combo_surface = None
        self._collect_cards = self._build_collect_cards(
            collect_panel, collect_bars, collect_icons
        )
        self._flash_surfaces = {
            "+1 SEC": _text(self.flash_font, "+1 SEC", (120, 255, 60), width=3).convert_alpha(),
            "+5 SEC": _text(self.flash_font, "+5 SEC", (255, 235, 70), width=3).convert_alpha(),
            "-2 SEC": _text(self.flash_font, "-2 SEC", (255, 78, 90), width=3).convert_alpha(),
        }

    def _build_collect_cards(self, panel, bars, icons):
        cards = {}
        for kind, icon in icons.items():
            for count in range(1, COLLECT_TARGET + 1):
                counter = _text(
                    self.collect_font,
                    f"{count}/{COLLECT_TARGET}",
                    (148, 255, 63),
                    width=2,
                ).convert_alpha()
                # Do not precompose these layers onto an intermediate alpha
                # surface.  pygame-ce's 32-bit ARM blitter can Bus Error on
                # repeated SRCALPHA -> SRCALPHA compositions.  Shared layer
                # tuples use less memory and are drawn directly to the display.
                cards[(kind, count)] = (
                    panel, bars[count - 1], icon, counter
                )
        return cards

    @staticmethod
    def _clock_text(remaining):
        seconds = max(0, math.ceil(remaining - 1e-6))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def flash_surface(self, value):
        surface = self._flash_surfaces.get(value)
        if surface is None and value:
            colour = (120, 255, 60) if value.startswith("+") else (255, 78, 90)
            surface = _text(self.flash_font, value, colour, width=3).convert_alpha()
            self._flash_surfaces[value] = surface
        return surface

    def draw(self, screen, remaining, score, combo, streak=0,
             combo_remaining=0.0, collect_kind=None, collect_count=0,
             collect_remaining=0.0):
        if collect_kind and collect_remaining > 0.0:
            count = max(1, min(COLLECT_TARGET, collect_count))
            card = self._collect_cards.get((collect_kind, count))
            if card is not None:
                panel, bar, icon, counter = card
                x, y = self.collect_pos
                screen.blit(panel, (x, y))
                screen.blit(bar, (x, y))
                screen.blit(icon, (x + 3, y + 1))
                screen.blit(counter, counter.get_rect(center=(x + 125, y + 30)))
        screen.blit(self.logo, self.logo_pos)
        screen.blit(self.score_panel, self.score_pos)
        screen.blit(self.time_panel, self.time_pos)

        score_key = f"{score:,}"
        if score_key != self._score_key:
            self._score_key = score_key
            max_score_width = self.score_panel.get_width() - 14
            for font in self.score_fonts:
                candidate = _text(
                    font, score_key, (174, 255, 66), width=1).convert_alpha()
                self._score_surface = candidate
                if candidate.get_width() <= max_score_width:
                    break
        screen.blit(
            self._score_surface,
            self._score_surface.get_rect(center=(self.score_center_x, self.score_pos[1] + 43)),
        )

        time_key = self._clock_text(remaining)
        if time_key != self._time_key:
            self._time_key = time_key
            self._time_surface = _text(
                self.time_font, time_key, (174, 255, 66), width=1).convert_alpha()
        screen.blit(
            self._time_surface,
            self._time_surface.get_rect(center=(self.time_center_x, self.time_pos[1] + 42)),
        )

        if combo > 1:
            combo_key = f"COMBO x{combo}  {streak} HIT"
            if combo_key != self._combo_key:
                self._combo_key = combo_key
                self._combo_surface = _text(
                    self.combo_font, combo_key, (255, 91, 213), width=2).convert_alpha()
            screen.blit(
                self._combo_surface,
                self._combo_surface.get_rect(center=(self.score_center_x, 82)),
            )
            meter = pygame.Rect(self.score_center_x - 58, 98, 116, 6)
            pygame.draw.rect(screen, (44, 12, 65), meter, border_radius=3)
            ratio = max(0.0, min(1.0, combo_remaining / COMBO_TIMEOUT_SECONDS))
            if ratio > 0.0:
                fill = meter.copy()
                fill.width = max(2, round(meter.width * ratio))
                pygame.draw.rect(screen, (255, 91, 213), fill, border_radius=3)


class ResultsOverlay:
    """Vegeredmeny-overlay a minijatek utan.

    FIGYELEM (32 bites ARM / Pi 3B+): ez a kepernyo NEM epithet egy kozos,
    teljes kepernyos SRCALPHA "layer"-t, amire rakomponalja a szovegeket.
    A szeles SRCALPHA->SRCALPHA blit `pygame_parachute: Bus Error`-ral
    megoli a processzt ezen a gepen (armv7l, pygame-ce 2.5.7 / SDL 2.32.4) -
    pontosan ez omlasztotta ossze a GUI-t a minijatek vegen. Helyette minden
    elem KOZVETLENUL a kepernyore (a display surface-re) kerul: a rajzolo
    primitivek (draw.rect/line) biztonsagosak, a szoveg-surface-ok pedig
    kicsik, es cache-elve vannak, hogy frame-enkent csak blit maradjon.
    Ugyanez a minta mukodik mar a score_gui.py PRESS_START/Special Thanks
    kepernyoin is.
    """

    ROW_Y = (151, 188, 222, 255)
    STAT_Y = (357, 373, 389, 406, 422)
    ROW_RIGHT = 528
    TOTAL_RIGHT = 528
    STAT_RIGHT = 454

    def __init__(self):
        self.background = self._load_background()
        self.value_fonts = tuple(_font(size) for size in (31, 29, 27, 25, 23, 21, 19))
        self.total_fonts = tuple(_font(size) for size in (50, 46, 42, 38, 34, 30))
        self.stat_fonts = tuple(_font(size) for size in (17, 16, 15, 14, 13))
        self._cache_key = None
        self._cached_text = None

    @staticmethod
    def _load_background():
        path = (Path(__file__).resolve().parent / "assets" / "Minigame" /
                "UI" / "SumScreen.png")
        fallback = pygame.Surface((WIDTH, HEIGHT)).convert()
        fallback.fill((8, 1, 25))
        if not path.is_file():
            print("[munchies] summary background missing: UI/SumScreen.png")
            return fallback
        try:
            background = pygame.image.load(str(path)).convert()
            if background.get_size() != (WIDTH, HEIGHT):
                print(
                    f"[munchies] SumScreen.png should be {WIDTH}x{HEIGHT}: "
                    f"{background.get_size()}"
                )
                background = pygame.transform.smoothscale(
                    background, (WIDTH, HEIGHT)
                ).convert()
            return background
        except (pygame.error, OSError) as exc:
            print(f"[munchies] summary background unavailable: {exc}")
            return fallback

    @staticmethod
    def _fit_text(fonts, value, color, max_width, outline=(12, 5, 24), width=2):
        surface = None
        for font in fonts:
            surface = _text(font, value, color, outline, width)
            if surface.get_width() <= max_width:
                return surface
        if surface is not None and surface.get_width() > max_width:
            height = max(1, round(surface.get_height() * max_width / surface.get_width()))
            surface = pygame.transform.smoothscale(surface, (max_width, height))
        return surface

    def draw(self, screen, result):
        cache_key = (
            result["munchies_score"], result["combo_bonus"],
            result["collection_bonus"], result["junk_penalty"],
            result["total_bonus"], result["collected_count"],
            result["completed_food_sets"], result["best_streak"],
            result["max_combo_multiplier"], result["junk_abducted"],
        )
        if cache_key != self._cache_key:
            self._cache_key = cache_key
            junk_value = (
                f'-{result["junk_penalty"]:,}' if result["junk_penalty"] else "0"
            )
            row_values = (
                f'{result["munchies_score"]:,}',
                f'{result["combo_bonus"]:,}',
                f'{result["collection_bonus"]:,}',
                junk_value,
            )
            stats = (
                result["collected_count"],
                result["completed_food_sets"],
                result["best_streak"],
                f'x{result["max_combo_multiplier"]}',
                result["junk_abducted"],
            )
            self._cached_text = {
                "rows": [
                    self._fit_text(
                        self.value_fonts, value,
                        (255, 82, 105) if index == 3 else (250, 250, 244),
                        184,
                        (55, 3, 18) if index == 3 else (7, 20, 15),
                        2,
                    )
                    for index, value in enumerate(row_values)
                ],
                "total": self._fit_text(
                    self.total_fonts, f'{result["total_bonus"]:,}',
                    (250, 245, 255), 184, (68, 10, 112), 3,
                ),
                "stats": [
                    self._fit_text(
                        self.stat_fonts, value, (248, 241, 255), 58,
                        (21, 5, 39), 1,
                    )
                    for value in stats
                ],
            }

        cached = self._cached_text
        screen.blit(self.background, (0, 0))
        for surface, y in zip(cached["rows"], self.ROW_Y):
            screen.blit(surface, surface.get_rect(midright=(self.ROW_RIGHT, y)))
        total = cached["total"]
        screen.blit(total, total.get_rect(midright=(self.TOTAL_RIGHT, 313)))
        for surface, y in zip(cached["stats"], self.STAT_Y):
            screen.blit(surface, surface.get_rect(midright=(self.STAT_RIGHT, y)))


class MunchiesAbductionGame:
    """Embeddable stateful game. `finished` becomes true after results."""

    def __init__(self, duration=GAME_SECONDS, rng=None,
                 sound_hook: Optional[Callable[[str], None]] = None,
                 autoplay_intro=True, progress_callback=None, difficulty=0):
        self.duration, self.rng, self.sound_hook = duration, rng or random.Random(), sound_hook
        self.set_difficulty(difficulty)
        self._activated = False
        report = progress_callback or (lambda _progress, _status: None)
        self._low_power = _is_raspberry_pi()
        # ARM pygame-ce's smoothscaler crashes on the tiny first frames of
        # scale-in animations. Keep the same dimensions/easing with SDL's
        # safe nearest scaler on Pi; desktop retains smoothscale.
        self._animation_scaler = (
            pygame.transform.scale
            if self._low_power
            else pygame.transform.smoothscale
        )
        self.assets = AssetBank(
            lambda progress, status: report(progress * .58, status)
        )
        report(.61, "RESULTS SCREEN")
        self.player, self.results = PlayerUFO(), ResultsOverlay()
        report(.66, "HUD")
        self.hud = HUD(
            self.assets.ui_logo,
            self.assets.ui_score_panel,
            self.assets.ui_time_panel,
            self.assets.ui_collect_panel,
            self.assets.ui_collect_bars,
            self.assets.ui_collect_icons,
        )
        report(.72, "CHARACTERS")
        self.portraits = CharacterPortraits(self.rng)
        self.items, self.elapsed, self.spawn_timer = [], 0.0, 0.25
        self._slalom_side = self.rng.choice((-1.0, 1.0))
        self.time_left = float(duration)
        self.score = self.combo_bonus = self.collected_count = self.streak = 0
        self.munchies_score = self.junk_penalty = self.junk_abducted = 0
        self.collection_bonus = 0
        self.food_counts = {kind: 0 for kind in GOOD_VALUES}
        self.completed_food_sets = {kind: 0 for kind in GOOD_VALUES}
        self.collect_display_kind = None
        self.collect_display_count = 0
        self.collect_display_time = 0.0
        self.best_streak, self.max_combo_multiplier = 0, 1
        self.combo_time_left = 0.0
        self._pending_combo_voices = []
        self.phase, self.countdown_elapsed = "intro", 0.0
        self.intro_elapsed = 0.0
        self._intro_title_audio_started = False
        self._intro_audio_end_time = INTRO_TITLE_SECONDS
        self._intro_sounds = {}
        self._intro_channels = []
        self._countdown_sounds = {}
        self._countdown_sound_channel = None
        self._countdown_sound_number = None
        self.time_up_elapsed = 0.0
        self._time_up_second_started = False
        self._time_up_second_started_at = None
        self._time_up_beam_visible = False
        self._time_up_sounds = {}
        self._time_up_channels = []
        self._time_up_first_duration = 0.0
        self._time_up_second_duration = 0.0
        self.results_elapsed, self.finished = 0.0, False
        self.world_speed = .6
        self._background_closed = False
        self.beam_shocks = []  # (age_seconds, starting beam t)
        self.countdown_font = _font(150)
        self.countdown_label_font = _font(30)
        self.countdown_numbers = {
            value: _text(self.countdown_font, str(value), (181, 255, 74), width=5)
            for value in (3, 2, 1)
        }
        self._countdown_shade = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        self._countdown_shade.fill((8, 0, 28, 96))
        self._countdown_shade = self._countdown_shade.convert_alpha()
        self._countdown_label = _text(
            self.countdown_label_font, "GET READY!", (255, 99, 220), width=3
        ).convert_alpha()
        (self._intro_background, self._intro_title,
         self._intro_title_rect, self._intro_foreground) = self._load_intro_assets()
        (self._time_up_black, self._time_up_title,
         self._time_up_title_rect) = self._load_time_up_assets()
        # The beam only occupies a narrow horizontal strip, but previously a
        # fresh full-screen alpha surface was allocated and blended every
        # frame. Reuse it and touch/blit only the actual beam bounds.
        self._beam_layer = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA).convert_alpha()
        self._beam_dirty_rect = pygame.Rect(
            0, BEAM_TOP_Y - 14, WIDTH, BEAM_BASE_Y - BEAM_TOP_Y + 32
        )
        self._beam_render_fps = 15.0 if self._low_power else float(FPS)
        self._beam_render_tick = -1
        self._beam_render_x = self.player.x
        self._music_active = False
        self._beam_sound = None
        self._beam_sound_channel = None
        self._beam_sound_fading = False
        self._pickup_sound_variants = []
        self._last_pickup_sound_variant = None
        self._reaction_sounds = {"good": [], "bad": []}
        self._pending_reactions = []
        self._last_reaction_sound = {"good": None, "bad": None}
        self._collection_complete_chimes = []
        self._collection_complete_bite = None
        self._fx_channels = []
        self._voice_duck_gain = 1.0
        self._time_10_voice_fired = False
        self._time_5_voice_fired = False
        self._idle_voice_count = 0
        self._next_idle_voice_at = self.rng.uniform(4.0, 5.0)
        self._banter_count = 0
        self._next_banter_at = self.rng.uniform(8.0, 12.0)
        self.time_flash_text = ""
        self.time_flash_age = 0.0
        report(.78, "SOUND EFFECTS")
        self._load_beam_sound()
        self._load_pickup_sounds()
        self._load_reaction_sounds()
        self._load_collection_complete_sounds()
        self.voices = CharacterVoiceDirector(
            self.portraits,
            self.rng,
            lambda progress, status: report(.80 + progress * .16, status),
        )
        report(.97, "INTRO AUDIO")
        self._load_intro_sounds()
        self._load_countdown_sounds()
        self._load_time_up_sounds()
        # Keep a spatially even pipeline across the 92.4 m road. Without
        # this, all eight objects would bunch up at the horizon on startup.
        for slot in range(8):
            self._spawn((slot + 1) * ITEM_APPROACH_FRAMES / 9.0)
        report(1.0, "READY")
        if autoplay_intro:
            self.activate()

    def activate(self):
        """Start an already preloaded session without touching its assets."""
        if self._activated:
            return
        self._activated = True
        self._play_intro_sound("theremin")

    def set_difficulty(self, difficulty):
        """Apply a persisted service setting without rebuilding any assets."""
        try:
            difficulty = int(difficulty)
        except (TypeError, ValueError):
            difficulty = 0
        self.difficulty_level = max(-3, min(3, difficulty))
        self.difficulty = DIFFICULTY_PROFILES[self.difficulty_level]

    def prepare_for_replay(self):
        """Reset mutable session state while retaining all heavy resources.

        The sprite scale cache, UI, fonts, portraits and decoded sounds stay
        resident.  Only the lightweight road streamer and gameplay counters
        are renewed, so the next VUK trigger can start immediately.
        """
        self.close_background()
        for channel in self._fx_channels:
            try:
                channel.stop()
            except pygame.error:
                pass

        self.assets.background = StreamingBackground()
        self.player = PlayerUFO()
        self.portraits.reset()
        self.voices.reset()

        self.items, self.elapsed, self.spawn_timer = [], 0.0, 0.25
        self._slalom_side = self.rng.choice((-1.0, 1.0))
        self.time_left = float(self.duration)
        self.score = self.combo_bonus = self.collected_count = self.streak = 0
        self.munchies_score = self.junk_penalty = self.junk_abducted = 0
        self.collection_bonus = 0
        self.food_counts = {kind: 0 for kind in GOOD_VALUES}
        self.completed_food_sets = {kind: 0 for kind in GOOD_VALUES}
        self.collect_display_kind = None
        self.collect_display_count = 0
        self.collect_display_time = 0.0
        self.best_streak, self.max_combo_multiplier = 0, 1
        self.combo_time_left = 0.0
        self._pending_combo_voices.clear()

        self.phase, self.countdown_elapsed = "intro", 0.0
        self.intro_elapsed = 0.0
        self._intro_title_audio_started = False
        self._intro_channels.clear()
        self._countdown_sound_channel = None
        self._countdown_sound_number = None
        self.time_up_elapsed = 0.0
        self._time_up_second_started = False
        self._time_up_second_started_at = None
        self._time_up_beam_visible = False
        self._time_up_channels.clear()
        self.results_elapsed, self.finished = 0.0, False
        self.world_speed = .6
        self._background_closed = False
        self.beam_shocks.clear()

        self._beam_layer.fill((0, 0, 0, 0), self._beam_dirty_rect)
        self._beam_render_tick = -1
        self._beam_render_x = self.player.x
        self._music_active = False
        self._beam_sound_channel = None
        self._beam_sound_fading = False
        self._last_pickup_sound_variant = None
        self._pending_reactions.clear()
        self._last_reaction_sound = {"good": None, "bad": None}
        self._fx_channels.clear()
        self._voice_duck_gain = 1.0
        self._time_10_voice_fired = False
        self._time_5_voice_fired = False
        self._idle_voice_count = 0
        self._next_idle_voice_at = self.rng.uniform(4.0, 5.0)
        self._banter_count = 0
        self._next_banter_at = self.rng.uniform(8.0, 12.0)
        self.time_flash_text = ""
        self.time_flash_age = 0.0
        self._activated = False

        for slot in range(8):
            self._spawn((slot + 1) * ITEM_APPROACH_FRAMES / 9.0)

    def _sound(self, name):
        if self.sound_hook: self.sound_hook(name)

    @staticmethod
    def _load_intro_assets():
        intro_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Intro"

        def load(name, alpha, fallback_color=(0, 0, 0, 0)):
            path = intro_dir / name
            fallback = pygame.Surface(
                (WIDTH, HEIGHT), pygame.SRCALPHA if alpha else 0
            )
            fallback.fill(fallback_color)
            fallback = fallback.convert_alpha() if alpha else fallback.convert()
            if not path.is_file():
                print(f"[munchies] intro asset missing: Intro/{name}")
                return fallback
            try:
                surface = pygame.image.load(str(path))
                surface = surface.convert_alpha() if alpha else surface.convert()
                if surface.get_size() != (WIDTH, HEIGHT):
                    print(
                        f"[munchies] intro asset should be {WIDTH}x{HEIGHT} "
                        f"({name}: {surface.get_size()})"
                    )
                    surface = pygame.transform.smoothscale(
                        surface, (WIDTH, HEIGHT)
                    )
                    surface = surface.convert_alpha() if alpha else surface.convert()
                return surface
            except pygame.error as exc:
                print(f"[munchies] intro asset unavailable ({name}): {exc}")
                return fallback

        background = load("bgr.png", False, (4, 1, 14))
        title_full = load("title.png", True)
        foreground = load("foreGr.png", True)
        title_rect = title_full.get_bounding_rect(min_alpha=1)
        if title_rect.width and title_rect.height:
            title = title_full.subsurface(title_rect).copy().convert_alpha()
        else:
            title_rect = pygame.Rect(WIDTH // 2, HEIGHT // 2, 1, 1)
            title = pygame.Surface((1, 1), pygame.SRCALPHA).convert_alpha()
        return background, title, title_rect, foreground

    @staticmethod
    def _load_time_up_assets():
        ui_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "UI"

        black_path = ui_dir / "Timesup_black.png"
        black = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        black.fill((2, 0, 18, 106))
        try:
            if black_path.is_file():
                black = pygame.image.load(str(black_path)).convert_alpha()
                if black.get_size() != (WIDTH, HEIGHT):
                    # The supplied veil is deliberately only 160x120. Expand it
                    # once during setup instead of scaling it on every frame.
                    black = pygame.transform.scale(black, (WIDTH, HEIGHT)).convert_alpha()
            else:
                print("[munchies] time-up asset missing: UI/Timesup_black.png")
        except pygame.error as exc:
            print(f"[munchies] time-up veil unavailable: {exc}")

        title_path = ui_dir / "Timesup.png"
        title_full = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA).convert_alpha()
        try:
            if title_path.is_file():
                title_full = pygame.image.load(str(title_path)).convert_alpha()
                if title_full.get_size() != (WIDTH, HEIGHT):
                    title_full = pygame.transform.smoothscale(
                        title_full, (WIDTH, HEIGHT)
                    ).convert_alpha()
            else:
                print("[munchies] time-up asset missing: UI/Timesup.png")
        except pygame.error as exc:
            print(f"[munchies] time-up title unavailable: {exc}")

        title_rect = title_full.get_bounding_rect(min_alpha=1)
        if title_rect.width and title_rect.height:
            title = title_full.subsurface(title_rect).copy().convert_alpha()
        else:
            title_rect = pygame.Rect(WIDTH // 2, HEIGHT // 2, 1, 1)
            title = pygame.Surface((1, 1), pygame.SRCALPHA).convert_alpha()
        return black, title, title_rect

    def _load_intro_sounds(self):
        fx_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sound" / "FX"
        sound_specs = (
            ("theremin", fx_dir / "Intro_theremin.wav", .78),
            ("title", fx_dir / "munchies----abduction---.wav", 1.0),
        )
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=MIXER_BUFFER_SAMPLES)
            pygame.mixer.set_num_channels(max(16, pygame.mixer.get_num_channels()))
            for cue, path, volume in sound_specs:
                if not path.is_file():
                    print(f"[munchies] intro sound missing: Sound/FX/{path.name}")
                    continue
                sound = pygame.mixer.Sound(str(path))
                sound.set_volume(volume)
                self._intro_sounds[cue] = sound
            self._intro_audio_end_time = max(
                INTRO_TITLE_SECONDS,
                self._intro_sounds.get("theremin").get_length()
                if "theremin" in self._intro_sounds else 0.0,
                INTRO_TITLE_SECONDS + self._intro_sounds.get("title").get_length()
                if "title" in self._intro_sounds else INTRO_TITLE_SECONDS,
            )
        except pygame.error as exc:
            self._intro_sounds.clear()
            self._intro_audio_end_time = INTRO_TITLE_SECONDS
            print(f"[munchies] intro sounds unavailable: {exc}")

    def _play_intro_sound(self, cue):
        sound = self._intro_sounds.get(cue)
        if sound is None:
            return
        channel = sound.play()
        if channel is not None:
            self._intro_channels.append(channel)

    def _load_time_up_sounds(self):
        fx_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sound" / "FX"
        sound_files = {
            "first": fx_dir / "times up.wav",
            "second": fx_dir / "time-s-up---.wav",
        }
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=MIXER_BUFFER_SAMPLES)
            pygame.mixer.set_num_channels(max(16, pygame.mixer.get_num_channels()))
            for cue, path in sound_files.items():
                if not path.is_file():
                    print(f"[munchies] time-up sound missing: Sound/FX/{path.name}")
                    continue
                sound = pygame.mixer.Sound(str(path))
                self._time_up_sounds[cue] = sound
            first = self._time_up_sounds.get("first")
            second = self._time_up_sounds.get("second")
            self._time_up_first_duration = first.get_length() if first is not None else 0.0
            self._time_up_second_duration = second.get_length() if second is not None else 0.0
        except pygame.error as exc:
            self._time_up_sounds.clear()
            self._time_up_first_duration = 0.0
            self._time_up_second_duration = 0.0
            print(f"[munchies] time-up sounds unavailable: {exc}")

    def _play_time_up_sound(self, cue):
        sound = self._time_up_sounds.get(cue)
        if sound is None:
            return None
        channel = sound.play()
        if channel is not None:
            self._time_up_channels.append(channel)
        return channel

    def _stop_time_up_audio(self):
        for channel in self._time_up_channels:
            try:
                channel.stop()
            except pygame.error:
                pass
        self._time_up_channels.clear()

    def _load_countdown_sounds(self):
        fx_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sound" / "FX"
        sound_files = {
            "normal": fx_dir / "countdown.wav",
            "final": fx_dir / "countdown2.wav",
        }
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=MIXER_BUFFER_SAMPLES)
            for cue, path in sound_files.items():
                if not path.is_file():
                    print(f"[munchies] countdown sound missing: Sound/FX/{path.name}")
                    continue
                self._countdown_sounds[cue] = pygame.mixer.Sound(str(path))
        except pygame.error as exc:
            self._countdown_sounds.clear()
            print(f"[munchies] countdown sounds unavailable: {exc}")

    def _play_countdown_sound(self, number):
        if number == self._countdown_sound_number:
            return
        self._countdown_sound_number = number
        if self._countdown_sound_channel is not None:
            self._countdown_sound_channel.stop()
        cue = "final" if number == 1 else "normal"
        sound = self._countdown_sounds.get(cue)
        self._countdown_sound_channel = sound.play() if sound is not None else None
        self._track_fx_channel(self._countdown_sound_channel)

    def _stop_intro_audio(self):
        for channel in self._intro_channels:
            try:
                channel.stop()
            except pygame.error:
                pass
        self._intro_channels.clear()

    def _begin_countdown(self):
        self._stop_intro_audio()
        self.phase = "countdown"
        self.countdown_elapsed = 0.0
        self._countdown_sound_number = None
        # The animated intro title is the last setup-time smoothscale.  From
        # here the Pi workers may decode safely while the three-second
        # countdown fills the road cache in the background.
        self.assets.background.start()
        self._start_music()
        self._play_countdown_sound(3)

    def _begin_time_up(self):
        self._time_up_beam_visible = bool(self.player.beam)
        self.player.beam = False
        self.phase = "time_up"
        self.time_up_elapsed = 0.0
        self._time_up_second_started = False
        self._time_up_second_started_at = None

        # Give the stinger an empty soundstage. Any pickup reaction that was
        # waiting for its half-second delay is discarded with the frozen play.
        self._stop_beam_sound()
        self._stop_music(fade_ms=0)
        for channel in self._fx_channels:
            try:
                channel.stop()
            except pygame.error:
                pass
        self._fx_channels.clear()
        self._pending_reactions.clear()
        self.voices.stop(release_reservation=False)

        # The Time's Up title uses smoothscale.  Stop the PNG workers before
        # that transform begins; concurrent pygame image decoding and
        # transforms are unsafe on the Pi's 32-bit SDL build.  The frozen
        # street frame remains in the cache and stays drawable.
        self.close_background()
        self._play_time_up_sound("first")

    def _finish_time_up(self):
        self._stop_time_up_audio()
        self.phase = "results"
        self.results_elapsed = 0.0
        self._time_up_beam_visible = False
        if not self.voices.trigger_ending(
                self.collected_count >= MISSION_SUCCESS_FOOD):
            self.voices.trigger("time_over")
        self._sound("results")
        self.close_background()

    def _update_time_up(self, dt):
        self.time_up_elapsed += dt
        if (not self._time_up_second_started
                and self.time_up_elapsed >= self._time_up_first_duration - 1e-9):
            self.time_up_elapsed = max(
                self.time_up_elapsed, self._time_up_first_duration
            )
            self._time_up_second_started = True
            self._time_up_second_started_at = self.time_up_elapsed
            self._play_time_up_sound("second")
        second_elapsed = (
            self.time_up_elapsed - self._time_up_second_started_at
            if self._time_up_second_started_at is not None else 0.0
        )
        if (self._time_up_second_started
                and second_elapsed >= self._time_up_second_duration - 1e-9):
            self._finish_time_up()

    def _start_music(self):
        if self._music_active:
            return
        music_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Music"
        tracks = []
        for pattern in ("*.mp3", "*.ogg", "*.wav"):
            tracks.extend(sorted(music_dir.glob(pattern)))
        if not tracks:
            return
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=MIXER_BUFFER_SAMPLES)
            pygame.mixer.music.load(str(tracks[0]))
            pygame.mixer.music.set_volume(.85)
            pygame.mixer.music.play(-1)
            self._music_active = True
            print(f"[munchies] music playing: {tracks[0].name}, mixer={pygame.mixer.get_init()}")
        except pygame.error as exc:
            print(f"[munchies] music unavailable: {exc}")

    def _stop_music(self, fade_ms=650):
        if not self._music_active:
            return
        try:
            if fade_ms <= 0:
                pygame.mixer.music.stop()
            else:
                pygame.mixer.music.fadeout(fade_ms)
        except pygame.error:
            pass
        self._music_active = False

    def _load_beam_sound(self):
        fx_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sound" / "FX"
        path = next((
            candidate for candidate in fx_dir.iterdir()
            if candidate.is_file()
            and candidate.suffix.lower() in (".wav", ".ogg")
            and candidate.stem.lower() == "lightwave"
        ), None) if fx_dir.is_dir() else None
        if path is None:
            print("[munchies] beam sound missing: Sound/FX/Lightwave.wav")
            return
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=MIXER_BUFFER_SAMPLES)
            self._beam_sound = pygame.mixer.Sound(str(path))
            self._beam_sound.set_volume(.58)
        except pygame.error as exc:
            print(f"[munchies] beam sound unavailable: {exc}")

    def _load_pickup_sounds(self):
        fx_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sound" / "FX"
        pitch_dir = fx_dir / "Pitch"
        variants = (
            ("low", (pitch_dir / "Gotit1_Low.wav", pitch_dir / "Gotit2_Low.wav")),
            ("normal", (fx_dir / "Gotit1.wav", fx_dir / "Gotit2.wav")),
            ("high", (pitch_dir / "Gotit1_High.wav", pitch_dir / "Gotit2_High.wav")),
        )
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=MIXER_BUFFER_SAMPLES)
            pygame.mixer.set_num_channels(max(12, pygame.mixer.get_num_channels()))
            for label, paths in variants:
                if not all(path.is_file() for path in paths):
                    print(f"[munchies] pickup pitch variant missing: {label}")
                    continue
                pair = tuple(pygame.mixer.Sound(str(path)) for path in paths)
                for sound in pair:
                    # Two layers play together; 0.58 is 80% of the former
                    # 0.72 per-layer level.
                    sound.set_volume(.58)
                self._pickup_sound_variants.append(pair)
        except pygame.error as exc:
            self._pickup_sound_variants.clear()
            print(f"[munchies] pickup sounds unavailable: {exc}")

    def _play_pickup_sounds(self):
        # Pick one shared -6% / normal / +6% pitch state, then start both
        # layers during the same update tick so their relationship stays intact.
        if not self._pickup_sound_variants:
            return
        for pair in self._pickup_sound_variants:
            for sound in pair:
                sound.stop()
        available = [
            pair for pair in self._pickup_sound_variants
            if pair is not self._last_pickup_sound_variant
        ] or self._pickup_sound_variants
        pair = self.rng.choice(available)
        self._last_pickup_sound_variant = pair
        for sound in pair:
            self._track_fx_channel(sound.play())

    def _load_reaction_sounds(self):
        fx_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sound" / "FX"
        directories = {
            path.name.lower(): path for path in fx_dir.iterdir() if path.is_dir()
        } if fx_dir.is_dir() else {}
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=MIXER_BUFFER_SAMPLES)
            for kind in ("good", "bad"):
                directory = directories.get(kind)
                paths = sorted(
                    path for path in directory.iterdir()
                    if path.is_file() and path.suffix.lower() in (".wav", ".ogg")
                ) if directory is not None else []
                for path in paths:
                    sound = pygame.mixer.Sound(str(path))
                    sound.set_volume(.80)
                    self._reaction_sounds[kind].append(sound)
                if not paths:
                    print(f"[munchies] no {kind} reaction sounds found")
        except pygame.error as exc:
            self._reaction_sounds = {"good": [], "bad": []}
            print(f"[munchies] reaction sounds unavailable: {exc}")

    def _load_collection_complete_sounds(self):
        fx_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sound" / "FX"
        chime_paths = (
            fx_dir / "collcomp_chime1.wav",
            fx_dir / "collcomp_chime2.wav",
        )
        bite_path = fx_dir / "collcomp_bite.wav"
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(
                    frequency=44100, size=-16, channels=2,
                    buffer=MIXER_BUFFER_SAMPLES,
                )
            pygame.mixer.set_num_channels(max(12, pygame.mixer.get_num_channels()))
            self._collection_complete_chimes = [
                pygame.mixer.Sound(str(path))
                for path in chime_paths if path.is_file()
            ]
            self._collection_complete_bite = (
                pygame.mixer.Sound(str(bite_path))
                if bite_path.is_file() else None
            )
            if len(self._collection_complete_chimes) != len(chime_paths):
                print("[munchies] one or more collection-complete chimes are missing")
            if self._collection_complete_bite is None:
                print("[munchies] collection-complete bite is missing")
        except pygame.error as exc:
            self._collection_complete_chimes.clear()
            self._collection_complete_bite = None
            print(f"[munchies] collection-complete sounds unavailable: {exc}")

    def _play_collection_complete_sounds(self):
        # Start both layers in the same update tick. SDL assigns separate mixer
        # channels, so the bite transient sits on top of either random chime.
        if self._collection_complete_chimes:
            chime = self.rng.choice(self._collection_complete_chimes)
            self._track_fx_channel(chime.play())
        if self._collection_complete_bite is not None:
            self._track_fx_channel(self._collection_complete_bite.play())

    def _schedule_reaction_sound(self, good):
        self._pending_reactions.append([.5, "good" if good else "bad"])

    def _update_reaction_sounds(self, dt):
        waiting = []
        for delay, kind in self._pending_reactions:
            delay -= dt
            if delay > 1e-9:
                waiting.append([delay, kind])
                continue
            choices = self._reaction_sounds[kind]
            if not choices:
                continue
            last = self._last_reaction_sound[kind]
            available = [sound for sound in choices if sound is not last] or choices
            sound = self.rng.choice(available)
            self._last_reaction_sound[kind] = sound
            self._track_fx_channel(sound.play())
        self._pending_reactions = waiting

    def _track_fx_channel(self, channel):
        if channel is None:
            return
        channel.set_volume(self._voice_duck_gain)
        self._fx_channels.append(channel)

    def _update_voice_ducking(self, dt):
        """Smoothly clear space for dialogue without audible volume pumping."""
        target = VOICE_DUCK_SFX_GAIN if self.voices.busy else 1.0
        tau = (
            VOICE_DUCK_ATTACK_SECONDS
            if target < self._voice_duck_gain
            else VOICE_DUCK_RELEASE_SECONDS
        )
        blend = 1.0 - math.exp(-dt / tau)
        self._voice_duck_gain += (target - self._voice_duck_gain) * blend
        if abs(self._voice_duck_gain - target) < .002:
            self._voice_duck_gain = target

        active_channels = []
        for channel in self._fx_channels:
            if channel.get_busy():
                channel.set_volume(self._voice_duck_gain)
                active_channels.append(channel)
        self._fx_channels = active_channels
        if self._beam_sound_channel is not None and self._beam_sound_channel.get_busy():
            self._beam_sound_channel.set_volume(self._voice_duck_gain)

    def _sync_beam_sound(self):
        active = self.phase == "playing" and bool(self.player.beam)
        channel_busy = (
            self._beam_sound_channel is not None
            and self._beam_sound_channel.get_busy()
        )
        if active and self._beam_sound is not None:
            if self._beam_sound_fading and self._beam_sound_channel is not None:
                self._beam_sound_channel.stop()
                channel_busy = False
            if not channel_busy:
                self._beam_sound_channel = self._beam_sound.play(loops=-1)
                if self._beam_sound_channel is not None:
                    self._beam_sound_channel.set_volume(self._voice_duck_gain)
            self._beam_sound_fading = False
        elif not active and channel_busy and not self._beam_sound_fading:
            self._beam_sound_channel.fadeout(55)
            self._beam_sound_fading = True
        elif not channel_busy:
            self._beam_sound_channel = None
            self._beam_sound_fading = False

    def _stop_beam_sound(self):
        if self._beam_sound_channel is not None:
            self._beam_sound_channel.stop()
            self._beam_sound_channel = None
        self._beam_sound_fading = False

    def handle_event(self, event):
        """Accepts pygame events or protocol GameEvents."""
        if hasattr(event, "kind"):
            # Some firmware versions report both DOWN/UP, older ones only a
            # single edge. Single-edge events become short pulses, never a
            # permanently stuck direction/beam.
            if event.kind == "FLIPPER_LEFT": self.player.left = "pulse"; self.player._left_pulse = .16; return
            if event.kind == "FLIPPER_RIGHT": self.player.right = "pulse"; self.player._right_pulse = .16; return
            if event.kind in ("PLUNGER", "PLAYER_PRESS"): self.player.beam = "pulse"; self.player._beam_pulse = .18; return
            down = event.kind.endswith("_DOWN")
            up = event.kind.endswith("_UP")
            if "FLIPPER_LEFT" in event.kind: self.player.left = down and not up
            elif "FLIPPER_RIGHT" in event.kind: self.player.right = down and not up
            elif "PLUNGER" in event.kind or "PLAYER_PRESS" in event.kind: self.player.beam = down and not up
            return
        if event.type not in (pygame.KEYDOWN, pygame.KEYUP): return
        down = event.type == pygame.KEYDOWN
        if event.key in (pygame.K_LEFT, pygame.K_a): self.player.left = down
        elif event.key in (pygame.K_RIGHT, pygame.K_d): self.player.right = down
        elif event.key in (pygame.K_SPACE, pygame.K_p): self.player.beam = down

    def _spawn(self, travelled_frames=0.0, kind=None, world_x=None,
               optional_choice=False):
        if kind is None:
            if self.elapsed < TUTORIAL_SECONDS:
                good_chance = .82
            elif self.elapsed < 70.0:
                good_chance = .72
            else:
                # Late difficulty comes from speed, lane pressure and choices,
                # not from turning the street into a wall of junk.
                good_chance = .76
            good = self.rng.random() < good_chance
            kind = self.rng.choice(tuple(GOOD_VALUES) if good else BAD_KINDS)
        if world_x is None:
            tutorial_blend = self._smoothstep(self.elapsed / TUTORIAL_SECONDS)
            base_lane_limit = .68 if self.elapsed < TUTORIAL_SECONDS else .88
            lane_limit = max(
                .58,
                min(.96, base_lane_limit + self.difficulty["lane"] * tutorial_blend),
            )
            world_x = self.rng.uniform(-lane_limit, lane_limit)
        progress = travelled_frames / ITEM_APPROACH_FRAMES
        item = RoadItem(
            kind, world_x, depth=progress, optional_choice=optional_choice,
            travelled_frames=travelled_frames,
        )
        item.visual_pixels = item.sprite_pixels()
        self.items.append(item)

    @staticmethod
    def _smoothstep(value):
        value = max(0.0, min(1.0, value))
        return value * value * (3.0 - 2.0 * value)

    def _speed_for_elapsed(self):
        if self.elapsed <= TUTORIAL_SECONDS:
            t = self._smoothstep(self.elapsed / TUTORIAL_SECONDS)
            base_speed = .6 + .4 * t
            # Every mode begins with the same readable first seconds.  Its
            # selected pressure fades in smoothly across the tutorial.
            difficulty_blend = t
        elif self.elapsed <= SPEED_RAMP_END_SECONDS:
            ramp_seconds = SPEED_RAMP_END_SECONDS - TUTORIAL_SECONDS
            t = self._smoothstep((self.elapsed - TUTORIAL_SECONDS) / ramp_seconds)
            base_speed = 1.0 + 1.4 * t
            difficulty_blend = 1.0
        else:
            # Keep adding pressure without forcing the 600-frame PNG sequence
            # into visibly jerky 3-4-frame jumps on every display tick.
            late = self.elapsed - SPEED_RAMP_END_SECONDS
            late_gain = .25 if getattr(self, "_low_power", False) else .35
            base_speed = 2.4 + late_gain * (1.0 - math.exp(-late / 50.0))
            difficulty_blend = 1.0
        multiplier = 1.0 + (self.difficulty["speed"] - 1.0) * difficulty_blend
        return base_speed * multiplier

    def _spawn_wave(self):
        capacity = 8 - len(self.items)
        if capacity <= 0:
            return 0
        late = max(0.0, min(1.0, (self.elapsed - 45.0) / 85.0))
        pattern_pressure = self.difficulty["patterns"]
        choice_chance = min(.82, (.10 + .38 * late) * pattern_pressure)
        if self.elapsed >= 45.0 and capacity >= 2 and self.rng.random() < choice_chance:
            triple_chance = min(.62, .38 * pattern_pressure)
            triple = (
                self.elapsed >= 105.0
                and capacity >= 3
                and self.rng.random() < triple_chance
            )
            lanes = (-.82, 0.0, .82) if triple else (-.78, .78)
            for lane in lanes:
                self._spawn(
                    kind=self.rng.choice(tuple(GOOD_VALUES)),
                    world_x=lane,
                    optional_choice=True,
                )
            return len(lanes)

        slalom_chance = min(.88, (.16 + .48 * late) * pattern_pressure)
        if self.elapsed >= 35.0 and self.rng.random() < slalom_chance:
            self._slalom_side *= -1.0
            kind_pool = tuple(GOOD_VALUES) if self.rng.random() < .86 else BAD_KINDS
            self._spawn(kind=self.rng.choice(kind_pool), world_x=.82 * self._slalom_side)
        else:
            self._spawn()
        return 1

    def _next_spawn_delay(self, wave_size):
        base = max(.38, .88 - min(self.elapsed, 28.0) * .018)
        if wave_size > 1:
            base *= 1.35
        return base * self.rng.uniform(.8, 1.15)

    def _time_reward_scale(self):
        effective_elapsed = self.elapsed * self.difficulty["fade"]
        if effective_elapsed <= TIME_REWARD_FADE_START:
            return 1.0
        if effective_elapsed >= TIME_REWARD_FADE_END:
            return 0.0
        remaining = 1.0 - (
            (effective_elapsed - TIME_REWARD_FADE_START)
            / (TIME_REWARD_FADE_END - TIME_REWARD_FADE_START)
        )
        return remaining ** 1.35

    def _time_bank_scale(self):
        """Discourage safe time hoarding without imposing a hard cap."""
        if self.time_left <= TIME_BANK_FULL_REWARD_SECONDS:
            return 1.0
        if self.time_left >= TIME_BANK_DAMPED_REWARD_SECONDS:
            return TIME_BANK_MIN_REWARD_SCALE
        span = TIME_BANK_DAMPED_REWARD_SECONDS - TIME_BANK_FULL_REWARD_SECONDS
        progress = self._smoothstep(
            (self.time_left - TIME_BANK_FULL_REWARD_SECONDS) / span
        )
        return 1.0 - (1.0 - TIME_BANK_MIN_REWARD_SCALE) * progress

    @staticmethod
    def _time_flash_label(seconds):
        rounded = round(seconds, 1)
        if abs(rounded - round(rounded)) < .05:
            return f"+{int(round(rounded))} SEC"
        return f"+{rounded:.1f} SEC"

    def _award_time(self, nominal_seconds):
        awarded = (
            nominal_seconds
            * self.difficulty["reward"]
            * self._time_reward_scale()
            * self._time_bank_scale()
        )
        if awarded < .05:
            return 0.0
        self.time_left += awarded
        self.time_flash_text = self._time_flash_label(awarded)
        self.time_flash_age = .75 if nominal_seconds <= 1.0 else 1.1
        return awarded

    def _award_food_points(self, base, multiplier):
        base_award = base
        combo_award = base * (multiplier - 1)
        self.munchies_score += base_award
        self.score += base_award
        self.combo_bonus += combo_award
        return base_award + combo_award

    def _apply_junk_penalty(self):
        self.junk_abducted += 1
        deducted = min(JUNK_SCORE_PENALTY, self.score)
        self.score -= deducted
        self.junk_penalty += deducted
        return deducted

    def _award_collection_points(self):
        awarded = COLLECTION_BONUS_POINTS
        self.collection_bonus += awarded
        return awarded

    @property
    def combo_multiplier(self):
        return next(
            (multiplier for threshold, multiplier in reversed(COMBO_TIERS)
             if self.streak >= threshold),
            1,
        )

    def _reset_combo(self, clear_pending=True):
        self.streak = 0
        self.combo_time_left = 0.0
        if clear_pending:
            self._pending_combo_voices.clear()

    def _record_food_pickup(self, kind):
        previous = self.food_counts[kind]
        current = previous + 1
        self.food_counts[kind] = current
        # Every food pickup refreshes the card immediately. Picking a
        # different kind simply replaces the currently displayed card.
        self.collect_display_kind = kind
        self.collect_display_count = current
        self.collect_display_time = COLLECT_PANEL_SECONDS
        if current < COLLECT_TARGET:
            return False
        # Keep 10/10 on the visible card, but reset the underlying counter so
        # the very next matching pickup starts a fresh 1/10 collection run.
        self.food_counts[kind] = 0
        self.completed_food_sets[kind] += 1
        self._award_collection_points()
        self._award_time(COLLECTION_TIME_BONUS_SECONDS)
        self._play_collection_complete_sounds()
        self.voices.trigger("collection_complete")
        self._sound("collection_complete")
        return True

    def update(self, dt):
        dt = min(dt, .1)
        if self.phase == "time_up":
            # This phase intentionally advances only its own two-part stinger.
            # Road, items, UFO, particles and portraits stay on the exact frame
            # on which the clock reached zero.
            self._update_time_up(dt)
            return
        self._update_reaction_sounds(dt)
        self.voices.update(dt)
        self._update_voice_ducking(dt)
        self.beam_shocks = [
            (age + dt, start_t) for age, start_t in self.beam_shocks
            if age + dt < .55
        ]
        if self.phase == "intro":
            self._stop_beam_sound()
            # Decode on the worker and convert one road frame per display tick.
            # The eight-second intro plus countdown can therefore warm the full
            # 60-frame LRU without ever blocking the first gameplay frame.
            self.assets.background.prime()
            self.intro_elapsed += dt
            if (not self._intro_title_audio_started
                    and self.intro_elapsed >= INTRO_TITLE_SECONDS - 1e-9):
                self.intro_elapsed = max(self.intro_elapsed, INTRO_TITLE_SECONDS)
                self._intro_title_audio_started = True
                self._play_intro_sound("title")
            intro_end = self._intro_audio_end_time + INTRO_POST_AUDIO_SECONDS
            if self.intro_elapsed >= intro_end - 1e-9:
                self._begin_countdown()
            return
        if self.phase == "results":
            self._stop_beam_sound()
            self.results_elapsed += dt
            minimum_time_passed = self.results_elapsed >= RESULTS_SECONDS
            voice_finished = not self.voices.busy
            voice_timeout = self.results_elapsed >= RESULTS_VOICE_MAX_SECONDS
            self.finished = minimum_time_passed and (voice_finished or voice_timeout)
            if self.finished:
                self.voices.stop()
            return
        if self.phase == "countdown":
            self._stop_beam_sound()
            # The road stays still, but decoded frames are moved into video
            # memory one at a time. This turns the countdown into a free
            # pre-roll buffer and prevents a conversion burst on "1" -> play.
            self.assets.background.prime()
            self.countdown_elapsed += dt
            if self.countdown_elapsed >= COUNTDOWN_SECONDS - 1e-9:
                self.countdown_elapsed = COUNTDOWN_SECONDS
                self.phase = "playing"
                self.voices.trigger("game_start")
            else:
                self._play_countdown_sound(self.countdown_number())
            return
        # Combo announcements are milestone events. If another line occupied
        # the channel at the exact pickup frame, play them as soon as it ends
        # instead of silently losing the achievement to the ordinary cooldown.
        if self._pending_combo_voices and not self.voices.busy:
            event_key = self._pending_combo_voices[0]
            if self.voices.trigger(
                    event_key, ignore_cooldown=True, chance_already_passed=True):
                self._pending_combo_voices.pop(0)
        if self.streak > 0:
            self.combo_time_left = max(0.0, self.combo_time_left - dt)
            if self.combo_time_left <= 0.0:
                # The visual/scoring chain expires, but a milestone line that
                # already passed its 40% roll may still finish after the
                # current speaker. Otherwise long clips would erase combos.
                self._reset_combo(clear_pending=False)
        self.elapsed += dt
        self.time_left = max(0.0, self.time_left - dt * self.difficulty["clock"])
        if self.time_left <= 5.0:
            # A -2 second junk penalty can jump across both thresholds in one
            # update. In that case play only the more urgent warning.
            self._time_10_voice_fired = True
            if not self._time_5_voice_fired:
                self._time_5_voice_fired = True
                self.voices.trigger("time_5")
        elif not self._time_10_voice_fired and self.time_left <= 10.0:
            self._time_10_voice_fired = True
            self.voices.trigger("time_10")
        self.time_flash_age = max(0.0, self.time_flash_age - dt)
        self.collect_display_time = max(0.0, self.collect_display_time - dt)
        # A readable 15-second tutorial, a compressed ramp to 45 seconds, then
        # a second acceleration for the increasingly rare extended skill-run.
        self.world_speed = self._speed_for_elapsed()
        self.assets.background.update(dt, self.world_speed)
        self.player.update(dt)
        self._sync_beam_sound()
        self.spawn_timer -= dt * self.world_speed
        if self.spawn_timer <= 0 and len(self.items) < 8:
            wave_size = self._spawn_wave()
            self.spawn_timer = self._next_spawn_delay(wave_size)
        survivors = []
        callout_candidates = []
        callout_top = VOICE_CALLOUT_TARGET_Y - VOICE_CALLOUT_HALF_WINDOW
        callout_bottom = VOICE_CALLOUT_TARGET_Y + VOICE_CALLOUT_HALF_WINDOW
        for item in self.items:
            if item.captured:
                item.capture_elapsed += dt
                progress = min(1.0, item.capture_elapsed / CAPTURE_DURATION)
                eased = 1.0 - (1.0 - progress) ** 3
                # Re-evaluate the UFO target X so the pickup follows the ship
                # if the player moves during the short suction animation.
                item.capture_x = item.capture_start_x + (self.player.x - item.capture_start_x) * eased
                item.capture_y = item.capture_start_y + (self.player.y - item.capture_start_y) * eased
                item.visual_pixels = max(1.0, item.capture_start_pixels * (1.0 - eased))
                if progress < 1.0:
                    survivors.append(item)
                continue

            # Advance in the same source-frame units as the scrolling street,
            # instead of using an unrelated screen-space velocity.
            item.travelled_frames += self.assets.background.SOURCE_FPS * self.world_speed * dt
            item.depth = item.travelled_frames / ITEM_APPROACH_FRAMES
            target_pixels = item.sprite_pixels()
            if item.visual_pixels <= 0:
                item.visual_pixels = target_pixels
            else:
                # Frame-rate-independent easing plus a perspective-aware
                # growth limiter. Near-camera targets can jump several pixels
                # per game tick; the display size catches up over a few frames
                # instead of visibly popping between cached sprites.
                delta = target_pixels - item.visual_pixels
                eased_step = delta * (1.0 - math.exp(-10.0 * dt))
                max_step = (20.0 + item.visual_pixels * .40) * dt
                item.visual_pixels += max(-max_step, min(max_step, eased_step))
            x, y = item.position()
            if item.good and not item.voice_spawn_checked:
                if callout_top <= y <= callout_bottom:
                    callout_candidates.append((abs(y - VOICE_CALLOUT_TARGET_Y), item))
                elif y > callout_bottom:
                    # The useful warning window has passed; never announce an
                    # item that is already under the UFO or leaving the frame.
                    item.voice_spawn_checked = True
            # Edge contact counts: the old centre-point-only test made a
            # pickup wait until roughly 80% of the sprite had entered.
            in_beam = self.player.beam_contains(x, y, item.visual_pixels * .42)
            if in_beam and item.depth > .55:
                if item.good:
                    previous_multiplier = self.combo_multiplier
                    self.streak += 1
                    self.combo_time_left = (
                        COMBO_TIMEOUT_SECONDS * self.difficulty["combo_window"]
                    )
                    mult = self.combo_multiplier
                    self.best_streak = max(self.best_streak, self.streak)
                    self.max_combo_multiplier = max(self.max_combo_multiplier, mult)
                    base = GOOD_VALUES[item.kind]
                    self._award_food_points(base, mult)
                    self.collected_count += 1; self._sound("collect")
                    self._award_time(FOOD_TIME_BONUS_SECONDS)
                    self._record_food_pickup(item.kind)
                    if mult > previous_multiplier:
                        self._sound("combo")
                    combo_event = COMBO_VOICE_EVENTS.get(self.streak)
                    if combo_event and self.voices.has_clips(combo_event):
                        rule = VOICE_RULES[combo_event]
                        # Roll once at the milestone. A pre-approved line may
                        # wait for the current speaker, but is never re-rolled
                        # every frame (which would turn 40% back into 100%).
                        if self.rng.random() <= rule.chance:
                            if not self.voices.trigger(
                                    combo_event, chance_already_passed=True):
                                if combo_event not in self._pending_combo_voices:
                                    self._pending_combo_voices.append(combo_event)
                    else:
                        item_event = f"food_collected:{item.kind}"
                        voice_event = (
                            item_event if item_event in VOICE_EVENT_IDS
                            and self.voices.has_clips(item_event)
                            else "food_collected"
                        )
                        # Select exactly one eligible pool per pickup. A failed
                        # probability roll must not cascade into another pool.
                        self.voices.trigger(voice_event)
                    self._next_idle_voice_at = self.elapsed + self.rng.uniform(4.0, 5.0)
                else:
                    self._apply_junk_penalty(); self._reset_combo(); self.player.stun = .55; self._sound("bad_pickup")
                    time_penalty = 2.0 * self.difficulty["junk_time"]
                    self.time_left = max(0.0, self.time_left - time_penalty)
                    rounded_penalty = round(time_penalty, 1)
                    if abs(rounded_penalty - round(rounded_penalty)) < .05:
                        penalty_label = f"-{int(round(rounded_penalty))} SEC"
                    else:
                        penalty_label = f"-{rounded_penalty:.1f} SEC"
                    self.time_flash_text, self.time_flash_age = penalty_label, .75
                    if item.kind in POLICE_BAD_KINDS:
                        badge_event = "police_item:badge"
                        voice_event = (
                            badge_event
                            if item.kind == "badge" and self.voices.has_clips(badge_event)
                            else "police_item"
                        )
                    else:
                        voice_event = "junk_collected"
                    self.voices.trigger(voice_event)
                self._play_pickup_sounds()
                self._schedule_reaction_sound(item.good)
                item.captured = True
                item.capture_elapsed = 0.0
                item.capture_start_x = item.capture_x = x
                item.capture_start_y = item.capture_y = y
                item.capture_start_pixels = max(1.0, item.visual_pixels)
                start_t = max(0.0, min(1.0, (y - BEAM_TOP_Y) / (BEAM_BASE_Y - BEAM_TOP_Y)))
                self.beam_shocks.append((0.0, start_t))
                survivors.append(item)
                continue
            # Remove as soon as the calibrated sprite has fully crossed the
            # bottom edge. The projection keeps advancing beyond the UFO;
            # it is no longer clamped at frame 580, so missed items cannot
            # remain frozen at the bottom of the screen.
            offscreen = y - item.sprite_pixels() * .5 > HEIGHT or item.travelled_frames >= TRACK_FRAME_COUNT
            if not offscreen:
                survivors.append(item)
            elif item.good and not item.optional_choice:
                self._reset_combo()
                self.voices.trigger("valuable_missed")
        self.items = survivors
        if callout_candidates:
            # Several foods can enter the band together during a late choice
            # wave. Announce the one the player can actually see near the
            # focal point, not whichever object happened to spawn first.
            _, announced_item = min(callout_candidates, key=lambda candidate: candidate[0])
            announced_item.voice_spawn_checked = True
            self.voices.trigger("good_item_spawn", announced_item.kind)
        if self.voices.voice_silence_elapsed >= VOICE_LAUGH_SILENCE_SECONDS:
            if self.voices.trigger("silence_laugh"):
                # Do not stack an ordinary idle line or a banter exchange right
                # behind the laugh; both schedulers get a fresh natural gap.
                self._next_idle_voice_at = self.elapsed + self.rng.uniform(5.0, 7.0)
                self._next_banter_at = self.elapsed + self.rng.uniform(8.0, 13.0)
        if self.elapsed >= self._next_idle_voice_at:
            if self.voices.trigger("idle"):
                self._idle_voice_count += 1
            interval = (3.8, 5.5) if self.elapsed >= 70.0 else (5.0, 7.0)
            self._next_idle_voice_at = self.elapsed + self.rng.uniform(*interval)
        if self.elapsed >= self._next_banter_at:
            if self.voices.trigger_banter():
                self._banter_count += 1
            interval = (6.0, 9.0) if self.elapsed >= 70.0 else (8.0, 13.0)
            self._next_banter_at = self.elapsed + self.rng.uniform(*interval)
        if self.time_left <= 0.0:
            self._begin_time_up()

    def close_background(self):
        if not self._background_closed:
            self.assets.background.close()
            self._background_closed = True
        self._stop_beam_sound()
        self._stop_intro_audio()
        self._stop_time_up_audio()
        if self._countdown_sound_channel is not None:
            self._countdown_sound_channel.stop()
            self._countdown_sound_channel = None
        self._stop_music()
        if self.finished:
            self.voices.stop()

    def result_dict(self):
        total_bonus = (
            self.munchies_score + self.combo_bonus
            + self.collection_bonus - self.junk_penalty
        )
        return {"total_bonus": total_bonus,
                "collected_count": self.collected_count,
                "combo_bonus": self.combo_bonus, "base_score": self.score,
                "munchies_score": self.munchies_score,
                "collection_bonus": self.collection_bonus,
                "junk_penalty": self.junk_penalty,
                "junk_abducted": self.junk_abducted,
                "completed_food_sets": sum(self.completed_food_sets.values()),
                "best_streak": self.best_streak,
                "max_combo_multiplier": self.max_combo_multiplier}

    @staticmethod
    def _inset_beam(polygon, top_inset, bottom_inset):
        return (
            (polygon[0][0] + top_inset, polygon[0][1]),
            (polygon[1][0] - top_inset, polygon[1][1]),
            (polygon[2][0] - bottom_inset, polygon[2][1]),
            (polygon[3][0] + bottom_inset, polygon[3][1]),
        )

    def _wavy_beam_shape(self, polygon, inset_top=0.0, inset_bottom=0.0,
                         wave_amplitude=0.0, phase_offset=0.0, segments=14):
        """Build a perspective trapezoid with gently animated organic edges."""
        left, right = [], []
        for step in range(segments + 1):
            t = step / segments
            y = BEAM_TOP_Y + (BEAM_BASE_Y - BEAM_TOP_Y) * t
            base_left = polygon[0][0] + (polygon[3][0] - polygon[0][0]) * t
            base_right = polygon[1][0] + (polygon[2][0] - polygon[1][0]) * t
            inset = inset_top + (inset_bottom - inset_top) * t
            inset = min(inset, max(0.0, (base_right - base_left) * .5 - 1.0))
            # Two frequencies avoid a mechanical, perfectly sinusoidal edge.
            wave = math.sin(t * math.tau * 2.15 + self.elapsed * 6.2 + phase_offset)
            wave += .38 * math.sin(t * math.tau * 4.7 - self.elapsed * 3.8 + phase_offset)
            wave *= wave_amplitude * (.35 + .65 * t)
            left.append((base_left + inset + wave, y))
            right.append((base_right - inset + wave, y))
        return tuple(left + list(reversed(right))), tuple(left), tuple(right)

    def _draw_beam(self, screen):
        """Soft, animated tractor beam inspired by the concept artwork."""
        render_tick = int(self.elapsed * self._beam_render_fps + 1e-9)
        if render_tick == self._beam_render_tick:
            # On the Pi the expensive procedural field is refreshed at the
            # same 15 FPS as the street sequence. Between refreshes the cached
            # layer follows the UFO exactly, so steering never leaves the beam
            # detached from its emitter.
            x_shift = round(self.player.x - self._beam_render_x)
            screen.blit(
                self._beam_layer,
                (self._beam_dirty_rect.left + x_shift, self._beam_dirty_rect.top),
                self._beam_dirty_rect,
            )
            return
        self._beam_render_tick = render_tick
        self._beam_render_x = self.player.x
        pulse = (math.sin(self.elapsed * 11.0) + 1.0) * .5
        capture_flash = max((1.0 - age / .24 for age, _ in self.beam_shocks if age < .24), default=0.0)
        outer = self.player.beam_polygon()
        beam = self._beam_layer
        beam.fill((0, 0, 0, 0), self._beam_dirty_rect)

        # Several translucent, borderless layers simulate a soft glow. Each
        # layer has a slightly different wave phase, avoiding the old rigid box.
        aura, _, _ = self._wavy_beam_shape(outer, -8, -11, 4.2, .1)
        wide_glow, _, _ = self._wavy_beam_shape(outer, -3, -5, 3.2, .8)
        body, body_left, body_right = self._wavy_beam_shape(
            outer, BEAM_TOP_INSET + 7, 6, 2.4, 1.4)
        inner, _, _ = self._wavy_beam_shape(outer, BEAM_TOP_INSET + 14, 20, 1.5, 2.1)
        core, _, _ = self._wavy_beam_shape(outer, BEAM_TOP_INSET + 20, 31, .8, 2.8)
        pygame.draw.polygon(beam, (64, 255, 30, 14 + int(pulse * 8)), aura)
        pygame.draw.polygon(beam, (72, 255, 34, 27 + int(pulse * 10)), wide_glow)
        pygame.draw.polygon(beam, (73, 255, 29, min(180, 88 + int(pulse * 26 + capture_flash * 55))), body)
        pygame.draw.polygon(beam, (128, 255, 69, min(160, 62 + int(pulse * 22 + capture_flash * 45))), inner)
        pygame.draw.polygon(beam, (205, 255, 145, min(150, 42 + int(pulse * 20 + capture_flash * 50))), core)
        pygame.draw.lines(beam, (137, 255, 66, 75 + int(pulse * 45)), False, body_left, 3)
        pygame.draw.lines(beam, (137, 255, 66, 75 + int(pulse * 45)), False, body_right, 3)

        # Curved energy waves travel toward the UFO through the cone. Their
        # perspective width is taken from the wavy body at the current height.
        for band in range(5):
            t = (self.elapsed * .72 + band / 5.0) % 1.0
            index = min(len(body_left) - 1, round(t * (len(body_left) - 1)))
            left_x, y = body_left[index]
            right_x = body_right[index][0]
            points = []
            for part in range(9):
                u = part / 8
                arch = math.sin(u * math.pi) * (2.0 + 3.0 * t)
                points.append((left_x + (right_x - left_x) * u, y - arch))
            alpha = int(35 + 85 * (1.0 - abs(.5 - t) * 2))
            pygame.draw.lines(beam, (207, 255, 153, alpha), False, points, 2)

        # A true double helix: the two strands exchange sides three times and
        # flash at their crossings.
        helix_crossings = []
        for filament in (-1, 1):
            points = []
            for index, (left_point, right_point) in enumerate(zip(body_left, body_right)):
                t = index / (len(body_left) - 1)
                centre = (left_point[0] + right_point[0]) * .5
                half_width = (right_point[0] - left_point[0]) * .5
                helix = math.sin(t * math.tau * 3.0 + self.elapsed * 7.5)
                x = centre + filament * half_width * .34 * helix
                points.append((x, left_point[1]))
                if filament == 1 and abs(helix) < .16:
                    helix_crossings.append((x, left_point[1]))
            color = ((208, 255, 145, 58 + int(pulse * 38)) if filament == 1
                     else (103, 255, 56, 52 + int(pulse * 34)))
            pygame.draw.lines(beam, color, False, points, 2)
        for x, y in helix_crossings:
            pygame.draw.circle(beam, (230, 255, 190, 105 + int(pulse * 70)), (round(x), round(y)), 3)

        # Domain-warped interference lattice. The incommensurate frequencies
        # 12/7 and 8/5 produce slowly repeating alien caustic cells.
        for row in range(1, 13):
            t = row / 13.0
            index = min(len(body_left) - 1, round(t * (len(body_left) - 1)))
            left_x, y = body_left[index]
            right_x = body_right[index][0]
            for column in range(8):
                u = -1.0 + column * (2.0 / 7.0)
                field = math.sin(12.0 * t - 5.0 * self.elapsed +
                                 2.0 * math.sin(7.0 * t + 3.0 * self.elapsed))
                field *= math.cos(8.0 * u +
                                  4.0 * math.sin(5.0 * t - 2.0 * self.elapsed))
                if field > .32:
                    warp = math.sin(t * 13.0 + self.elapsed * 4.1 + u * 3.0) * .025
                    x = left_x + (right_x - left_x) * max(0.0, min(1.0, (u + 1) * .5 + warp))
                    alpha = min(145, round(35 + field * 92 + capture_flash * 35))
                    radius = 1 if field < .72 else 2
                    pygame.draw.circle(beam, (203, 255, 154, alpha), (round(x), round(y)), radius)

        # Capture shockwaves: one lens collapses toward the UFO while a weaker
        # echo shoots back toward the focus aperture.
        for age, start_t in self.beam_shocks:
            progress = min(1.0, age / .48)
            eased = 1.0 - (1.0 - progress) ** 3
            for t, strength in ((start_t + (1.0 - start_t) * eased, 1.0),
                                (start_t * (1.0 - eased), .48)):
                index = min(len(body_left) - 1, round(t * (len(body_left) - 1)))
                left_x, y = body_left[index]
                right_x = body_right[index][0]
                width = max(4.0, right_x - left_x)
                alpha = round((1.0 - progress) * 210 * strength)
                if alpha > 0:
                    pygame.draw.ellipse(beam, (221, 255, 179, alpha),
                                        (left_x - width * .10, y - 4,
                                         width * 1.20, 8), 2)

        # Small rising motes, kept deterministic to avoid a particle manager.
        for particle in range(15):
            seed = particle * 37
            base = ((seed * 17) % 101) / 100.0
            speed = .72 + (particle % 5) * .055
            t = (base + self.elapsed * speed) % 1.0
            y = BEAM_TOP_Y + t * (BEAM_BASE_Y - BEAM_TOP_Y)
            index = min(len(body_left) - 1, round(t * (len(body_left) - 1)))
            left, right = body_left[index][0], body_right[index][0]
            lane = ((seed * 29) % 97) / 96.0
            lane += math.sin(self.elapsed * 4.0 + particle * 1.7) * .035
            x = left + max(0.0, min(1.0, lane)) * max(1, right - left)
            radius = 1 + (seed % 2)
            pygame.draw.circle(beam, (183, 255, 108, 120 + (seed % 70)), (round(x), round(y)), radius)

        # Rounded focus aperture hides the previously flat-cut far end. The
        # concentric ovals pulse independently and read as a tractor focus
        # field rather than another hard polygon edge.
        top_centre = (body_left[0][0] + body_right[0][0]) * .5
        top_width = max(8.0, body_right[0][0] - body_left[0][0])
        pygame.draw.ellipse(beam, (75, 255, 38, 22 + int(pulse * 15)),
                            (top_centre - top_width * .75, BEAM_TOP_Y - 11,
                             top_width * 1.5, 22), 5)
        pygame.draw.ellipse(beam, (130, 255, 73, 75 + int(pulse * 55)),
                            (top_centre - top_width * .60, BEAM_TOP_Y - 7,
                             top_width * 1.2, 14), 2)
        pygame.draw.ellipse(beam, (220, 255, 170, 105 + int(pulse * 65)),
                            (top_centre - top_width * .34, BEAM_TOP_Y - 3,
                             top_width * .68, 6))

        # A broad source glow sits behind the UFO and visually connects the
        # beam to its emitter.
        base_centre = (outer[2][0] + outer[3][0]) * .5
        base_width = outer[2][0] - outer[3][0]
        pygame.draw.ellipse(beam, (88, 255, 42, 35 + int(pulse * 28)),
                            (base_centre - base_width * .62, BEAM_BASE_Y - 8,
                             base_width * 1.24, 16))

        screen.blit(beam, self._beam_dirty_rect.topleft, self._beam_dirty_rect)

    def _draw_countdown(self, screen):
        screen.blit(self._countdown_shade, (0, 0))
        number = self.countdown_number()
        centre = (WIDTH // 2, HEIGHT // 2 + 4)
        slot_progress = (
            self.countdown_elapsed % COUNTDOWN_SLOT_SECONDS
        ) / COUNTDOWN_SLOT_SECONDS
        pulse = math.sin(slot_progress * math.pi) ** 2
        radius = 77 + round(pulse * 8)
        pygame.draw.circle(screen, (28, 5, 52), centre, radius)
        pygame.draw.circle(screen, (115, 255, 50), centre, radius, 5)
        pygame.draw.circle(screen, (94, 25, 170), centre, radius - 9, 3)
        number_surface = self.countdown_numbers[number]
        screen.blit(number_surface, number_surface.get_rect(center=centre))
        screen.blit(self._countdown_label,
                    self._countdown_label.get_rect(center=(WIDTH // 2, centre[1] + 105)))

    def countdown_number(self):
        # Tiny epsilon keeps exact one-second boundaries from being delayed one
        # frame by binary floating-point representation.
        slot = min(2, int(
            (self.countdown_elapsed + 1e-9) / COUNTDOWN_SLOT_SECONDS
        ))
        return 3 - slot

    def _draw_intro(self, screen):
        screen.blit(self._intro_background, (0, 0))
        title_progress = self._smoothstep(
            self.intro_elapsed / INTRO_TITLE_SECONDS
        )
        if title_progress > 0.0:
            width = max(1, round(self._intro_title.get_width() * title_progress))
            height = max(1, round(self._intro_title.get_height() * title_progress))
            if (width, height) == self._intro_title.get_size():
                title = self._intro_title
            else:
                title = self._animation_scaler(
                    self._intro_title, (width, height)
                )
            screen.blit(title, title.get_rect(center=self._intro_title_rect.center))
        screen.blit(self._intro_foreground, (0, 0))

    def _time_up_title_progress(self):
        scale_seconds = self._time_up_first_duration * .5
        if scale_seconds <= 1e-9:
            return 1.0
        return self._smoothstep(self.time_up_elapsed / scale_seconds)

    def _draw_time_up(self, screen):
        # Direct-to-display alpha blits keep this safe on the 32-bit Pi; the
        # results overlay previously proved that full-screen alpha composition
        # on an intermediate SRCALPHA surface can trigger an ARM Bus Error.
        screen.blit(self._time_up_black, (0, 0))
        title_progress = self._time_up_title_progress()
        if title_progress <= 0.0:
            return
        width = max(1, round(self._time_up_title.get_width() * title_progress))
        height = max(1, round(self._time_up_title.get_height() * title_progress))
        if (width, height) == self._time_up_title.get_size():
            title = self._time_up_title
        else:
            title = self._animation_scaler(
                self._time_up_title, (width, height)
            )
        screen.blit(title, title.get_rect(center=self._time_up_title_rect.center))

    def _draw_frame_overlay(self, screen):
        for surface, position in self.assets.ui_frame_parts:
            screen.blit(surface, position)

    def _draw_game_hud(self, screen):
        self.hud.draw(screen, self.time_left, self.score + self.combo_bonus,
                      self.combo_multiplier, self.streak, self.combo_time_left,
                      self.collect_display_kind,
                      self.collect_display_count,
                      self.collect_display_time)
        if self.phase == "playing" and self.time_flash_age > 0:
            flash = self.hud.flash_surface(self.time_flash_text)
            if flash is not None:
                screen.blit(
                    flash,
                    flash.get_rect(center=(self.hud.time_center_x, 91)),
                )

    def draw(self, screen):
        if self.phase == "intro":
            self._draw_intro(screen)
            return
        if self.phase in ("playing", "countdown", "time_up"):
            self.assets.background.draw(screen)
            # The shadow belongs to the road, below every gameplay effect.
            shadow = self.assets.ufo_shadow
            shadow_rect = shadow.get_rect(
                center=(round(self.player.x), self.player.y + UFO_SHADOW_Y_OFFSET)
            )
            screen.blit(shadow, shadow_rect)
            if ((self.phase == "playing" and self.player.beam)
                    or (self.phase == "time_up" and self._time_up_beam_visible)):
                self._draw_beam(screen)
            # The Blender calibration renders the approaching object even
            # while it is geometrically behind the visible street horizon.
            # Clip the item layer so sprites emerge progressively from behind
            # the road instead of floating in the sky.
            previous_clip = screen.get_clip()
            screen.set_clip(pygame.Rect(0, ITEM_HORIZON_Y, WIDTH, HEIGHT - ITEM_HORIZON_Y))
            for item in sorted(self.items, key=lambda i: i.depth):
                shown_pixels = item.visual_pixels if item.visual_pixels > 0 else item.sprite_pixels()
                size_ratio = (shown_pixels - ITEM_MIN_PIXELS) / (ITEM_MAX_PIXELS - ITEM_MIN_PIXELS)
                idx = max(0, min(AssetBank.STEPS - 1, round(size_ratio * (AssetBank.STEPS - 1))))
                variant = "glow" if item.captured else "shadow"
                sprite = self.assets.item_scaled[item.kind][variant][idx]
                x, y = item.position()
                screen.blit(sprite, sprite.get_rect(center=(int(x), int(y))))
            screen.set_clip(previous_clip)
            ufo_visual_time = self.countdown_elapsed + self.elapsed
            ufo_bob = round(
                math.sin(ufo_visual_time * math.tau * UFO_BOB_HZ) * UFO_BOB_AMPLITUDE
            )
            ufo = self.assets.ufo_body
            ufo_rect = ufo.get_rect(
                center=(round(self.player.x), self.player.y + UFO_VISUAL_Y_OFFSET + ufo_bob)
            )
            screen.blit(ufo, ufo_rect)
            light_frames = self.assets.ufo_light_frames
            light = light_frames[int(ufo_visual_time * UFO_LIGHT_FPS) % len(light_frames)]
            # The 197x60 light sequence is the lower crop of the 197x101 UFO.
            # Bottom alignment places its travelling glow on the green hull strip.
            light_rect = light.get_rect(midbottom=ufo_rect.midbottom)
            screen.blit(light, light_rect)
            self.portraits.draw(screen)
            if self.phase == "countdown":
                self._draw_countdown(screen)
            # The supplied cabinet-style frame sits above every scene element;
            # its title and live values are then drawn on top of the frame.
            self._draw_frame_overlay(screen)
            self._draw_game_hud(screen)
            if self.phase == "time_up":
                self._draw_time_up(screen)
        else:
            self.results.draw(screen, self.result_dict())


def run_munchies_abduction(screen=None, input_provider=None, duration=GAME_SECONDS):
    """Blocking standalone adapter; embedded GUI code should use the class."""
    own_display = screen is None
    if own_display:
        pygame.init(); screen = pygame.display.set_mode((WIDTH, HEIGHT)); pygame.display.set_caption("Munchies Abduction")
    game, clock = MunchiesAbductionGame(duration=duration), pygame.time.Clock()
    while not game.finished:
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT: game.finished = True
            game.handle_event(event)
        if input_provider:
            for event in input_provider(): game.handle_event(event)
        game.update(clock.tick(FPS) / 1000.0); game.draw(screen); pygame.display.flip()
    result = game.result_dict()
    game.close_background()
    if own_display: pygame.quit()
    return result


if __name__ == "__main__":
    print(run_munchies_abduction())
