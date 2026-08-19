"""Shared location rules for externally distributed PNG video assets."""

from __future__ import annotations

import os
from pathlib import Path


ENVIRONMENT_VARIABLE = "CNC_PNG_VIDEO_ROOT"
EXTERNAL_DIRECTORY_NAME = "CnC-Pinball-Video-Assets"


def default_external_root() -> Path:
    """Return a writable, repository-independent default directory."""
    configured = os.environ.get(ENVIRONMENT_VARIABLE)
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        return base / EXTERNAL_DIRECTORY_NAME
    return Path.home() / EXTERNAL_DIRECTORY_NAME


def legacy_sequence_root() -> Path:
    # A klipek kozvetlenul az assets/Videos alatt vannak (a korabbi
    # Videos/Test almappa megszunt, es az mp4-ek is kikerultek innen).
    return Path(__file__).resolve().parent / "assets" / "Videos"


def resolve_sequence_root(explicit_root: Path | str | None = None) -> Path:
    """Prefer explicit/external assets, retaining a temporary local fallback."""
    if explicit_root is not None:
        return Path(explicit_root).expanduser()
    external = default_external_root()
    if external.is_dir():
        return external
    legacy = legacy_sequence_root()
    return legacy if legacy.is_dir() else external
