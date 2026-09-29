"""Standalone, three-lane guitar chart editor.

Notes performed with A/S/D are corrected to a BPM grid immediately. Both the
performed and corrected times are saved, so a chart can be re-quantized later.
Audio and chart files live in ``src/assets/GuitarHero/Songs``.
"""

from __future__ import annotations

import argparse
from array import array
import copy
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

import pygame


WIDTH, HEIGHT, FPS = 640, 480, 60
SONGS_DIR = Path(__file__).resolve().parent / "assets" / "GuitarHero" / "Songs"
STEM_CACHE_DIR = SONGS_DIR.parent / ".stem_cache"
DEMUCS_MODEL = "htdemucs_6s"
AUDIO_EXTENSIONS = {".ogg", ".mp3", ".wav", ".flac"}
GRID_OPTIONS = ((1, "1/4"), (2, "1/8"), (3, "1/8T"),
                (4, "1/16"), (6, "1/16T"), (8, "1/32"))
LANE_KEYS = {pygame.K_a: 0, pygame.K_s: 1, pygame.K_d: 2,
             pygame.K_1: 0, pygame.K_2: 1, pygame.K_3: 2}
LANE_NAMES = ("BAL", "SHOOT", "JOBB")
LANE_COLORS = ((255, 92, 92), (255, 220, 72), (84, 210, 255))
AUTO_SAMPLE_RATE = 11_025
GUITAR_SPECTRUM_START_HZ = 80
GUITAR_SPECTRUM_STOP_HZ = 3200
GUITAR_BANDS_HZ = ((80, 300), (300, 900), (900, 3200))


def clamp(value, low, high):
    return max(low, min(high, value))


def format_time(milliseconds):
    milliseconds = max(0, int(round(milliseconds)))
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def quantize_time_ms(raw_time_ms, bpm, beat_offset_ms, subdivision, strength=1.0):
    """Pull an absolute time towards the nearest musical grid line."""
    bpm = clamp(float(bpm), 20.0, 400.0)
    subdivision = max(1, int(subdivision))
    strength = clamp(float(strength), 0.0, 1.0)
    step_ms = 60_000.0 / bpm / subdivision
    index = round((float(raw_time_ms) - beat_offset_ms) / step_ms)
    snapped = beat_offset_ms + index * step_ms
    result = float(raw_time_ms) + (snapped - float(raw_time_ms)) * strength
    return max(0, int(round(result)))


