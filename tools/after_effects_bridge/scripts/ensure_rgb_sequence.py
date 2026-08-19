"""Convert PNG sequences to opaque RGB and verify their frame numbering."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from PIL import Image


FRAME_RE = re.compile(r"^(?P<prefix>.+)_(?P<number>\d{5})\.png$", re.IGNORECASE)


def convert_and_verify(folder: Path, prefix: str, expected_frames: int) -> None:
    files = sorted(folder.glob("*.png"))
    expected_names = [f"{prefix}_{index:05d}.png" for index in range(1, expected_frames + 1)]
    actual_names = [file.name for file in files]
    if actual_names != expected_names:
        missing = sorted(set(expected_names) - set(actual_names))
        extra = sorted(set(actual_names) - set(expected_names))
        raise RuntimeError(f"{folder}: sequence mismatch; missing={missing}, extra={extra}")

    for file in files:
        if not FRAME_RE.match(file.name):
            raise RuntimeError(f"Unexpected PNG name: {file}")
        with Image.open(file) as source:
            if source.size != (640, 480):
                raise RuntimeError(f"Unexpected size for {file}: {source.size}")
            rgb = source.convert("RGB")
            temporary = file.with_name(file.stem + ".__rgb_tmp.png")
            rgb.save(temporary, format="PNG", optimize=False)
        temporary.replace(file)

    for file in files:
        with Image.open(file) as image:
            if image.mode != "RGB" or image.size != (640, 480):
                raise RuntimeError(f"RGB verification failed for {file}: {image.mode} {image.size}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("scores", nargs="+")
    parser.add_argument("--frames", type=int, default=150)
    args = parser.parse_args()

    for score in args.scores:
        folder = args.root / score
        convert_and_verify(folder, score, args.frames)
        print(f"{score}: {args.frames} RGB frames verified")


if __name__ == "__main__":
    main()
