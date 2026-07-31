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


WIDTH, HEIGHT, FPS = 640, 480, 30
HORIZON_Y, ROAD_BOTTOM = 126, 480
GAME_SECONDS, RESULTS_SECONDS = 60.0, 4.5
COUNTDOWN_SECONDS = 1.5
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

GOOD_VALUES = {
    "pizza": 25000, "burrito": 15000, "chips": 10000,
    "burger": 18000, "taco": 15000, "donut": 12000, "soda": 8000,
}
BAD_KINDS = ("trash_lid", "trash", "police", "junk", "can", "boot")


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
    worker performs file IO/PNG decoding and keeps a bounded request queue;
    this avoids both display-thread races and loading hitches.  A 60-frame
    LRU window keeps memory usage independent from sequence length.
    """

    SOURCE_FPS = 30.0
    OUTPUT_FPS = 15.0
    CACHE_SIZE = 60
    LOOK_BEHIND = 5
    READY_SIZE = 8
    # Converting several decoded RGBA frames in the same game tick creates a
    # visible main-thread spike on a Raspberry Pi. One conversion per tick is
    # enough to sustain the 15 FPS sequence while keeping frame time bounded.
    CONVERTS_PER_TICK = 1

    def __init__(self):
        base = Path(__file__).resolve().parent / "assets" / "Minigame"
        level_dir = base / "Level"
        self.paths = sorted(level_dir.glob("STREET_*.png"))
        if not self.paths:  # backwards-compatible name while assets migrate
            self.paths = sorted(level_dir.glob("Scrolling_background_*.png"))
        self.static_background = self._load_static_background(base / "Level_BG.png")
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
        self._worker = None

        # Guarantee a drawable first frame; the remaining window streams.
        if self.paths:
            try:
                self.cache[0] = self._prepare_frame(pygame.image.load(str(self.paths[0])))
            except pygame.error as exc:
                print(f"[munchies] first street frame load failed: {exc}")
            self._worker = threading.Thread(target=self._loader, name="munchies-bg-loader", daemon=True)
            self._worker.start()
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

    @staticmethod
    def _prepare_frame(surface):
        if surface.get_size() != (WIDTH, HEIGHT):
            surface = pygame.transform.scale(surface, (WIDTH, HEIGHT))
        return surface.convert_alpha() if surface.get_flags() & pygame.SRCALPHA else surface.convert()

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
        # Predict the actual 15 FPS output indices, not every source frame.
        # At 2x speed this naturally requests roughly every fourth PNG. Thus
        # acceleration does not multiply decoder workload.
        # Keep the currently displayed frame pinned even between two output
        # ticks; otherwise the continuously moving playhead could invalidate
        # its request before the worker finishes decoding it.
        desired = [self.current_index]
        slots = list(range(0, self.CACHE_SIZE - self.LOOK_BEHIND))
        slots.extend(range(-1, -self.LOOK_BEHIND - 1, -1))
        for slot in slots:
            seconds_ahead = slot / self.OUTPUT_FPS
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
        output_period = 1.0 / self.OUTPUT_FPS
        if self._output_accumulator >= output_period:
            self._output_accumulator %= output_period
            self.current_index = int(self.playhead) % len(self.paths)
            self._request_window(speed)

    def _nearest_cached(self, wanted):
        if wanted in self.cache:
            self.cache.move_to_end(wanted)
            return self.cache[wanted]
        if not self.cache:
            return None
        # Prefer the closest frame behind the playhead, so a slow storage
        # device never makes the street jump forward unexpectedly.
        count = len(self.paths)
        index = min(self.cache, key=lambda i: (wanted - i) % count)
        self.cache.move_to_end(index)
        return self.cache[index]

    def draw(self, screen):
        screen.blit(self.static_background, (0, 0))
        frame = self._nearest_cached(self.current_index)
        if frame is not None:
            screen.blit(frame, (0, 0))

    def close(self):
        self._stop.set()
        try:
            self._requests.put_nowait(None)
        except queue.Full:
            pass
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=.5)


class AssetBank:
    """Creates cheap cached sprites. Replace `_make_item` with PNG loading later."""

    # One cached sprite for every integer pixel size. The calibration formula
    # remains continuous; quantisation is now at most half a pixel instead of
    # the visibly chunky 4-9 px jumps of the old 16-step cache.
    STEPS = ITEM_MAX_PIXELS - ITEM_MIN_PIXELS + 1

    def __init__(self):
        self.background = StreamingBackground()
        self.ufo_frames = [self._make_ufo(i) for i in range(4)]
        self.item_base = {kind: self._load_item(kind) for kind in (*GOOD_VALUES, *BAD_KINDS)}
        self.item_scaled = {
            kind: [self._scale_item(sprite, self._item_cache_size(i))
                   for i in range(self.STEPS)]
            for kind, sprite in self.item_base.items()
        }

    @classmethod
    def _item_cache_size(cls, index):
        return ITEM_MIN_PIXELS + index

    @staticmethod
    def _scale_item(sprite, target_size):
        longest = max(sprite.get_width(), sprite.get_height())
        width = max(1, round(sprite.get_width() * target_size / longest))
        height = max(1, round(sprite.get_height() * target_size / longest))
        return pygame.transform.scale(sprite, (width, height))

    @classmethod
    def _load_item(cls, kind):
        path = Path(__file__).resolve().parent / "assets" / "Minigame" / "Sprites" / "extracted" / f"{kind}.png"
        if path.is_file():
            try:
                return pygame.image.load(str(path)).convert_alpha()
            except pygame.error as exc:
                print(f"[munchies] sprite load failed ({kind}): {exc}")
        return cls._make_item(kind)

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


@dataclass
class RoadItem:
    kind: str
    world_x: float
    depth: float = 0.0
    captured: bool = False
    travelled_frames: float = 0.0
    visual_pixels: float = 0.0
    capture_elapsed: float = 0.0
    capture_start_x: float = 0.0
    capture_start_y: float = 0.0
    capture_start_pixels: float = 0.0
    capture_x: float = 0.0
    capture_y: float = 0.0

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
    BOXES = ((8, 8, 155, "TIME LEFT"),
             (174, 8, 292, "BONUS"),
             (477, 8, 155, "MUNCHIES"))

    def __init__(self):
        self.label, self.value = _font(16), _font(27)
        self.box_surfaces = []
        for _, _, width, label in self.BOXES:
            panel = pygame.Surface((width, 64), pygame.SRCALPHA)
            pygame.draw.rect(panel, (14, 3, 29), (0, 0, width, 64), border_radius=13)
            pygame.draw.rect(panel, (109, 15, 194), (0, 0, width, 64), 3, border_radius=13)
            label_surface = _text(self.label, label, (170, 255, 62))
            panel.blit(label_surface, label_surface.get_rect(center=(width // 2, 15)))
            self.box_surfaces.append(panel.convert_alpha())
        self._value_keys = [None] * len(self.BOXES)
        self._value_surfaces = [None] * len(self.BOXES)
        self._combo_key = None
        self._combo_surface = None
        self._flash_surfaces = {
            "+1 SEC": _text(self.value, "+1 SEC", (120, 255, 60), width=3).convert_alpha(),
            "-2 SEC": _text(self.value, "-2 SEC", (255, 78, 90), width=3).convert_alpha(),
        }

    def _value_surface(self, slot, value):
        if value != self._value_keys[slot]:
            self._value_keys[slot] = value
            self._value_surfaces[slot] = _text(self.value, value).convert_alpha()
        return self._value_surfaces[slot]

    def flash_surface(self, value):
        return self._flash_surfaces.get(value)

    def draw(self, screen, remaining, score, count, combo):
        values = (f"{max(0, remaining):04.1f}", f"{score:,}", f"{count:02d}/15")
        for slot, ((x, y, width, _), panel, value) in enumerate(
                zip(self.BOXES, self.box_surfaces, values)):
            screen.blit(panel, (x, y))
            value_surface = self._value_surface(slot, value)
            screen.blit(value_surface, value_surface.get_rect(center=(x + width // 2, y + 43)))
        if combo > 1:
            combo_key = f"COMBO x{combo}"
            if combo_key != self._combo_key:
                self._combo_key = combo_key
                self._combo_surface = _text(
                    self.value, combo_key, (255, 91, 213)).convert_alpha()
            screen.blit(self._combo_surface, (14, 82))


class ResultsOverlay:
    def __init__(self):
        self.title, self.row, self.total = _font(55), _font(25), _font(48)
        self._cache_key = None
        self._cached_surface = None

    def draw(self, screen, result):
        cache_key = (result["collected_count"], result["combo_bonus"], result["total_bonus"])
        if cache_key != self._cache_key:
            self._cache_key = cache_key
            layer = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            layer.fill((7, 0, 23, 220))
            pygame.draw.rect(layer, (44, 7, 75), (52, 86, 536, 326), border_radius=30)
            pygame.draw.rect(layer, (126, 34, 204), (52, 86, 536, 326), 6, border_radius=30)
            title = _text(self.title, "MUNCHIES", (111, 255, 43), width=4); layer.blit(title, title.get_rect(center=(320, 75)))
            sub = _text(self.row, "ABDUCTION RESULTS", (255, 88, 211)); layer.blit(sub, sub.get_rect(center=(320, 122)))
            rows = (("FOOD COLLECTED", result["collected_count"]), ("COMBO BONUS", f'{result["combo_bonus"]:,}'))
            for i, (label, value) in enumerate(rows):
                y = 178 + i * 52
                layer.blit(_text(self.row, label), (95, y)); val = _text(self.row, value, (255, 224, 63)); layer.blit(val, val.get_rect(right=545, top=y))
            pygame.draw.line(layer, (158, 42, 221), (85, 282), (555, 282), 3)
            total = _text(self.total, f'{result["total_bonus"]:,}', (255, 235, 70), width=3); layer.blit(total, total.get_rect(center=(320, 326)))
            footer = _text(self.row, "WELL ABDUCTED!  RETURNING TO PINBALL...", (128, 255, 71)); layer.blit(footer, footer.get_rect(center=(320, 382)))
            self._cached_surface = layer.convert_alpha()
        screen.blit(self._cached_surface, (0, 0))


class MunchiesAbductionGame:
    """Embeddable stateful game. `finished` becomes true after results."""

    def __init__(self, duration=GAME_SECONDS, rng=None, sound_hook: Optional[Callable[[str], None]] = None):
        self.duration, self.rng, self.sound_hook = duration, rng or random.Random(), sound_hook
        self.assets, self.player, self.hud, self.results = AssetBank(), PlayerUFO(), HUD(), ResultsOverlay()
        self.items, self.elapsed, self.spawn_timer = [], 0.0, 0.25
        self.time_left = float(duration)
        self.score = self.combo_bonus = self.collected_count = self.streak = 0
        self.phase, self.countdown_elapsed = "countdown", 0.0
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
        # The beam only occupies a narrow horizontal strip, but previously a
        # fresh full-screen alpha surface was allocated and blended every
        # frame. Reuse it and touch/blit only the actual beam bounds.
        self._beam_layer = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA).convert_alpha()
        self._beam_dirty_rect = pygame.Rect(
            0, BEAM_TOP_Y - 14, WIDTH, BEAM_BASE_Y - BEAM_TOP_Y + 32
        )
        self._beam_render_fps = 15.0 if _is_raspberry_pi() else float(FPS)
        self._beam_render_tick = -1
        self._beam_render_x = self.player.x
        self._music_active = False
        self.time_flash_text = ""
        self.time_flash_age = 0.0
        self._start_music()
        # Keep a spatially even pipeline across the 92.4 m road. Without
        # this, all eight objects would bunch up at the horizon on startup.
        for slot in range(8):
            self._spawn((slot + 1) * ITEM_APPROACH_FRAMES / 9.0)

    def _sound(self, name):
        if self.sound_hook: self.sound_hook(name)

    def _start_music(self):
        music_dir = Path(__file__).resolve().parent / "assets" / "Minigame" / "Music"
        tracks = []
        for pattern in ("*.mp3", "*.ogg", "*.wav"):
            tracks.extend(sorted(music_dir.glob(pattern)))
        if not tracks:
            return
        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
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
            pygame.mixer.music.fadeout(fade_ms)
        except pygame.error:
            pass
        self._music_active = False

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

    def _spawn(self, travelled_frames=0.0):
        good = self.rng.random() < .72
        kind = self.rng.choice(tuple(GOOD_VALUES) if good else BAD_KINDS)
        progress = travelled_frames / ITEM_APPROACH_FRAMES
        item = RoadItem(
            kind, self.rng.uniform(-.88, .88), depth=progress,
            travelled_frames=travelled_frames,
        )
        item.visual_pixels = item.sprite_pixels()
        self.items.append(item)

    @property
    def combo_multiplier(self):
        return 3 if self.streak >= 5 else 2 if self.streak >= 3 else 1

    def update(self, dt):
        dt = min(dt, .1)
        self.beam_shocks = [
            (age + dt, start_t) for age, start_t in self.beam_shocks
            if age + dt < .55
        ]
        if self.phase == "results":
            self.results_elapsed += dt
            self.finished = self.results_elapsed >= RESULTS_SECONDS
            return
        if self.phase == "countdown":
            # The road stays still, but decoded frames are moved into video
            # memory one at a time. This turns the countdown into a free
            # pre-roll buffer and prevents a conversion burst on "1" -> play.
            self.assets.background.prime()
            self.countdown_elapsed += dt
            if self.countdown_elapsed >= COUNTDOWN_SECONDS - 1e-9:
                self.countdown_elapsed = COUNTDOWN_SECONDS
                self.phase = "playing"
            return
        self.elapsed += dt
        self.time_left = max(0.0, self.time_left - dt)
        self.time_flash_age = max(0.0, self.time_flash_age - dt)
        progress = min(1.0, self.elapsed / max(.001, self.duration))
        # Slow, readable launch; increasingly large source-frame steps create
        # the acceleration without changing the GUI's stable 30 FPS cadence.
        self.world_speed = .6 + 1.8 * progress ** 1.35
        self.assets.background.update(dt, self.world_speed)
        self.player.update(dt)
        self.spawn_timer -= dt * self.world_speed
        if self.spawn_timer <= 0 and len(self.items) < 8:
            self._spawn(); self.spawn_timer = max(.38, .88 - self.elapsed * .018) * self.rng.uniform(.8, 1.15)
        survivors = []
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
            # Edge contact counts: the old centre-point-only test made a
            # pickup wait until roughly 80% of the sprite had entered.
            in_beam = self.player.beam_contains(x, y, item.visual_pixels * .42)
            if in_beam and item.depth > .55:
                if item.good:
                    self.streak += 1; mult = self.combo_multiplier; base = GOOD_VALUES[item.kind]
                    self.score += base; self.combo_bonus += base * (mult - 1); self.collected_count += 1; self._sound("collect")
                    self.time_left += 1.0
                    self.time_flash_text, self.time_flash_age = "+1 SEC", .75
                    if self.streak in (3, 5): self._sound("combo")
                else:
                    self.score = max(0, self.score - 5000); self.streak = 0; self.player.stun = .55; self._sound("bad_pickup")
                    self.time_left = max(0.0, self.time_left - 2.0)
                    self.time_flash_text, self.time_flash_age = "-2 SEC", .75
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
            elif item.good:
                self.streak = 0
        self.items = survivors
        if self.time_left <= 0.0:
            self.phase = "results"; self.player.beam = False; self._sound("results")
            self.close_background()

    def close_background(self):
        if not self._background_closed:
            self.assets.background.close()
            self._background_closed = True
        self._stop_music()

    def result_dict(self):
        return {"total_bonus": self.score + self.combo_bonus, "collected_count": self.collected_count,
                "combo_bonus": self.combo_bonus, "base_score": self.score, "best_streak": self.streak}

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
        pulse = (math.sin((self.countdown_elapsed % .5) / .5 * math.pi) ** 2)
        radius = 77 + round(pulse * 8)
        pygame.draw.circle(screen, (28, 5, 52), centre, radius)
        pygame.draw.circle(screen, (115, 255, 50), centre, radius, 5)
        pygame.draw.circle(screen, (94, 25, 170), centre, radius - 9, 3)
        number_surface = self.countdown_numbers[number]
        screen.blit(number_surface, number_surface.get_rect(center=centre))
        screen.blit(self._countdown_label,
                    self._countdown_label.get_rect(center=(WIDTH // 2, centre[1] + 105)))

    def countdown_number(self):
        # Tiny epsilon keeps exact 30 FPS boundaries at 15/30 and 30/30 from
        # being delayed one frame by binary floating-point representation.
        slot = min(2, int((self.countdown_elapsed + 1e-9) / .5))
        return 3 - slot

    def draw(self, screen):
        self.assets.background.draw(screen)
        if self.phase in ("playing", "countdown"):
            if self.phase == "playing" and self.player.beam:
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
                sprite = self.assets.item_scaled[item.kind][idx]; x, y = item.position(); screen.blit(sprite, sprite.get_rect(center=(int(x), int(y))))
            screen.set_clip(previous_clip)
            ufo = self.assets.ufo_frames[int(self.elapsed * 7) % len(self.assets.ufo_frames)]
            screen.blit(ufo, ufo.get_rect(center=(int(self.player.x), self.player.y)))
            self.hud.draw(screen, self.time_left, self.score + self.combo_bonus,
                          self.collected_count, self.combo_multiplier)
            if self.phase == "playing" and self.time_flash_age > 0:
                flash = self.hud.flash_surface(self.time_flash_text)
                if flash is not None:
                    screen.blit(flash, flash.get_rect(center=(84, 91)))
            if self.phase == "countdown":
                self._draw_countdown(screen)
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
