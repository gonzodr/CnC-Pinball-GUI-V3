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


SELECTOR_GROOVE = "0066_mus_Mode_select_groove.wav"
NAV_WHOOSH = "0067_fx_woosh.wav"
ARCADE_ENTER_LAYERS = (
    NAV_WHOOSH,
    "0020_fx_punch.wav",
    "0028_fx_kvakk.wav",
)
SELECT_EFFECT = "0068_fx_select_mode.wav"
NAV_KEYS = {
    -1: "0105_fx_keyleft.wav",
    1: "0106_fx_keyright.wav",
}
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
        self._groove_active = False

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
            filenames = {
                SELECT_EFFECT, NAV_WHOOSH, *NAV_KEYS.values(),
                *ARCADE_ENTER_LAYERS,
            }
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

    def start_selector(self):
        if not self._ensure_loaded():
            return
        groove_path = self.asset_dir / SELECTOR_GROOVE
        if not groove_path.is_file():
            print(f"[mock-audio] mode select zene nem talalhato: {groove_path}")
            return
        try:
            pygame.mixer.music.load(str(groove_path))
            pygame.mixer.music.play(loops=0)
            self._groove_active = True
        except (pygame.error, OSError) as exc:
            print(f"[mock-audio] mode select zene nem indithato: {exc}")

    def stop_selector(self):
        if self._groove_active and pygame.mixer.get_init() is not None:
            pygame.mixer.music.stop()
        self._groove_active = False

    def navigate(self, direction):
        if not self._ensure_loaded():
            return
        key_sound = NAV_KEYS.get(-1 if direction < 0 else 1)
        if key_sound is None:
            return
        self._sounds[key_sound].play()
        self._sounds[NAV_WHOOSH].play()

    def enter_arcade(self):
        if not self._ensure_loaded():
            return
        for filename in ARCADE_ENTER_LAYERS:
            self._sounds[filename].play()

    def exit_arcade(self):
        if not self._ensure_loaded():
            return
        self._sounds[NAV_WHOOSH].play()

    def play(self, mode_id):
        if not self._ensure_loaded():
            return
        self.stop_selector()
        self._sounds[SELECT_EFFECT].play()
        voices = MODE_VOICES.get(mode_id, ())
        if voices:
            self._sounds[random.choice(voices)].play()
