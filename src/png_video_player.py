"""In-process Pygame player for numbered, full-screen PNG sequences.

The player keeps the SDL/DRM display owned by the main GUI.  PNG decoding is
performed by bounded worker threads, while display-format conversion and
blitting stay on the main thread.  A generation number makes a newly
triggered clip supersede all work belonging to the previous clip.
"""

from __future__ import annotations

import queue
import random
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

import pygame

from video_asset_paths import resolve_sequence_root


class PngSequencePlayer:
    """Stream one 640x480 PNG sequence at 30 FPS with bounded memory use."""

    FRAME_SIZE = (640, 480)
    FPS = 30.0
    CACHE_SIZE = 60
    LOOK_BEHIND = 4
    READY_SIZE = 8
    CONVERTS_PER_TICK = 2
    DECODER_WORKERS = 2

    def __init__(self, root: Path | str | None = None):
        self.root = resolve_sequence_root(root)
        self.clips = self._index_clips()

        self._cache: OrderedDict[int, pygame.Surface] = OrderedDict()
        self._posters: dict[str, pygame.Surface] = {}
        self._requests: queue.Queue[tuple[int, str, int] | None] = queue.Queue(
            maxsize=self.CACHE_SIZE * 2
        )
        self._ready: queue.Queue[tuple[int, str, int, pygame.Surface]] = queue.Queue(
            maxsize=self.READY_SIZE
        )
        self._pending: set[tuple[int, int]] = set()
        self._desired: set[int] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._workers: list[threading.Thread] = []

        self._generation = 0
        self._active_name: str | None = None
        self._last_name: str | None = None
        self._started_at = 0.0
        self._current_index = 0
        self._last_requested_index = -1
        self.active = False
        self.finished = False
        self._closed = False

        self._draw_requests = 0
        self._cache_misses = 0
        self._fallback_gap_total = 0
        self._fallback_gap_max = 0

        # One decoded first frame per clip makes a trigger visually immediate
        # without the prohibitive cost of keeping 60 frames from every clip.
        self._load_posters()

        for worker_index in range(self.DECODER_WORKERS):
            worker = threading.Thread(
                target=self._loader,
                name=f"png-video-loader-{worker_index + 1}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)

        frame_count = sum(len(paths) for paths in self.clips.values())
        print(
            f"[png-video] {len(self.clips)} clip, {frame_count} frame indexelve "
            f"innen: {self.root}"
        )

    @staticmethod
    def _frame_sort_key(path: Path):
        match = re.search(r"(\d+)$", path.stem)
        return (int(match.group(1)) if match else -1, path.name.lower())

    def _index_clips(self) -> dict[str, list[Path]]:
        clips: dict[str, list[Path]] = {}
        if not self.root.is_dir():
            print(f"[png-video] sequence mappa nem talalhato: {self.root}")
            return clips
        for directory in sorted(
            (entry for entry in self.root.iterdir() if entry.is_dir()),
            key=lambda entry: entry.name.lower(),
        ):
            frames = sorted(directory.glob("*.png"), key=self._frame_sort_key)
            if frames:
                clips[directory.name] = frames
        return clips

    def _prepare_frame(self, surface: pygame.Surface) -> pygame.Surface:
        if surface.get_size() != self.FRAME_SIZE:
            surface = pygame.transform.scale(surface, self.FRAME_SIZE)
        # Full-screen sequences are opaque; convert() gives the cheapest
        # possible blit in the active display format and drops unused alpha.
        return surface.convert()

    def _load_posters(self):
        for name, paths in self.clips.items():
            try:
                self._posters[name] = self._prepare_frame(
                    pygame.image.load(str(paths[0]))
                )
            except (pygame.error, OSError) as exc:
                print(f"[png-video] {name} elso frame nem toltheto be: {exc}")

    @property
    def active_name(self) -> str | None:
        return self._active_name

    @property
    def clip_names(self) -> tuple[str, ...]:
        return tuple(self.clips)

    def start_random(self, now: float | None = None) -> str | None:
        """Start a random clip, avoiding the currently/last played one."""
        if not self.clips or self._closed:
            return None
        avoid = self._active_name if self.active else self._last_name
        candidates = [name for name in self.clips if name != avoid]
        if not candidates:
            candidates = list(self.clips)
        name = random.choice(candidates)
        return name if self.start(name, now=now) else None

    def start(self, name: str, now: float | None = None) -> bool:
        """Immediately replace the current clip with ``name``."""
        if name not in self.clips or self._closed:
            return False

        interrupted = self._active_name if self.active else None
        self._cancel_generation()
        self._cache.clear()
        self.active = False
        self.finished = False
        self._active_name = None
        poster = self._posters.get(name)
        if poster is None:
            try:
                poster = self._prepare_frame(
                    pygame.image.load(str(self.clips[name][0]))
                )
                self._posters[name] = poster
            except (pygame.error, OSError) as exc:
                print(f"[png-video] {name} nem indithato: {exc}")
                self._last_name = interrupted or self._last_name
                return False
        self._cache[0] = poster

        self._active_name = name
        self._started_at = time.monotonic() if now is None else now
        self._current_index = 0
        self._last_requested_index = -1
        self.active = True
        self.finished = False
        self._draw_requests = 0
        self._cache_misses = 0
        self._fallback_gap_total = 0
        self._fallback_gap_max = 0
        self._request_window()

        if interrupted:
            print(f"[png-video] megszakitva: {interrupted} -> {name}")
        else:
            print(f"[png-video] inditva: {name}")
        return True

    def _cancel_generation(self):
        with self._lock:
            self._generation += 1
            self._desired.clear()
            self._pending.clear()
        while True:
            try:
                self._requests.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                self._ready.get_nowait()
            except queue.Empty:
                break

    def _request_window(self):
        if not self.active or self._active_name is None:
            return
        paths = self.clips[self._active_name]
        first = max(0, self._current_index - self.LOOK_BEHIND)
        last = min(len(paths), first + self.CACHE_SIZE)
        desired = set(range(first, last))
        generation = self._generation
        name = self._active_name

        with self._lock:
            self._desired = desired
            requested = [
                index for index in range(first, last)
                if index not in self._cache
                and (generation, index) not in self._pending
            ]
            self._pending.update((generation, index) for index in requested)

        for position, index in enumerate(requested):
            try:
                self._requests.put_nowait((generation, name, index))
            except queue.Full:
                with self._lock:
                    self._pending.difference_update(
                        (generation, rest) for rest in requested[position:]
                    )
                break
        self._last_requested_index = self._current_index

    def _loader(self):
        while not self._stop.is_set():
            try:
                request = self._requests.get(timeout=.1)
            except queue.Empty:
                continue
            if request is None:
                break
            generation, name, index = request
            pending_key = (generation, index)
            with self._lock:
                useful = (
                    generation == self._generation
                    and name == self._active_name
                    and index in self._desired
                )
            if not useful:
                with self._lock:
                    self._pending.discard(pending_key)
                continue
            try:
                decoded = pygame.image.load(str(self.clips[name][index]))
                while not self._stop.is_set():
                    with self._lock:
                        still_useful = (
                            generation == self._generation
                            and name == self._active_name
                            and index in self._desired
                        )
                    if not still_useful:
                        with self._lock:
                            self._pending.discard(pending_key)
                        break
                    try:
                        self._ready.put(
                            (generation, name, index, decoded), timeout=.1
                        )
                        break
                    except queue.Full:
                        continue
            except (pygame.error, OSError, IndexError) as exc:
                print(f"[png-video] {name} frame {index} hiba: {exc}")
                with self._lock:
                    self._pending.discard(pending_key)

    def _consume_ready(self):
        converted = inspected = 0
        while converted < self.CONVERTS_PER_TICK and inspected < self.READY_SIZE:
            try:
                generation, name, index, decoded = self._ready.get_nowait()
            except queue.Empty:
                break
            inspected += 1
            pending_key = (generation, index)
            with self._lock:
                useful = (
                    generation == self._generation
                    and name == self._active_name
                    and index in self._desired
                )
            if not useful:
                with self._lock:
                    self._pending.discard(pending_key)
                continue
            try:
                self._cache[index] = self._prepare_frame(decoded)
                self._cache.move_to_end(index)
                converted += 1
                while len(self._cache) > self.CACHE_SIZE:
                    obsolete = next(
                        (key for key in self._cache if key not in self._desired),
                        None,
                    )
                    if obsolete is None:
                        self._cache.popitem(last=False)
                    else:
                        del self._cache[obsolete]
            except pygame.error as exc:
                print(f"[png-video] {name} frame {index} konverzio hiba: {exc}")
            finally:
                with self._lock:
                    self._pending.discard(pending_key)

    def update(self, now: float | None = None):
        self._consume_ready()
        if not self.active or self._active_name is None:
            return
        current_time = time.monotonic() if now is None else now
        elapsed = max(0.0, current_time - self._started_at)
        index = int(elapsed * self.FPS)
        if index >= len(self.clips[self._active_name]):
            self._finish()
            return
        self._current_index = index
        if self._current_index != self._last_requested_index:
            self._request_window()

    def _nearest_cached(self, wanted: int) -> pygame.Surface | None:
        self._draw_requests += 1
        if wanted in self._cache:
            self._cache.move_to_end(wanted)
            return self._cache[wanted]
        self._cache_misses += 1
        if not self._cache:
            return None
        behind = [index for index in self._cache if index <= wanted]
        fallback_index = max(behind) if behind else min(self._cache)
        gap = abs(wanted - fallback_index)
        self._fallback_gap_total += gap
        self._fallback_gap_max = max(self._fallback_gap_max, gap)
        self._cache.move_to_end(fallback_index)
        return self._cache[fallback_index]

    def draw(self, screen: pygame.Surface):
        frame = self._nearest_cached(self._current_index)
        if frame is not None:
            screen.blit(frame, (0, 0))
        else:
            screen.fill((0, 0, 0))

    def _finish(self):
        name = self._active_name
        self._log_stats(name, "vege")
        self._last_name = name
        self.active = False
        self.finished = True
        self._active_name = None
        self._cancel_generation()
        self._cache.clear()

    def stop(self):
        if self.active:
            name = self._active_name
            self._log_stats(name, "leallitva")
            self._last_name = name
        self.active = False
        self.finished = False
        self._active_name = None
        self._cancel_generation()
        self._cache.clear()

    def _log_stats(self, name: str | None, reason: str):
        if not name:
            return
        hit_rate = 100.0
        if self._draw_requests:
            hit_rate = 100.0 * (
                self._draw_requests - self._cache_misses
            ) / self._draw_requests
        average_gap = (
            self._fallback_gap_total / self._cache_misses
            if self._cache_misses else 0.0
        )
        print(
            f"[png-video] {name} {reason}: {hit_rate:.1f}% cache hit, "
            f"{self._cache_misses} fallback, atlag/max gap "
            f"{average_gap:.1f}/{self._fallback_gap_max} frame"
        )

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.stop()
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
        self._posters.clear()
