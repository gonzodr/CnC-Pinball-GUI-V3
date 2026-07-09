"""A Special Thanks kepernyo nevlistajanak perzisztens tarolasa - a
szerviz menubol szerkesztheto (hozzaadas/torles), ugyanaz a mintat
koveti, mint a ScoreManager."""

import json
import os


class ThanksNamesManager:
    # Abszolut ut a sajat fajl mellett, mint a ScoreManager-nel.
    FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thanks_names.json")

    DEFAULT_NAMES = ["Balint Hajnal", "Daniel Szilvasi", "Elod Toth", "Timea Varga"]

    def __init__(self):
        self.names = self.load()

    def load(self):
        if not os.path.exists(self.FILE_PATH):
            return list(self.DEFAULT_NAMES)
        try:
            with open(self.FILE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return [str(n) for n in data]
        except Exception:
            pass
        return list(self.DEFAULT_NAMES)

    def save(self):
        with open(self.FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(self.names, f, ensure_ascii=False)

    def add(self, name):
        name = name.strip()
        if name:
            self.names.append(name)
            self.names.sort(key=str.lower)
            self.save()

    def remove_at(self, index):
        if 0 <= index < len(self.names):
            del self.names[index]
            self.save()
