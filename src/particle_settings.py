"""Reszecske-effekt (spark/firework) globalis intenzitas-szorzoi - a
szerviz menu Particle szerkesztojebol allithato (Fel/Le: parameter,
Bal/Jobb: ertek), szorzokent hat MINDHAROM kepernyo (SUMMARY/FINAL_SCORES/
BEAT_SCORE) reszecske-effektjere, ugyanaz a mentesi minta, mint a
ScoreManager/ThanksNamesManager-nel."""

import json
import os


class ParticleSettingsManager:
    FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "particle_settings.json")

    DEFAULTS = {
        "count_mult": 1.0,
        "size_mult": 1.0,
        "speed_mult": 1.0,
        "lifetime_mult": 1.0,
    }

    # (min, max, step) - a szerkeszto csuszkaihoz
    RANGES = {
        "count_mult": (0.2, 4.0, 0.1),
        "size_mult": (0.5, 4.0, 0.1),
        "speed_mult": (0.3, 3.0, 0.1),
        "lifetime_mult": (0.3, 3.0, 0.1),
    }

    LABELS = {
        "count_mult": "Darabszam",
        "size_mult": "Meret",
        "speed_mult": "Sebesseg",
        "lifetime_mult": "Elettartam",
    }

    def __init__(self):
        self.values = self.load()

    def load(self):
        if not os.path.exists(self.FILE_PATH):
            return dict(self.DEFAULTS)
        try:
            with open(self.FILE_PATH, "r") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    merged = dict(self.DEFAULTS)
                    merged.update({k: v for k, v in data.items() if k in self.DEFAULTS})
                    return merged
        except Exception:
            pass
        return dict(self.DEFAULTS)

    def save(self):
        with open(self.FILE_PATH, "w") as f:
            json.dump(self.values, f)

    def keys_in_order(self):
        return ["count_mult", "size_mult", "speed_mult", "lifetime_mult"]

    def adjust(self, key, direction):
        """direction: +1 vagy -1 - egy 'step'-nyivel modositja az erteket,
        a RANGES-hez clamp-elve, majd rogton menti is."""
        lo, hi, step = self.RANGES[key]
        new_val = round(self.values[key] + direction * step, 2)
        self.values[key] = max(lo, min(hi, new_val))
        self.save()

    def reset_defaults(self):
        self.values = dict(self.DEFAULTS)
        self.save()
