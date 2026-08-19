"""Extract a saturated colored illustration from a neutral checkerboard background."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    with Image.open(args.source).convert("RGB") as image:
        rgb = np.asarray(image, dtype=np.float32)

    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    alpha = np.clip((chroma - 7.0) / 36.0, 0.0, 1.0)
    alpha_image = Image.fromarray(np.uint8(alpha * 255.0), mode="L").filter(
        ImageFilter.GaussianBlur(radius=0.45)
    )

    rgba = Image.fromarray(np.uint8(rgb), mode="RGB").convert("RGBA")
    rgba.putalpha(alpha_image)
    bbox = alpha_image.getbbox()
    if bbox is None:
        raise RuntimeError("No colored foreground detected")

    padding = 16
    left = max(0, bbox[0] - padding)
    top = max(0, bbox[1] - padding)
    right = min(rgba.width, bbox[2] + padding)
    bottom = min(rgba.height, bbox[3] + padding)
    cropped = rgba.crop((left, top, right, bottom))
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(args.destination, format="PNG")
    print(f"saved {args.destination} size={cropped.size} alpha={cropped.getchannel('A').getextrema()}")


if __name__ == "__main__":
    main()
