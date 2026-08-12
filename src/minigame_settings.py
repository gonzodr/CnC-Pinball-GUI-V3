"""Persistent per-minigame service settings.

The service menu exposes every registered minigame as a row.  Difficulty is
stored as a seven-position integer (-3..+3), keeping zero exactly compatible
with the gameplay tuning that predates this menu.
"""

import json
import os


class MinigameSettingsManager:
    FILE_PATH = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "minigame_settings.json"
    )

    GAMES = (
        ("munchies_abduction", "MUNCHIES ABDUCTION"),
    )

    MIN_DIFFICULTY = -3
    MAX_DIFFICULTY = 3
    DEFAULTS = {game_id: 0 for game_id, _label in GAMES}
    LEVEL_LABELS = {
        -3: "ALMOST ENDLESS",
        -2: "VERY EASY",
        -1: "EASY",
        0: "NORMAL",
        1: "HARD",
        2: "HARDER",
        3: "VERY HARD",
    }

    def __init__(self):
        self.values = self.load()

    @classmethod
    def clamp(cls, value):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        return max(cls.MIN_DIFFICULTY, min(cls.MAX_DIFFICULTY, value))

    def load(self):
        values = dict(self.DEFAULTS)
        if not os.path.exists(self.FILE_PATH):
            return values
        try:
            with open(self.FILE_PATH, "r", encoding="utf-8") as settings_file:
                data = json.load(settings_file)
            if isinstance(data, dict):
                for game_id in values:
                    if game_id in data:
                        values[game_id] = self.clamp(data[game_id])
        except (OSError, ValueError, TypeError):
            pass
        return values

    def save(self):
        with open(self.FILE_PATH, "w", encoding="utf-8") as settings_file:
            json.dump(self.values, settings_file, indent=2)

    def get_difficulty(self, game_id):
        return self.clamp(self.values.get(game_id, 0))

    def difficulty_label(self, game_id):
        return self.LEVEL_LABELS[self.get_difficulty(game_id)]

    def adjust_difficulty(self, game_id, direction):
        if game_id not in self.DEFAULTS:
            return 0
        current = self.get_difficulty(game_id)
        self.values[game_id] = self.clamp(current + (1 if direction > 0 else -1))
        self.save()
        return self.values[game_id]

    def reset_normal(self, game_id):
        if game_id in self.DEFAULTS:
            self.values[game_id] = 0
            self.save()
