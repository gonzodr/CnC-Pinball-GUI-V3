"""Download and atomically install PNG-sequence video Release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from video_asset_paths import default_external_root  # noqa: E402


CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(source: str) -> tuple[dict, str]:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in ("http", "https", "file"):
        with urllib.request.urlopen(source) as response:
            manifest = json.load(response)
        return manifest, source
    path = Path(source).expanduser().resolve()
    with path.open("r", encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    return manifest, path.as_uri()


def resolve_package_url(manifest_url: str, package_url: str) -> str:
    parsed = urllib.parse.urlparse(package_url)
    if parsed.scheme:
        return package_url
    return urllib.parse.urljoin(manifest_url, package_url)


def download(url: str, destination: Path, expected_size: int | None = None):
    partial = destination.with_suffix(destination.suffix + ".part")
    partial.unlink(missing_ok=True)
    print(f"[video-assets] letoltes: {destination.name}")
    downloaded = 0
    with urllib.request.urlopen(url) as response, partial.open("wb") as output:
        total = expected_size or int(response.headers.get("Content-Length", 0))
        while chunk := response.read(CHUNK_SIZE):
            output.write(chunk)
            downloaded += len(chunk)
            if total:
                print(
                    f"\r  {downloaded / 1024**2:.1f}/{total / 1024**2:.1f} MiB "
                    f"({downloaded * 100 / total:.0f}%)",
                    end="",
                    flush=True,
                )
    if downloaded:
        print()
    if expected_size is not None and downloaded != expected_size:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"Hibas meret: {destination.name}: {downloaded} != {expected_size}"
        )
    os.replace(partial, destination)


def safe_extract(archive: Path, destination: Path):
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            member_path = (destination / member.filename).resolve()
            try:
                member_path.relative_to(destination_resolved)
            except ValueError as exc:
                raise RuntimeError(
                    f"Tiltott utvonal a ZIP-ben: {member.filename}"
                ) from exc
            # Unix symlink entries are unnecessary for video assets and can
            # escape the staging tree after extraction.
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError(f"Symlink nem engedelyezett: {member.filename}")
        package.extractall(destination)


def validate_staging(staging: Path) -> tuple[int, int]:
    clip_count = 0
    frame_count = 0
    for directory in staging.iterdir():
        if directory.is_dir() and not directory.name.startswith("."):
            frames = list(directory.glob("*.png"))
            if frames:
                clip_count += 1
                frame_count += len(frames)
    if clip_count == 0 or frame_count == 0:
        raise RuntimeError("A csomagokban nem talalhato egyetlen PNG klip sem")
    return clip_count, frame_count


def install(manifest_source: str, target: Path, cache: Path, keep_downloads=False):
    manifest, manifest_url = load_manifest(manifest_source)
    if manifest.get("schema_version") != 1:
        raise RuntimeError("Nem tamogatott video asset manifest")
    packages = manifest.get("packages")
    if not isinstance(packages, list) or not packages:
        raise RuntimeError("A manifest nem tartalmaz letoltheto csomagot")

    target = target.expanduser().resolve()
    home = Path.home().resolve()
    if target in (Path(target.anchor), home) or target.parent == target:
        raise RuntimeError(f"Nem biztonsagos celkonyvtar: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    archives: list[Path] = []
    for package in packages:
        name = Path(package["name"]).name
        if not name.lower().endswith(".zip"):
            raise RuntimeError(f"Csak ZIP csomag tamogatott: {name}")
        destination = cache / name
        expected_hash = str(package["sha256"]).lower()
        expected_size = package.get("size_bytes")
        if not destination.is_file() or sha256_file(destination) != expected_hash:
            download(
                resolve_package_url(manifest_url, package["url"]),
                destination,
                int(expected_size) if expected_size is not None else None,
            )
        print(f"[video-assets] SHA-256 ellenorzes: {name}")
        actual_hash = sha256_file(destination)
        if actual_hash != expected_hash:
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"SHA-256 elteres: {name}")
        archives.append(destination)

    staging = Path(tempfile.mkdtemp(prefix=".video-assets-staging-", dir=target.parent))
    backup = target.with_name(target.name + ".previous")
    activated = False
    try:
        for archive in archives:
            print(f"[video-assets] kibontas: {archive.name}")
            safe_extract(archive, staging)
        clip_count, frame_count = validate_staging(staging)
        (staging / ".asset-version.json").write_text(
            json.dumps(
                {
                    "asset_version": manifest.get("asset_version", "unknown"),
                    "clips": clip_count,
                    "frames": frame_count,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        if backup.exists():
            shutil.rmtree(backup)
        if target.exists():
            os.replace(target, backup)
        try:
            os.replace(staging, target)
            activated = True
        except Exception:
            if backup.exists() and not target.exists():
                os.replace(backup, target)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        print(
            f"[video-assets] kesz: {clip_count} klip, {frame_count} frame -> {target}"
        )
    finally:
        if not activated and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if not keep_downloads:
            for archive in archives:
                archive.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        default=str(REPO_ROOT / "video_assets_manifest.json"),
        help="Helyi manifest vagy HTTPS URL",
    )
    parser.add_argument("--target", type=Path, default=default_external_root())
    parser.add_argument(
        "--cache",
        type=Path,
        default=default_external_root().parent / "video_assets_downloads",
    )
    parser.add_argument("--keep-downloads", action="store_true")
    args = parser.parse_args()
    try:
        install(args.manifest, args.target, args.cache, args.keep_downloads)
    except Exception as exc:
        print(f"[video-assets] HIBA: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