def detect_note_candidates(samples, sample_rate=AUTO_SAMPLE_RATE,
                           min_gap_ms=90):
    """Return ``(time_ms, lane)`` candidates from mono signed-16 PCM.

    This is deliberately lightweight: short-time energy finds attacks, while
    zero-crossing density splits them into three repeatable pseudo-frequency
    lanes.  It needs no NumPy/librosa installation on the cabinet.
    """
    if not samples or sample_rate <= 0:
        return []
    frame_size = max(64, round(sample_rate * 0.023))
    energies, crossings = [], []
    for start in range(0, len(samples) - frame_size + 1, frame_size):
        frame = samples[start:start + frame_size]
        energies.append(sum(abs(value) for value in frame) / frame_size)
        crossings.append(sum(
            1 for left, right in zip(frame, frame[1:])
            if (left < 0 <= right) or (left >= 0 > right)
        ))
    if len(energies) < 4 or max(energies, default=0) < 32:
        return []

    novelty = []
    for index, energy in enumerate(energies):
        history = energies[max(0, index - 8):index]
        baseline = statistics.median(history) if history else energy
        novelty.append(max(0.0, energy - baseline))
    positive = [value for value in novelty if value > 0]
    if not positive:
        return []
    median = statistics.median(positive)
    deviations = [abs(value - median) for value in positive]
    # A median/MAD kuszob a halkabb zeneken is mukodik; a tul magas
    # szorzo a ritka, eros attackokat eppen sajat maguk miatt nyelne el.
    threshold = max(64.0, median + 0.75 * statistics.median(deviations))
    gap_frames = max(1, round(min_gap_ms / 1000.0 * sample_rate / frame_size))

    peaks = []
    for index in range(1, len(novelty) - 1):
        if (novelty[index] < threshold
                or novelty[index] < novelty[index - 1]
                or novelty[index] < novelty[index + 1]):
            continue
        if peaks and index - peaks[-1] < gap_frames:
            if novelty[index] > novelty[peaks[-1]]:
                peaks[-1] = index
            continue
        peaks.append(index)
    if not peaks:
        return []

    peak_crossings = sorted(crossings[index] for index in peaks)
    low_cut = peak_crossings[len(peak_crossings) // 3]
    high_cut = peak_crossings[(len(peak_crossings) * 2) // 3]
    result = []
    fallback_pattern = (0, 1, 2, 1)
    for peak_number, index in enumerate(peaks):
        crossing_count = crossings[index]
        if low_cut == high_cut:
            lane = fallback_pattern[peak_number % len(fallback_pattern)]
        else:
            lane = 0 if crossing_count <= low_cut else (
                2 if crossing_count >= high_cut else 1)
        time_ms = round(index * frame_size * 1000.0 / sample_rate)
        result.append((time_ms, lane))
    return result


def decode_audio_mono_pcm(audio_path, sample_rate=AUTO_SAMPLE_RATE):
    """Decode any ffmpeg-supported song to a small mono analysis stream."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("Az automata note-keresohoz ffmpeg szukseges")
    command = [
        ffmpeg, "-v", "error", "-i", str(audio_path), "-vn",
        "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "pipe:1",
    ]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    completed = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, **kwargs)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "Az audio elemzese sikertelen")
    samples = array("h")
    samples.frombytes(completed.stdout[:len(completed.stdout) // 2 * 2])
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def cached_guitar_stem_path(audio_path):
    return (STEM_CACHE_DIR / DEMUCS_MODEL / Path(audio_path).stem
            / "guitar.wav")


def ensure_guitar_stem(audio_path, progress_hook=None):
    """Return a real guitar stem, creating and caching it with HTDemucs."""
    audio_path = Path(audio_path).resolve()
    companion = audio_path.with_name(f"{audio_path.stem}.guitar.wav")
    if companion.is_file():
        return companion
    cached = cached_guitar_stem_path(audio_path)
    if cached.is_file() and cached.stat().st_mtime >= audio_path.stat().st_mtime:
        return cached

    demucs = shutil.which("demucs")
    if demucs is None:
        raise RuntimeError(
            "Nincs Demucs. PC-n: python -m pip install demucs soundfile")
    STEM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        demucs, "-n", DEMUCS_MODEL, "--two-stems", "guitar",
        "--out", str(STEM_CACHE_DIR), str(audio_path),
    ]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **kwargs)
    started_at = time.monotonic()
    while process.poll() is None:
        if progress_hook is not None:
            progress_hook(time.monotonic() - started_at)
        time.sleep(0.05)
    if process.returncode != 0 or not cached.is_file():
        raise RuntimeError(
            "A guitar stem leválasztása sikertelen; futtasd terminálból a "
            "demucs -n htdemucs_6s parancsot a részletekért")
    return cached


def _parse_ppm_rgb(payload):
    """Parse the fixed P6 stream emitted by ffmpeg's ppm encoder."""
    first, second, third, pixels = payload.split(b"\n", 3)
    if first != b"P6" or third.strip() != b"255":
        raise RuntimeError("Ismeretlen spektrum-kep formatum")
    width, height = (int(value) for value in second.split())
    expected = width * height * 3
    if len(pixels) < expected:
        raise RuntimeError("Hianyos spektrum-kep")
    return width, height, pixels[:expected]


def build_guitar_heatmap(audio_path, duration_ms, width=None, height=192):
    """Render and reduce an audio spectrogram to three guitar-band lanes."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("A gitar-hoterkephez ffmpeg szukseges")
    if width is None:
        width = int(clamp(round(duration_ms / 35.0), 1024, 8192))
    spectrum_filter = (
        f"showspectrumpic=s={width}x{height}:legend=0:color=intensity:"
        f"scale=log:fscale=log:start={GUITAR_SPECTRUM_START_HZ}:"
        f"stop={GUITAR_SPECTRUM_STOP_HZ}:win_func=hann"
    )
    command = [
        ffmpeg, "-v", "error", "-i", str(audio_path), "-lavfi",
        spectrum_filter, "-frames:v", "1", "-f", "image2pipe",
        "-vcodec", "ppm", "pipe:1",
    ]
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    completed = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, **kwargs)
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "A gitar-spektrum eloallitasa sikertelen")
    width, height, pixels = _parse_ppm_rgb(completed.stdout)

    log_range = math.log(
        GUITAR_SPECTRUM_STOP_HZ / GUITAR_SPECTRUM_START_HZ)

    def frequency_y(frequency):
        ratio = math.log(frequency / GUITAR_SPECTRUM_START_HZ) / log_range
        return int(clamp(round((1.0 - ratio) * (height - 1)), 0, height - 1))

    band_rows = []
    for low_hz, high_hz in GUITAR_BANDS_HZ:
        top, bottom = frequency_y(high_hz), frequency_y(low_hz)
        band_rows.append(range(min(top, bottom), max(top, bottom) + 1))

    raw_columns = []
    for x in range(width):
        values = []
        for rows in band_rows:
            total = 0
            for y in rows:
                offset = (y * width + x) * 3
                total += max(pixels[offset:offset + 3])
            values.append(total / max(1, len(rows)))
        raw_columns.append(tuple(values))

    # Lane-enkenti robusztus normalizalas: a halkan kevert gitar is latszik,
    # de egyetlen hangos dobutes nem egeti feherre az egesz timeline-t.
    scales = []
    for lane in range(3):
        ordered = sorted(column[lane] for column in raw_columns)
        percentile = ordered[min(len(ordered) - 1, round(len(ordered) * 0.95))]
        scales.append(max(1.0, percentile))
    return [tuple(clamp(column[lane] / scales[lane], 0.0, 1.0)
                  for lane in range(3)) for column in raw_columns]


def build_activity_envelope(samples, column_count):
    """Return absolute guitar-stem loudness per heatmap column, normalized."""
    if not samples or column_count <= 0:
        return []
    samples_per_column = len(samples) / column_count
    raw = []
    for column in range(column_count):
        start = round(column * samples_per_column)
        end = max(start + 1, round((column + 1) * samples_per_column))
        frame = samples[start:min(end, len(samples))]
        raw.append(math.sqrt(sum(value * value for value in frame)
                             / max(1, len(frame))))
    ordered = sorted(raw)
    noise = ordered[min(len(ordered) - 1, round(len(ordered) * 0.10))]
    reference = ordered[min(len(ordered) - 1, round(len(ordered) * 0.95))]
    scale = max(1.0, reference - noise)
    normalized = [clamp((value - noise) / scale, 0.0, 1.0) for value in raw]
    # Harom oszlopos simitas: a blokkhatartol nem keletkezik hamis attack.
    return [sum(normalized[max(0, i - 1):min(len(normalized), i + 2)])
            / len(normalized[max(0, i - 1):min(len(normalized), i + 2)])
            for i in range(len(normalized))]


def candidates_from_guitar_heatmap(heatmap, duration_ms, min_gap_ms=90,
                                   activity=None):
    """Find guitar-rhythm attacks from positive spectral flux."""
    if len(heatmap) < 4 or duration_ms <= 0:
        return []
    flux = []
    for index, column in enumerate(heatmap):
        history = heatmap[max(0, index - 5):index]
        baseline = (
            tuple(statistics.median(row[lane] for row in history)
                  for lane in range(3))
            if history else column
        )
        flux.append(tuple(max(0.0, column[lane] - baseline[lane])
                          for lane in range(3)))

    lane_thresholds = []
    for lane in range(3):
        positive = [row[lane] for row in flux if row[lane] > 0]
        if not positive:
            lane_thresholds.append(1.0)
            continue
        median = statistics.median(positive)
        deviation = statistics.median(abs(value - median) for value in positive)
        lane_thresholds.append(max(0.035, median + 1.5 * deviation))

    spectral_scores = [
        max(row[lane] / lane_thresholds[lane] for lane in range(3))
        for row in flux
    ]
    if activity is None or len(activity) != len(heatmap):
        activity = [1.0] * len(heatmap)
    activity_flux = []
    for index, value in enumerate(activity):
        history = activity[max(0, index - 6):index]
        baseline = statistics.median(history) if history else value
        activity_flux.append(max(0.0, value - baseline))
    positive_activity_flux = [value for value in activity_flux if value > 0]
    activity_threshold = (
        max(0.025, statistics.median(positive_activity_flux) * 1.5)
        if positive_activity_flux else 1.0
    )
    scores = []
    for index, spectral_score in enumerate(spectral_scores):
        # A relative spektrumcsucs csak akkor ervenyes, ha a guitar stem
        # abszolut hangereje sem halk. Ez szuri ki a szunetben felerositett
        # dob/cin athallast.
        if activity[index] < 0.13:
            scores.append(0.0)
            continue
        onset_boost = activity_flux[index] / activity_threshold
        scores.append(spectral_score * (0.45 + activity[index])
                      + onset_boost * 0.70)
    gap_columns = max(1, round(min_gap_ms / duration_ms * len(heatmap)))
    peaks = []
    for index in range(1, len(scores) - 1):
        if scores[index] < 1.0 or scores[index] < scores[index - 1] \
                or scores[index] < scores[index + 1]:
            continue
        if peaks and index - peaks[-1] < gap_columns:
            if scores[index] > scores[peaks[-1]]:
                peaks[-1] = index
            continue
        peaks.append(index)

    candidates = []
    previous_lane = None
    for index in peaks:
        strengths = [flux[index][lane] / lane_thresholds[lane]
                     for lane in range(3)]
        lane = max(range(3), key=strengths.__getitem__)
        # Az azonos savban egymas utan jovo attackoknal a masodik legerosebb
        # savra valtunk, hogy a chart jatszhato ritmust adjon, ne egy oszlopot.
        if lane == previous_lane:
            lane = sorted(range(3), key=strengths.__getitem__, reverse=True)[1]
        previous_lane = lane
        candidates.append((round(index / (len(heatmap) - 1) * duration_ms), lane))
    return candidates


class ChartDocument:
    FORMAT_VERSION = 1
    TAP_DURATION_THRESHOLD_MS = 160

    def __init__(self, audio_path):
        self.audio_path = Path(audio_path).resolve()
        self.chart_path = self.audio_path.with_suffix(".chart.json")
        self.title = self.audio_path.stem
        self.bpm = 120.0
        self.beat_offset_ms = 0
        self.subdivision = 4
        self.quantize_enabled = True
        self.quantize_strength = 1.0
        self.input_latency_ms = 0
        self.notes = []
        self.selected_id = None
        self.dirty = False
        self._next_id = 1
        self._undo, self._redo = [], []
        if self.chart_path.is_file():
            self.load()

    @property
    def grid_label(self):
        return next((label for value, label in GRID_OPTIONS
                     if value == self.subdivision), f"x{self.subdivision}")

    @property
    def grid_step_ms(self):
        return 60_000.0 / self.bpm / self.subdivision

    def _snapshot(self):
        return copy.deepcopy(self.notes)

    def checkpoint(self):
        self._undo.append(self._snapshot())
        self._undo = self._undo[-200:]
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self.notes, self.selected_id, self.dirty = self._undo.pop(), None, True
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self.notes, self.selected_id, self.dirty = self._redo.pop(), None, True
        return True

    def corrected_time(self, raw_time_ms):
        corrected = float(raw_time_ms) - self.input_latency_ms
        if not self.quantize_enabled:
            return max(0, int(round(corrected)))
        return quantize_time_ms(corrected, self.bpm, self.beat_offset_ms,
                                self.subdivision, self.quantize_strength)

    def find_note(self, note_id):
        return next((note for note in self.notes
                     if note.get("_id") == note_id), None)

    def add_note(self, lane, raw_time_ms):
        lane = int(clamp(int(lane), 0, 2))
        corrected = self.corrected_time(raw_time_ms)
        duplicate = next((note for note in self.notes
                          if note["lane"] == lane
                          and abs(note["time_ms"] - corrected) <= 1), None)
        if duplicate is not None:
            self.selected_id = duplicate["_id"]
            return None
        self.checkpoint()
        note_id = self._next_id
        self._next_id += 1
        self.notes.append({
            "_id": note_id, "time_ms": corrected, "lane": lane,
            "duration_ms": 0,
            "raw_time_ms": max(0, int(round(raw_time_ms))),
            "raw_duration_ms": 0,
        })
        self.notes.sort(key=lambda item: (item["time_ms"], item["lane"]))
        self.selected_id, self.dirty = note_id, True
        return note_id

    def finish_note(self, note_id, raw_end_ms):
        note = self.find_note(note_id)
        if note is None:
            return False
        raw_duration = max(0, int(round(raw_end_ms - note["raw_time_ms"])))
        note["raw_duration_ms"] = raw_duration
        if raw_duration < self.TAP_DURATION_THRESHOLD_MS:
            note["duration_ms"] = 0
        else:
            note["duration_ms"] = max(
                1, self.corrected_time(raw_end_ms) - note["time_ms"])
        self.dirty = True
        return True

    def resize_note_start(self, note_id, raw_start_ms):
        """Move a note's left edge while keeping its right edge fixed."""
        note = self.find_note(note_id)
        if note is None:
            return False
        raw_end = note["raw_time_ms"] + note.get("raw_duration_ms", 0)
        raw_start = int(round(clamp(raw_start_ms, 0, raw_end)))
        note["raw_time_ms"] = raw_start
        note["raw_duration_ms"] = raw_end - raw_start
        note["time_ms"] = self.corrected_time(raw_start)
        self._update_note_duration(note, raw_end)
        self.notes.sort(key=lambda item: (item["time_ms"], item["lane"]))
        self.dirty = True
        return True

    def resize_note_end(self, note_id, raw_end_ms):
        """Move a note's right edge while keeping its left edge fixed."""
        note = self.find_note(note_id)
        if note is None:
            return False
        raw_end = max(note["raw_time_ms"], int(round(raw_end_ms)))
        note["raw_duration_ms"] = raw_end - note["raw_time_ms"]
        self._update_note_duration(note, raw_end)
        self.dirty = True
        return True

    def _update_note_duration(self, note, raw_end_ms):
        raw_duration = note.get("raw_duration_ms", 0)
        note["duration_ms"] = (
            max(1, self.corrected_time(raw_end_ms) - note["time_ms"])
            if raw_duration >= self.TAP_DURATION_THRESHOLD_MS else 0
        )

    def add_generated_notes(self, candidates, replace=False):
        """Add auto-detected raw ``(time_ms, lane)`` candidates in one undo."""
        candidates = list(candidates)
        if not candidates:
            return 0
        self.checkpoint()
        if replace:
            self.notes.clear()
            self.selected_id = None
        occupied = {(note["lane"], note["time_ms"]) for note in self.notes}
        added = 0
        for raw_time_ms, lane in candidates:
            lane = int(clamp(int(lane), 0, 2))
            corrected = self.corrected_time(raw_time_ms)
            if any(existing_lane == lane and abs(existing_time - corrected) <= 1
                   for existing_lane, existing_time in occupied):
                continue
            note_id = self._next_id
            self._next_id += 1
            self.notes.append({
                "_id": note_id,
                "time_ms": corrected,
                "lane": lane,
                "duration_ms": 0,
                "raw_time_ms": max(0, int(round(raw_time_ms))),
                "raw_duration_ms": 0,
            })
            occupied.add((lane, corrected))
            added += 1
        self.notes.sort(key=lambda item: (item["time_ms"], item["lane"]))
        self.dirty = self.dirty or added > 0 or replace
        return added

    def delete_selected(self):
        if self.selected_id is None:
            return False
        old_count = len(self.notes)
        self.checkpoint()
        self.notes = [n for n in self.notes if n["_id"] != self.selected_id]
        if len(self.notes) == old_count:
            self._undo.pop()
            return False
        self.selected_id, self.dirty = None, True
        return True

    def move_note(self, note_id, raw_time_ms, lane):
        note = self.find_note(note_id)
        if note is None:
            return False
        note["raw_time_ms"] = max(0, int(round(raw_time_ms)))
        note["time_ms"] = self.corrected_time(raw_time_ms)
        note["lane"] = int(clamp(int(lane), 0, 2))
        raw_duration = note.get("raw_duration_ms", 0)
        if raw_duration >= self.TAP_DURATION_THRESHOLD_MS:
            raw_end = note["raw_time_ms"] + raw_duration
            note["duration_ms"] = max(
                1, self.corrected_time(raw_end) - note["time_ms"])
        self.notes.sort(key=lambda item: (item["time_ms"], item["lane"]))
        self.dirty = True
        return True

    def requantize_all(self):
        self.checkpoint()
        for note in self.notes:
            raw_start = note.get("raw_time_ms", note["time_ms"])
            raw_duration = note.get("raw_duration_ms", note.get("duration_ms", 0))
            note["time_ms"] = self.corrected_time(raw_start)
            note["duration_ms"] = (
                max(1, self.corrected_time(raw_start + raw_duration)
                    - note["time_ms"])
                if raw_duration >= self.TAP_DURATION_THRESHOLD_MS else 0
            )
        self.notes.sort(key=lambda item: (item["time_ms"], item["lane"]))
        self.dirty = True

    def load(self):
        with self.chart_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.title = str(data.get("title") or self.audio_path.stem)
        self.bpm = clamp(float(data.get("bpm", 120.0)), 20.0, 400.0)
        self.beat_offset_ms = int(data.get("beat_offset_ms", 0))
        self.subdivision = max(1, int(data.get("grid_subdivision", 4)))
        self.quantize_enabled = bool(data.get("quantize_enabled", True))
        self.quantize_strength = clamp(
            float(data.get("quantize_strength", 1.0)), 0.0, 1.0)
        self.input_latency_ms = int(data.get("input_latency_ms", 0))
        self.notes = []
        for source in data.get("notes", []):
            try:
                note = {
                    "time_ms": max(0, int(source["time_ms"])),
                    "lane": int(clamp(int(source["lane"]), 0, 2)),
                    "duration_ms": max(0, int(source.get("duration_ms", 0))),
                    "raw_time_ms": max(0, int(source.get(
                        "raw_time_ms", source["time_ms"]))),
                    "raw_duration_ms": max(0, int(source.get(
                        "raw_duration_ms", source.get("duration_ms", 0)))),
                }
            except (KeyError, TypeError, ValueError):
                continue
            self.notes.append(note)
        self.notes.sort(key=lambda item: (item["time_ms"], item["lane"]))
        self._next_id = 1
        for note in self.notes:
            note["_id"] = self._next_id
            self._next_id += 1
        self._undo.clear()
        self._redo.clear()
        self.selected_id, self.dirty = None, False

    def save(self):
        self.chart_path.parent.mkdir(parents=True, exist_ok=True)
        keys = ("time_ms", "lane", "duration_ms",
                "raw_time_ms", "raw_duration_ms")
        serial_notes = [
            {key: note[key] for key in keys}
            for note in sorted(self.notes,
                               key=lambda item: (item["time_ms"], item["lane"]))
        ]
        payload = {
            "format_version": self.FORMAT_VERSION,
            "title": self.title,
            "audio": self.audio_path.name,
            "bpm": round(self.bpm, 3),
            "beat_offset_ms": int(self.beat_offset_ms),
            "grid_subdivision": int(self.subdivision),
            "quantize_enabled": bool(self.quantize_enabled),
            "quantize_strength": round(self.quantize_strength, 3),
            "input_latency_ms": int(self.input_latency_ms),
            "notes": serial_notes,
        }
        temporary = self.chart_path.with_name(self.chart_path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, self.chart_path)
        self.dirty = False


class MusicTransport:
    """Absolute-time wrapper around pygame.mixer.music."""

    def __init__(self):
        self.audio_path = None
        self.cursor_ms = self.duration_ms = 0.0
        self.playing = False
        self._base_ms = self._started_at = 0.0

    def load(self, path):
        self.stop()
        self.audio_path = Path(path)
        pygame.mixer.music.load(str(path))
        self.cursor_ms = self.duration_ms = 0.0
        try:
            probe = pygame.mixer.Sound(str(path))
            self.duration_ms = probe.get_length() * 1000.0
            del probe
        except (pygame.error, OSError):
            pass

    def current_ms(self):
        if not self.playing:
            return self.cursor_ms
        value = self._base_ms + (time.monotonic() - self._started_at) * 1000.0
        if self.duration_ms and value >= self.duration_ms:
            self.stop(self.duration_ms)
            return self.cursor_ms
        return value

    def play(self):
        if self.audio_path is None:
            return False, "Nincs betoltott zene"
        start_ms = self.cursor_ms
        try:
            pygame.mixer.music.play(loops=0, start=max(0.0, start_ms / 1000.0))
        except (pygame.error, TypeError) as exc:
            if start_ms > 1:
                return False, f"Ez a formatum innen nem indithato: {exc}"
            pygame.mixer.music.play(loops=0)
        self._base_ms, self._started_at = start_ms, time.monotonic()
        self.playing = True
        return True, "Lejatszas"

    def pause(self):
        if self.playing:
            self.cursor_ms = self.current_ms()
            pygame.mixer.music.stop()
            self.playing = False

    def toggle(self):
        if self.playing:
            self.pause()
            return True, "Szunet"
        return self.play()

    def seek(self, milliseconds):
        was_playing = self.playing
        if was_playing:
            self.pause()
        upper = self.duration_ms if self.duration_ms > 0 else max(0, milliseconds)
        self.cursor_ms = clamp(float(milliseconds), 0.0, upper)
        return self.play() if was_playing else (True, "Pozicio beallitva")

    def stop(self, at_ms=0.0):
        try:
            pygame.mixer.music.stop()
        except pygame.error:
            pass
        self.playing = False
        self.cursor_ms = max(0.0, float(at_ms))


class GuitarChartEditor:
    TIMELINE = pygame.Rect(30, 126, 580, 192)
    RESIZE_HANDLE_WIDTH = 9

    def __init__(self, fullscreen=False, initial_audio=None):
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        if pygame.mixer.get_init() is None:
            pygame.mixer.init(44100, -16, 2, 512)
        flags = pygame.FULLSCREEN if fullscreen else 0
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT), flags)
        pygame.display.set_caption("CnC Guitar Chart Editor")
        pygame.mouse.set_visible(True)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 22)
        self.small = pygame.font.Font(None, 17)
        self.tiny = pygame.font.Font(None, 15)
        self.title_font = pygame.font.Font(None, 30)
        self.mode, self.files = "browser", []
        self.browser_cursor = self.browser_scroll = 0
        self.document = None
        self.transport = MusicTransport()
        self.running = True
        self.held_notes = {}
        self.dragging_id = None
        self.dragging_edge = None
        self.drag_offset_ms = 0.0
        self.guitar_heatmap = None
        self.guitar_heatmap_duration_ms = 0.0
        self.guitar_activity = None
        self.guitar_stem_path = None
        self.auditioning_guitar = False
        self.view_span_ms = 6000.0
        self.status, self.status_until = "", 0.0
        self.leave_confirm_until = 0.0
        self.tap_times = []
        self.rescan()
        if initial_audio is not None and Path(initial_audio).is_file():
            self.open_song(Path(initial_audio))

    def set_status(self, message, seconds=2.5):
        self.status = message
        self.status_until = time.monotonic() + seconds

    def rescan(self):
        SONGS_DIR.mkdir(parents=True, exist_ok=True)
        self.files = sorted(
            (path for path in SONGS_DIR.rglob("*")
             if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS),
            key=lambda path: str(path).casefold())
        self.browser_cursor = int(clamp(
            self.browser_cursor, 0, max(0, len(self.files) - 1)))

    def open_song(self, path):
        try:
            document = ChartDocument(path)
            self.transport.load(path)
        except (OSError, ValueError, json.JSONDecodeError, pygame.error) as exc:
            self.set_status(f"Nem toltheto be: {exc}", 5.0)
            return
        self.document, self.mode = document, "editor"
        self.guitar_heatmap = None
        self.guitar_heatmap_duration_ms = 0.0
        self.guitar_activity = None
        companion = document.audio_path.with_name(
            f"{document.audio_path.stem}.guitar.wav")
        cached = cached_guitar_stem_path(document.audio_path)
        self.guitar_stem_path = companion if companion.is_file() else (
            cached if cached.is_file() else None)
        self.auditioning_guitar = False
        self.tap_times.clear()
        self.set_status("Chart betoltve" if document.chart_path.is_file()
                        else "Uj chart")

    def close_song(self):
        self.transport.stop()
        self.document, self.mode = None, "browser"
        self.held_notes.clear()
        self.dragging_id = None
        self.dragging_edge = None
        self.guitar_heatmap = None
        self.guitar_heatmap_duration_ms = 0.0
        self.guitar_activity = None
        self.guitar_stem_path = None
        self.auditioning_guitar = False
        self.rescan()

    def request_close_song(self):
        if self.document is not None and self.document.dirty:
            now = time.monotonic()
            if now > self.leave_confirm_until:
                self.leave_confirm_until = now + 2.0
                self.set_status("NINCS MENTVE - Esc ujra: eldobas", 2.0)
                return
        self.close_song()

    def cursor_ms(self):
        return self.transport.current_ms()

    def time_to_x(self, milliseconds):
        half = self.view_span_ms / 2.0
        return round(self.TIMELINE.centerx +
                     (milliseconds - self.cursor_ms()) / half
                     * (self.TIMELINE.w / 2.0))

    def x_to_time(self, x):
        half = self.view_span_ms / 2.0
        return max(0.0, self.cursor_ms() +
                   (x - self.TIMELINE.centerx) / (self.TIMELINE.w / 2.0) * half)

    def y_to_lane(self, y):
        return int(clamp((y - self.TIMELINE.y) //
                         (self.TIMELINE.h // 3), 0, 2))

    def note_rect(self, note):
        lane_h = self.TIMELINE.h // 3
        x = self.time_to_x(note["time_ms"])
        width = 14
        if note.get("duration_ms", 0) > 0:
            width = max(14, self.time_to_x(
                note["time_ms"] + note["duration_ms"]) - x)
        return pygame.Rect(x,
                           self.TIMELINE.y + note["lane"] * lane_h + 17,
                           width, lane_h - 34)

    def note_resize_handles(self, note):
        rect = self.note_rect(note)
        half = self.RESIZE_HANDLE_WIDTH // 2
        left = pygame.Rect(rect.left - half, rect.top - 2,
                           self.RESIZE_HANDLE_WIDTH, rect.h + 4)
        right = pygame.Rect(rect.right - half, rect.top - 2,
                            self.RESIZE_HANDLE_WIDTH, rect.h + 4)
        return left, right

    def resize_edge_at(self, note, position):
        left, right = self.note_resize_handles(note)
        hits = []
        if left.collidepoint(position):
            hits.append((abs(position[0] - left.centerx), "start"))
        if right.collidepoint(position):
            hits.append((abs(position[0] - right.centerx), "end"))
        return min(hits)[1] if hits else None

    def note_at(self, position):
        if self.document is None:
            return None
        hits = [note for note in self.document.notes
                if self.note_rect(note).inflate(8, 8).collidepoint(position)]
        mouse_time = self.x_to_time(position[0])
        return min(hits, key=lambda n: abs(n["time_ms"] - mouse_time)) \
            if hits else None

    def cycle_grid(self, direction=1):
        values = [value for value, _label in GRID_OPTIONS]
        try:
            index = values.index(self.document.subdivision)
        except ValueError:
            index = 0
        self.document.subdivision = values[(index + direction) % len(values)]
        self.document.dirty = True
        self.set_status(f"Racs: {self.document.grid_label}; R = ujrakvantalas")

    def tap_tempo(self):
        song_time = self.cursor_ms()
        if self.tap_times and song_time - self.tap_times[-1] > 2500:
            self.tap_times.clear()
        self.tap_times.append(song_time)
        self.tap_times = self.tap_times[-9:]
        if len(self.tap_times) == 1:
            self.document.beat_offset_ms = int(round(song_time))
            self.document.dirty = True
            self.set_status("Elso utem rogzitve; T minden negyedre")
            return
        intervals = [b - a for a, b in zip(self.tap_times, self.tap_times[1:])
                     if b > a]
        if intervals:
            self.document.bpm = clamp(
                60_000.0 / statistics.median(intervals), 20.0, 400.0)
            self.document.dirty = True
            self.set_status(f"Tap tempo: {self.document.bpm:.1f} BPM")

    def auto_generate_chart(self, replace=False):
        """Create a guitar-focused first-pass chart from a spectral heatmap."""
        self.transport.pause()
        try:
            self.set_status(
                "AI guitar stem keszitese (elso alkalommal kb. 1 perc)...",
                3600.0,
            )
            self.draw()

            def show_stem_progress(elapsed):
                pygame.event.pump()
                self.set_status(
                    f"AI guitar stem: {elapsed:.0f} mp (kesobb cache-bol indul)",
                    2.0,
                )
                self.draw()
                self.clock.tick(15)

            guitar_stem = ensure_guitar_stem(
                self.document.audio_path, progress_hook=show_stem_progress)
            self.guitar_stem_path = guitar_stem
            samples = decode_audio_mono_pcm(guitar_stem)
            duration_ms = len(samples) * 1000.0 / AUTO_SAMPLE_RATE
            min_gap = int(clamp(self.document.grid_step_ms * 0.70, 80, 240))
            heatmap = build_guitar_heatmap(guitar_stem, duration_ms)
            activity = build_activity_envelope(samples, len(heatmap))
            candidates = candidates_from_guitar_heatmap(
                heatmap, duration_ms, min_gap_ms=min_gap, activity=activity)
            self.guitar_heatmap = heatmap
            self.guitar_heatmap_duration_ms = duration_ms
            self.guitar_activity = activity
            method = "AI guitar stem"
        except (OSError, RuntimeError) as exc:
            self.set_status(f"Auto chart hiba: {exc}", 6.0)
            return
        added = self.document.add_generated_notes(candidates, replace=replace)
        action = "ujrageneralva" if replace else "kiegeszitve"
        self.set_status(
            f"Auto chart {action}: {added} note, {method} (Ctrl+Z visszavonja)",
            5.0,
        )

    def toggle_guitar_audition(self):
        """Switch playback between the master and cached guitar stem."""
        if self.guitar_stem_path is None or not self.guitar_stem_path.is_file():
            self.set_status("Meg nincs guitar stem; elobb nyomj F-et", 4.0)
            return
        position = self.cursor_ms()
        was_playing = self.transport.playing
        target = (self.document.audio_path if self.auditioning_guitar
                  else self.guitar_stem_path)
        try:
            self.transport.load(target)
            self.transport.seek(position)
            if was_playing:
                self.transport.play()
        except pygame.error as exc:
            self.set_status(f"Stem lejatszasi hiba: {exc}", 5.0)
            return
        self.auditioning_guitar = not self.auditioning_guitar
        self.set_status(
            "Lejatszas: CSAK GUITAR STEM" if self.auditioning_guitar
            else "Lejatszas: EREDETI MASTER",
            4.0,
        )

    def handle_browser_key(self, event):
        if event.key == pygame.K_ESCAPE:
            self.running = False
        elif event.key == pygame.K_r:
            self.rescan()
            self.set_status("Zenelista frissitve")
        elif event.key == pygame.K_UP and self.files:
            self.browser_cursor = (self.browser_cursor - 1) % len(self.files)
        elif event.key == pygame.K_DOWN and self.files:
            self.browser_cursor = (self.browser_cursor + 1) % len(self.files)
        elif event.key == pygame.K_PAGEUP and self.files:
            self.browser_cursor = max(0, self.browser_cursor - 10)
        elif event.key == pygame.K_PAGEDOWN and self.files:
            self.browser_cursor = min(len(self.files) - 1,
                                      self.browser_cursor + 10)
        elif event.key == pygame.K_RETURN and self.files:
            self.open_song(self.files[self.browser_cursor])

    def handle_editor_keydown(self, event):
        document = self.document
        mods = event.mod if hasattr(event, "mod") else pygame.key.get_mods()
        ctrl, shift = bool(mods & pygame.KMOD_CTRL), bool(mods & pygame.KMOD_SHIFT)
        # Ctrl+S must be handled before bare S, which is the middle lane.
        if ctrl and event.key == pygame.K_s:
            try:
                document.save()
                self.set_status(f"Mentve: {document.chart_path.name}")
            except OSError as exc:
                self.set_status(f"Mentesi hiba: {exc}", 5.0)
            return
        if ctrl and event.key == pygame.K_z:
            self.set_status("Visszavonas" if document.undo()
                            else "Nincs mit visszavonni")
            return
        if ctrl and event.key == pygame.K_y:
            self.set_status("Ujra" if document.redo()
                            else "Nincs mit ujra vegrehajtani")
            return
        if event.key in LANE_KEYS:
            if event.key not in self.held_notes:
                note_id = document.add_note(LANE_KEYS[event.key], self.cursor_ms())
                self.held_notes[event.key] = note_id
                if note_id is not None:
                    note = document.find_note(note_id)
                    delta = note["time_ms"] - note["raw_time_ms"]
                    self.set_status(f"Note kvantalva: {delta:+d} ms", 1.0)
            return
        if event.key == pygame.K_SPACE:
            ok, message = self.transport.toggle()
            self.set_status(message if ok else f"Hiba: {message}")
        elif event.key == pygame.K_ESCAPE:
            self.request_close_song()
        elif event.key == pygame.K_LEFT:
            self.transport.seek(self.cursor_ms() - (1000 if shift else 100))
        elif event.key == pygame.K_RIGHT:
            self.transport.seek(self.cursor_ms() + (1000 if shift else 100))
        elif event.key == pygame.K_HOME:
            self.transport.seek(0)
        elif event.key == pygame.K_DELETE:
            if document.delete_selected():
                self.set_status("Note torolve")
        elif event.key == pygame.K_q:
            document.quantize_enabled = not document.quantize_enabled
            document.dirty = True
            self.set_status("Automatikus kvantalas BE" if document.quantize_enabled
                            else "Automatikus kvantalas KI")
        elif event.key == pygame.K_g:
            self.cycle_grid(-1 if shift else 1)
        elif event.key == pygame.K_b:
            document.beat_offset_ms = int(round(self.cursor_ms()))
            document.dirty = True
            self.set_status(f"Utemracs kezdete: {format_time(document.beat_offset_ms)}")
        elif event.key == pygame.K_t:
            self.tap_tempo()
        elif event.key == pygame.K_r:
            document.requantize_all()
            self.set_status(f"{len(document.notes)} note ujrakvantalva")
        elif event.key == pygame.K_f:
            self.auto_generate_chart(replace=shift)
        elif event.key == pygame.K_h:
            self.toggle_guitar_audition()
        elif event.key in (pygame.K_COMMA, pygame.K_LESS):
            document.bpm = clamp(document.bpm - (0.1 if shift else 0.5),
                                 20.0, 400.0)
            document.dirty = True
            self.set_status(f"BPM: {document.bpm:.1f}; R = ujrakvantalas")
        elif event.key in (pygame.K_PERIOD, pygame.K_GREATER):
            document.bpm = clamp(document.bpm + (0.1 if shift else 0.5),
                                 20.0, 400.0)
            document.dirty = True
            self.set_status(f"BPM: {document.bpm:.1f}; R = ujrakvantalas")
        elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            document.input_latency_ms -= 5
            document.dirty = True
            self.set_status(f"Input latency: {document.input_latency_ms:+d} ms")
        elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            document.input_latency_ms += 5
            document.dirty = True
            self.set_status(f"Input latency: {document.input_latency_ms:+d} ms")
        elif event.key == pygame.K_LEFTBRACKET:
            self.view_span_ms = clamp(self.view_span_ms / 1.5, 1000.0, 30_000.0)
        elif event.key == pygame.K_RIGHTBRACKET:
            self.view_span_ms = clamp(self.view_span_ms * 1.5, 1000.0, 30_000.0)

    def handle_editor_keyup(self, event):
        if event.key in self.held_notes:
            self.document.finish_note(self.held_notes.pop(event.key), self.cursor_ms())

    def handle_mouse_down(self, event):
        document = self.document
        if not self.TIMELINE.collidepoint(event.pos):
            return
        note = self.note_at(event.pos)
        if event.button == 3 and note is not None:
            document.selected_id = note["_id"]
            document.delete_selected()
            self.set_status("Note torolve")
        elif event.button == 1 and note is not None:
            document.selected_id = note["_id"]
            self.dragging_id = note["_id"]
            self.dragging_edge = self.resize_edge_at(note, event.pos)
            self.drag_offset_ms = self.x_to_time(event.pos[0]) - note["raw_time_ms"]
            document.checkpoint()
            self.transport.pause()
        elif event.button == 1:
            self.transport.seek(self.x_to_time(event.pos[0]))
            document.selected_id = None

    def handle_mouse_motion(self, event):
        if self.dragging_id is not None and event.buttons[0]:
            mouse_time = self.x_to_time(event.pos[0])
            if self.dragging_edge == "start":
                self.document.resize_note_start(self.dragging_id, mouse_time)
            elif self.dragging_edge == "end":
                self.document.resize_note_end(self.dragging_id, mouse_time)
            else:
                self.document.move_note(
                    self.dragging_id,
                    mouse_time - self.drag_offset_ms,
                    self.y_to_lane(event.pos[1]),
                )

    def process_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                if self.mode == "browser":
                    self.handle_browser_key(event)
                else:
                    self.handle_editor_keydown(event)
            elif event.type == pygame.KEYUP and self.mode == "editor":
                self.handle_editor_keyup(event)
            elif event.type == pygame.MOUSEBUTTONDOWN and self.mode == "editor":
                if event.button in (4, 5):
                    factor = 0.8 if event.button == 4 else 1.25
                    self.view_span_ms = clamp(
                        self.view_span_ms * factor, 1000.0, 30_000.0)
                else:
                    self.handle_mouse_down(event)
            elif event.type == pygame.MOUSEMOTION and self.mode == "editor":
                self.handle_mouse_motion(event)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.dragging_id = None
                self.dragging_edge = None
            elif event.type == pygame.DROPFILE:
                dropped = Path(event.file)
                if dropped.suffix.lower() in AUDIO_EXTENSIONS:
                    self.open_song(dropped)

    def draw_browser(self):
        self.screen.fill((12, 10, 22))
        self.screen.blit(self.title_font.render(
            "GUITAR CHART EDITOR", True, (255, 225, 80)), (24, 20))
        self.screen.blit(self.small.render(
            f"Songs: {SONGS_DIR}", True, (155, 155, 172)), (24, 53))
        if not self.files:
            lines = (
                "Nincs zene a Songs mappaban.",
                "Masolj ide .ogg, .mp3, .wav vagy .flac fajlt,",
                "majd nyomj R-t. PC-n ra is huzhatod az ablakra.",
            )
            for index, line in enumerate(lines):
                self.screen.blit(self.font.render(
                    line, True, (220, 220, 225)), (34, 112 + index * 30))
        else:
            visible = 12
            if self.browser_cursor < self.browser_scroll:
                self.browser_scroll = self.browser_cursor
            if self.browser_cursor >= self.browser_scroll + visible:
                self.browser_scroll = self.browser_cursor - visible + 1
            end = min(len(self.files), self.browser_scroll + visible)
            for row, index in enumerate(range(self.browser_scroll, end)):
                path = self.files[index]
                selected = index == self.browser_cursor
                color = (255, 232, 92) if selected else (218, 218, 225)
                chart = " [CHART]" if path.with_suffix(".chart.json").is_file() else ""
                label = f"{'> ' if selected else '  '}{path.relative_to(SONGS_DIR)}{chart}"
                self.screen.blit(self.small.render(
                    label[:72], True, color), (28, 90 + row * 27))
        hint = "Fel/Le: valaszt  Enter: megnyit  R: frissit  Esc: kilep"
        self.screen.blit(self.small.render(
            hint, True, (175, 205, 255)), (24, 445))

    def draw_editor(self):
        document = self.document
        self.screen.fill((9, 8, 18))
        title = document.title if len(document.title) <= 38 \
            else document.title[:35] + "..."
        self.screen.blit(self.title_font.render(
            title, True, (255, 230, 82)), (22, 12))
        dirty = " *" if document.dirty else ""
        info = (f"{format_time(self.cursor_ms())}   {document.bpm:.1f} BPM   "
                f"racs {document.grid_label}   "
                f"Q:{'BE' if document.quantize_enabled else 'KI'}{dirty}")
        self.screen.blit(self.small.render(
            info, True, (205, 205, 218)), (23, 46))
        second = (f"Beat start {format_time(document.beat_offset_ms)}   "
                  f"input {document.input_latency_ms:+d} ms   "
                  f"note-ok {len(document.notes)}")
        self.screen.blit(self.small.render(
            second, True, (145, 178, 205)), (23, 67))

        timeline = self.TIMELINE
        pygame.draw.rect(self.screen, (20, 19, 34), timeline)
        lane_h = timeline.h // 3
        for lane in range(3):
            rect = pygame.Rect(timeline.x, timeline.y + lane * lane_h,
                               timeline.w, lane_h)
            pygame.draw.rect(self.screen,
                             (26 + lane * 3, 25 + lane * 3, 43 + lane * 3),
                             rect)
            pygame.draw.line(self.screen, (67, 64, 85),
                             rect.bottomleft, rect.bottomright)
            self.screen.blit(self.tiny.render(
                LANE_NAMES[lane], True, LANE_COLORS[lane]),
                (timeline.x + 4, rect.y + 3))

        # Az automata alapjaul szolgalo gitar-spektrum halvanyan ott marad a
        # lane-ek mogott. Igy azonnal latszik, mely attackokra tett note-ot,
        # es hol erdemes kezzel potolni vagy torolni.
        if self.guitar_heatmap and self.guitar_heatmap_duration_ms > 0:
            heat_count = len(self.guitar_heatmap)
            for x in range(timeline.left, timeline.right + 1, 2):
                sample_time = self.x_to_time(x)
                if sample_time > self.guitar_heatmap_duration_ms:
                    continue
                heat_index = int(clamp(
                    round(sample_time / self.guitar_heatmap_duration_ms
                          * (heat_count - 1)),
                    0, heat_count - 1,
                ))
                for lane, strength in enumerate(self.guitar_heatmap[heat_index]):
                    if self.guitar_activity:
                        strength *= self.guitar_activity[heat_index]
                    if strength < 0.04:
                        continue
                    base = (26 + lane * 3, 25 + lane * 3, 43 + lane * 3)
                    color = tuple(round(base[channel]
                                        + (LANE_COLORS[lane][channel]
                                           - base[channel])
                                        * strength * 0.42)
                                  for channel in range(3))
                    pygame.draw.rect(
                        self.screen, color,
                        (x, timeline.y + lane * lane_h + 1, 2, lane_h - 2),
                    )

        step = document.grid_step_ms
        half_view = self.view_span_ms / 2.0
        view_start, view_end = self.cursor_ms() - half_view, self.cursor_ms() + half_view
        first = math.floor((view_start - document.beat_offset_ms) / step)
        last = math.ceil((view_end - document.beat_offset_ms) / step)
        for index in range(first, last + 1):
            grid_time = document.beat_offset_ms + index * step
            x = self.time_to_x(grid_time)
            if timeline.left <= x <= timeline.right:
                strong = index % document.subdivision == 0
                color = (82, 76, 105) if strong else (46, 44, 62)
                pygame.draw.line(self.screen, color,
                                 (x, timeline.y), (x, timeline.bottom),
                                 2 if strong else 1)

        for note in document.notes:
            rect = self.note_rect(note)
            if rect.right < timeline.left or rect.left > timeline.right:
                continue
            clipped = rect.clip(timeline)
            pygame.draw.rect(self.screen, LANE_COLORS[note["lane"]],
                             clipped, border_radius=5)
            if note["_id"] == document.selected_id:
                pygame.draw.rect(self.screen, (255, 255, 255),
                                 clipped.inflate(4, 4), 2, border_radius=6)
                left_handle, right_handle = self.note_resize_handles(note)
                for handle in (left_handle, right_handle):
                    visible_handle = handle.clip(timeline)
                    if visible_handle.w > 0:
                        pygame.draw.rect(
                            self.screen, (255, 255, 255), visible_handle,
                            border_radius=2)

        pygame.draw.line(self.screen, (255, 255, 255),
                         (timeline.centerx, timeline.y - 5),
                         (timeline.centerx, timeline.bottom + 5), 2)
        pygame.draw.polygon(self.screen, (255, 255, 255),
                            ((timeline.centerx - 6, timeline.y - 7),
                             (timeline.centerx + 6, timeline.y - 7),
                             (timeline.centerx, timeline.y)))
        pygame.draw.rect(self.screen, (100, 96, 120), timeline, 2)

        play_state = "PLAY" if self.transport.playing else "PAUSE"
        self.screen.blit(self.small.render(
            play_state, True, (118, 255, 140)), (30, 330))
        help_lines = (
            "A/S/D: note (tartva = sustain)   SPACE: play/pause",
            "Bal/Jobb: 100ms   eger: mozgat; ket feher szel: hossz",
            "T: tap tempo   B: beat start   G: racs   ,/.: BPM",
            "F: auto chart   Shift+F: csere   H: master/gitar   [/]: zoom",
            "Ctrl+S: ment  Ctrl+Z/Y: undo/redo  Del: torol  Esc: lista",
        )
        for index, line in enumerate(help_lines):
            self.screen.blit(self.tiny.render(
                line, True, (185, 185, 198)), (28, 354 + index * 20))

    def draw(self):
        self.draw_browser() if self.mode == "browser" else self.draw_editor()
        if self.status and time.monotonic() < self.status_until:
            surface = self.small.render(self.status, True, (255, 245, 160))
            background = surface.get_rect(
                midbottom=(WIDTH // 2, HEIGHT - 4)).inflate(14, 8)
            pygame.draw.rect(self.screen, (42, 34, 20),
                             background, border_radius=5)
            self.screen.blit(surface, surface.get_rect(center=background.center))
        pygame.display.flip()

    def run(self):
        while self.running:
            self.process_events()
            self.draw()
            self.clock.tick(FPS)
        self.transport.stop()
        pygame.quit()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="CnC three-lane guitar chart editor")
    parser.add_argument("audio", nargs="?", type=Path,
                        help="optional audio file to open")
    parser.add_argument("--fullscreen", action="store_true",
                        help="use the cabinet fullscreen display")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    GuitarChartEditor(args.fullscreen, args.audio).run()


if __name__ == "__main__":
    main()
