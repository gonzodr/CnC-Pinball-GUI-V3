"""Extract Munchies item sprites from the two supplied concept sheets.

The sheets have opaque, gently varying backgrounds.  For each known cell we
fit a bilinear background from its corner colours, threshold the colour
difference, retain the largest connected foreground component and soften its
edge.  This is deterministic and keeps the original painted pixels intact.
"""

from pathlib import Path
from collections import deque

import numpy as np
from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1] / "assets" / "Minigame" / "Sprites"
OUT = ROOT / "extracted"

CROPS = {
    "Eatables.png": {
        "pizza": (160, 145, 515, 450),
        "burrito": (505, 145, 785, 455),
        "chips": (815, 145, 1110, 455),
        "burger": (1135, 155, 1450, 440),
        "taco": (135, 585, 515, 865),
        "donut": (480, 585, 800, 860),
        "soda": (840, 505, 1110, 860),
    },
    "Junk.png": {
        "trash_lid": (135, 120, 545, 410),
        "police": (580, 120, 985, 390),
        "junk": (1030, 120, 1455, 430),
        "trash": (130, 510, 545, 870),
        "can": (605, 510, 895, 870),
        "boot": (1045, 535, 1450, 870),
    },
}


def _corner_colour(rgb, x, y, size=12):
    h, w = rgb.shape[:2]
    return np.median(
        rgb[max(0, y-size):min(h, y+size), max(0, x-size):min(w, x+size)],
        axis=(0, 1),
    )


def _background_model(rgb):
    h, w = rgb.shape[:2]
    tl, tr = _corner_colour(rgb, 5, 5), _corner_colour(rgb, w-5, 5)
    bl, br = _corner_colour(rgb, 5, h-5), _corner_colour(rgb, w-5, h-5)
    xx = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    return (tl*(1-xx)+tr*xx)*(1-yy) + (bl*(1-xx)+br*xx)*yy


def _flood_foreground(rgb, allowed, tolerance=48.0):
    """Return everything enclosed by a sufficiently sharp painted outline."""
    h, w = rgb.shape[:2]
    background = np.zeros((h, w), dtype=bool)
    todo = deque()
    for x in range(w):
        todo.append((0, x)); todo.append((h - 1, x))
    for y in range(1, h - 1):
        todo.append((y, 0)); todo.append((y, w - 1))
    limit = tolerance * tolerance
    while todo:
        y, x = todo.popleft()
        if background[y, x] or not allowed[y, x]:
            continue
        background[y, x] = True
        here = rgb[y, x]
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < h and 0 <= nx < w and not background[ny, nx] and allowed[ny, nx]:
                delta = rgb[ny, nx] - here
                if float(np.dot(delta, delta)) <= limit:
                    todo.append((ny, nx))
    return ~background


def _largest_component(mask):
    h, w = mask.shape
    remaining = set(map(tuple, np.argwhere(mask)))
    largest = []
    while remaining:
        seed = remaining.pop()
        component = [seed]
        todo = [seed]
        while todo:
            y, x = todo.pop()
            for point in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if point in remaining:
                    remaining.remove(point)
                    todo.append(point)
                    component.append(point)
        if len(component) > len(largest):
            largest = component
    result = np.zeros_like(mask)
    if largest:
        yy, xx = zip(*largest)
        result[np.asarray(yy), np.asarray(xx)] = True
    return result


def extract(sheet, box, destination, allow_green_glow=False):
    crop = sheet.crop(box).convert("RGB")
    rgb = np.asarray(crop, dtype=np.float32)
    model = _background_model(rgb)
    model_difference = np.sqrt(np.sum((rgb - model) ** 2, axis=2))
    allowed = model_difference < (180.0 if allow_green_glow else 68.0)
    if allow_green_glow:
        red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
        allowed |= (green > red * 1.18) & (green > blue * 1.22)
    component = _largest_component(_flood_foreground(rgb, allowed))

    # Preserve antialiasing and a small amount of the painted halo without
    # retaining the concept-sheet background texture.
    hard_mask = Image.fromarray((component * 255).astype(np.uint8), "L")
    hard_mask = hard_mask.filter(ImageFilter.MaxFilter(7))
    soft_mask = hard_mask.filter(ImageFilter.GaussianBlur(1.3))
    rgba = crop.convert("RGBA")
    rgba.putalpha(soft_mask)
    bbox = soft_mask.getbbox()
    if bbox is None:
        raise RuntimeError(f"No foreground found for {destination.name}")
    pad = 6
    bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
            min(rgba.width, bbox[2] + pad), min(rgba.height, bbox[3] + pad))
    rgba.crop(bbox).save(destination, optimize=True)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for sheet_name, sprites in CROPS.items():
        sheet = Image.open(ROOT / sheet_name)
        for name, box in sprites.items():
            extract(sheet, box, OUT / f"{name}.png", allow_green_glow=sheet_name == "Eatables.png")
            print(f"extracted {name}.png")


if __name__ == "__main__":
    main()
