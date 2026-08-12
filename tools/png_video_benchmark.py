"""Real-time benchmark for the in-process PNG sequence video player."""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import pygame


FRAME_PERIOD = 1.0 / 30.0


def _rss_mib() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def run_clip(player, screen, name: str) -> dict[str, float]:
    player.start(name)
    work_times: list[float] = []
    frame_intervals: list[float] = []
    peak_rss = _rss_mib()
    late_frames = 0
    started = previous_frame = time.monotonic()

    while player.active:
        frame_started = time.monotonic()
        frame_intervals.append(frame_started - previous_frame)
        previous_frame = frame_started
        player.update()
        if player.active:
            player.draw(screen)
            pygame.display.flip()
        work_time = time.monotonic() - frame_started
        work_times.append(work_time)
        peak_rss = max(peak_rss, _rss_mib())
        if work_time > FRAME_PERIOD:
            late_frames += 1
        time.sleep(max(0.0, FRAME_PERIOD - work_time))

    elapsed = time.monotonic() - started
    misses = player._cache_misses
    draws = player._draw_requests
    return {
        "seconds": elapsed,
        "fps": len(work_times) / elapsed if elapsed else 0.0,
        "avg_work_ms": statistics.mean(work_times) * 1000.0,
        "p95_work_ms": _percentile(work_times, .95) * 1000.0,
        "max_work_ms": max(work_times, default=0.0) * 1000.0,
        "p95_interval_ms": _percentile(frame_intervals[1:], .95) * 1000.0,
        "max_interval_ms": max(frame_intervals[1:], default=0.0) * 1000.0,
        "late_frames": late_frames,
        "misses": misses,
        "draws": draws,
        "peak_rss_mib": peak_rss,
    }


def run_switch_stress(player, screen, switches: int, interval: float):
    selected: list[str] = []
    work_times: list[float] = []
    peak_rss = _rss_mib()
    next_switch = time.monotonic()
    switch_count = 0
    end_time = next_switch + switches * interval + 1.0

    while time.monotonic() < end_time:
        frame_started = time.monotonic()
        if switch_count < switches and frame_started >= next_switch:
            selected.append(player.start_random() or "-")
            switch_count += 1
            next_switch += interval
        player.update()
        if player.active:
            player.draw(screen)
            pygame.display.flip()
        work_time = time.monotonic() - frame_started
        work_times.append(work_time)
        peak_rss = max(peak_rss, _rss_mib())
        time.sleep(max(0.0, FRAME_PERIOD - work_time))

    player.stop()
    print(
        "SWITCH_STRESS "
        f"switches={switch_count} unique={len(set(selected))} "
        f"avg_ms={statistics.mean(work_times) * 1000.0:.2f} "
        f"p95_ms={_percentile(work_times, .95) * 1000.0:.2f} "
        f"max_ms={max(work_times, default=0.0) * 1000.0:.2f} "
        f"peak_rss={peak_rss:.1f}MiB"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--src", required=True, type=Path)
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--switches", type=int, default=12)
    parser.add_argument("--switch-interval", type=float, default=.4)
    parser.add_argument("clips", nargs="*")
    args = parser.parse_args()

    sys.path.insert(0, str(args.src))
    from png_video_player import PngSequencePlayer

    pygame.init()
    flags = pygame.FULLSCREEN if args.fullscreen else 0
    screen = pygame.display.set_mode((640, 480), flags)
    pygame.mouse.set_visible(False)
    player = PngSequencePlayer(args.root)
    clips = args.clips or list(player.clip_names)
    if not clips:
        print("Nincs benchmarkolhato clip.")
        return 2

    try:
        print(
            f"BENCHMARK driver={pygame.display.get_driver()} "
            f"clips={','.join(clips)} baseline_rss={_rss_mib():.1f}MiB"
        )
        for name in clips:
            result = run_clip(player, screen, name)
            print(
                f"RESULT {name} "
                + " ".join(f"{key}={value:.2f}" for key, value in result.items())
            )
        run_switch_stress(
            player, screen, args.switches, max(.1, args.switch_interval)
        )
    finally:
        player.close()
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
