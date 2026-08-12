"""Build sub-2-GiB GitHub Release ZIPs and their download manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


DEFAULT_PACKAGE_MIB = 1536


def directory_size(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def group_clips(clips: list[tuple[Path, int]], limit: int):
    groups: list[list[tuple[Path, int]]] = []
    current: list[tuple[Path, int]] = []
    current_size = 0
    for clip, size in clips:
        if size > limit:
            raise RuntimeError(
                f"Egyetlen klip nagyobb a csomaglimitnel: {clip.name} "
                f"({size / 1024**2:.1f} MiB)"
            )
        if current and current_size + size > limit:
            groups.append(current)
            current = []
            current_size = 0
        current.append((clip, size))
        current_size += size
    if current:
        groups.append(current)
    return groups


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--asset-version", required=True)
    parser.add_argument(
        "--release-base-url",
        required=True,
        help="Pl. https://github.com/USER/REPO/releases/download/video-assets-v1/",
    )
    parser.add_argument("--package-mib", type=int, default=DEFAULT_PACKAGE_MIB)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Manifest celja; alapbol az output/video_assets_manifest.json",
    )
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"Nincs sequence gyoker: {root}")
    output.mkdir(parents=True, exist_ok=True)
    clips = [
        (directory, directory_size(directory))
        for directory in sorted(root.iterdir(), key=lambda path: path.name.lower())
        if directory.is_dir() and any(directory.glob("*.png"))
    ]
    if not clips:
        raise SystemExit("Nincs csomagolhato PNG klip")

    limit = args.package_mib * 1024 * 1024
    groups = group_clips(clips, limit)
    packages = []
    prefix = f"cnc-video-assets-{args.asset_version}"
    for index, group in enumerate(groups, 1):
        archive = output / f"{prefix}-part{index:02d}.zip"
        print(
            f"[video-assets] {archive.name}: "
            + ", ".join(clip.name for clip, _size in group)
        )
        with zipfile.ZipFile(archive, "w", allowZip64=True) as package:
            for clip, _size in group:
                for frame in sorted(clip.glob("*.png")):
                    package.write(
                        frame,
                        arcname=f"{clip.name}/{frame.name}",
                        compress_type=zipfile.ZIP_STORED,
                    )
        packages.append(
            {
                "name": archive.name,
                "url": args.release_base_url.rstrip("/") + "/" + archive.name,
                "sha256": sha256_file(archive),
                "size_bytes": archive.stat().st_size,
                "clips": [clip.name for clip, _size in group],
            }
        )

    manifest = {
        "schema_version": 1,
        "asset_version": args.asset_version,
        "description": "CnC Pinball 640x480 RGB PNG sequence video assets",
        "packages": packages,
    }
    manifest_path = (
        args.manifest.expanduser().resolve()
        if args.manifest is not None
        else output / "video_assets_manifest.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[video-assets] manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
