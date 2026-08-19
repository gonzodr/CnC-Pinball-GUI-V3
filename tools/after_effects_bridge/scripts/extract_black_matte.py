"""Convert artwork rendered over black into a straight-alpha RGBA PNG.

The source is treated as premultiplied RGB over black.  The strongest colour
channel becomes the matte; RGB is un-premultiplied so no black fringe remains
when After Effects composites the result with Normal blending.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def extract(source: Path, destination: Path, gamma: float, floor: float) -> None:
    image = Image.open(source).convert("RGB")
    rgb = np.asarray(image, dtype=np.float32) / 255.0

    energy = np.max(rgb, axis=2)
    base_alpha = np.clip((energy - floor) / max(1e-6, 1.0 - floor), 0.0, 1.0)
    alpha = np.power(base_alpha, gamma)

    straight = np.zeros_like(rgb)
    visible = alpha > (1.0 / 255.0)
    straight[visible] = np.clip(rgb[visible] / alpha[visible, None], 0.0, 1.0)

    rgba = np.dstack((straight, alpha))
    output = Image.fromarray(np.round(rgba * 255.0).astype(np.uint8), "RGBA")
    output = output.resize((640, 480), Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    output.save(destination, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--gamma", type=float, default=0.72)
    parser.add_argument("--floor", type=float, default=0.006)
    args = parser.parse_args()
    extract(args.source, args.destination, args.gamma, args.floor)


if __name__ == "__main__":
    main()
