"""Optional PC-only preview of the cabinet's game-mode confirmation audio."""

import random
from pathlib import Path

import pygame

from game_modes import (
    GAME_COOP,
    GAME_MUNCHIES,
    GAME_MULTIBALL_MAYHEM,
    GAME_QUICK,
    GAME_STANDARD,
)


SELECT_EFFECT = "0068_fx_select_mode.wav"
MODE_VOICES = {
    GAME_STANDARD: (
        "0329_CHEECH_MODE_STANDARD.wav",
        "0333_CHONG_MODE_STANDARD.wav",
    ),
    GAME_COOP: (
        "0330_CHEECH_MODE_COOP.wav",
        "0334_CHONG_MODE_COOP.wav",
    ),
    GAME_QUICK: (
        "0331_CHEECH_MODE_QUICK.wav",
        "0335_CHONG_MODE_QUICK.wav",
    ),
    GAME_MULTIBALL_MAYHEM: (
        "0332_CHEECH_MODE_MULTIB.wav",
        "0336_CHONG_MODE_MULTIB.wav",
    ),
    GAME_MUNCHIES: (),
}


class MockModeAudio:
    """Lazily loads and plays the same layered sounds as the Arduino."""

    def __init__(self, enabled=False, asset_dir=None):
        self.enabled = bool(enabled)
        self.asset_dir = Path(asset_dir) if asset_dir else None
        self._loaded = False
        self._sounds = {}

    def _ensure_loaded(self):
        if not self.enabled or self.asset_dir is None:
            return False
        if self._loaded:
            return True
        if not self.asset_dir.is_dir():
            print(f"[mock-audio] hangmappa nem talalhato: {self.asset_dir}")
            self.enabled = False
            return False

        try:
            if pygame.mixer.get_init() is None:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            pygame.mixer.set_num_channels(max(8, pygame.mixer.get_num_channels()))
            filenames = {SELECT_EFFECT}
            for voices in MODE_VOICES.values():
                filenames.update(voices)
            for filename in filenames:
                path = self.asset_dir / filename
                if not path.is_file():
                    raise FileNotFoundError(path)
                self._sounds[filename] = pygame.mixer.Sound(str(path))
        except (FileNotFoundError, pygame.error, OSError) as exc:
            print(f"[mock-audio] mode select hangok nem tolthetok be: {exc}")
            self._sounds.clear()
            self.enabled = False
            return False

        self._loaded = True
        print(f"[mock-audio] mode select hangok aktivak: {self.asset_dir}")
        return True

    def play(self, mode_id):
        if not self._ensure_loaded():
            return
        self._sounds[SELECT_EFFECT].play()
        voices = MODE_VOICES.get(mode_id, ())
        if voices:
            self._sounds[random.choice(voices)].play()
