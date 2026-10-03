import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from game_modes import GAME_MUNCHIES, GAME_QUICK
from mock_mode_audio import (
    ARCADE_ENTER_LAYERS,
    MODE_VOICES,
    NAV_KEYS,
    NAV_WHOOSH,
    SELECTOR_GROOVE,
    SELECT_EFFECT,
    MockModeAudio,
)


class FakeSound:
    def __init__(self):
        self.play_count = 0

    def play(self):
        self.play_count += 1


class MockModeAudioTests(unittest.TestCase):
    def _loaded_player(self):
        player = MockModeAudio(enabled=True, asset_dir="unused")
        player._loaded = True
        filenames = {
            SELECT_EFFECT, NAV_WHOOSH, *NAV_KEYS.values(),
            *ARCADE_ENTER_LAYERS,
        }
        for voices in MODE_VOICES.values():
            filenames.update(voices)
        player._sounds = {filename: FakeSound() for filename in filenames}
        return player

    def test_selector_music_is_one_shot_and_navigation_layers_key_with_whoosh(self):
        player = self._loaded_player()
        with (
            patch("mock_mode_audio.Path.is_file", return_value=True),
            patch("mock_mode_audio.pygame.mixer.music.load") as load,
            patch("mock_mode_audio.pygame.mixer.music.play") as play,
        ):
            player.start_selector()
        load.assert_called_once_with(str(Path("unused") / SELECTOR_GROOVE))
        play.assert_called_once_with(loops=0)

        player.navigate(-1)
        self.assertEqual(player._sounds[NAV_KEYS[-1]].play_count, 1)
        self.assertEqual(player._sounds[NAV_WHOOSH].play_count, 1)

        player.navigate(1)
        self.assertEqual(player._sounds[NAV_KEYS[1]].play_count, 1)
        self.assertEqual(player._sounds[NAV_WHOOSH].play_count, 2)

    def test_quick_layers_select_fx_with_one_random_character_voice(self):
        player = self._loaded_player()
        chosen_voice = MODE_VOICES[GAME_QUICK][1]
        with patch("mock_mode_audio.random.choice", return_value=chosen_voice):
            player.play(GAME_QUICK)
        self.assertEqual(player._sounds[SELECT_EFFECT].play_count, 1)
        self.assertEqual(player._sounds[chosen_voice].play_count, 1)
        self.assertEqual(
            player._sounds[MODE_VOICES[GAME_QUICK][0]].play_count, 0
        )

    def test_munchies_plays_only_the_select_effect(self):
        player = self._loaded_player()
        player.play(GAME_MUNCHIES)
        self.assertEqual(player._sounds[SELECT_EFFECT].play_count, 1)
        self.assertTrue(
            all(
                player._sounds[voice].play_count == 0
                for voices in MODE_VOICES.values()
                for voice in voices
            )
        )

    def test_arcade_transition_layers_and_reverse_woosh(self):
        player = self._loaded_player()
        player.enter_arcade()
        for filename in ARCADE_ENTER_LAYERS:
            self.assertEqual(player._sounds[filename].play_count, 1)
        player.exit_arcade()
        self.assertEqual(player._sounds[NAV_WHOOSH].play_count, 2)


if __name__ == "__main__":
    unittest.main()
