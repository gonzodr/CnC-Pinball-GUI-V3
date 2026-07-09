import json
import os

class ScoreManager:
    # Abszolut ut a sajat fajl mellett - igy a hiscores.json mindig a
    # src mappaba kerul, fuggetlenul attol, honnan (melyik munkakonyvtarbol)
    # inditjak a main.py-t.
    FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hiscores.json")

    def __init__(self):
        self.scores = self.load()

    def load(self):
        default = [{"name": "---", "score": 0} for _ in range(10)]
        if not os.path.exists(self.FILE_PATH):
            return default
        try:
            with open(self.FILE_PATH, "r") as f:
                data = json.load(f)
                # Ha üres a fájl, vagy rövidebb mint 10, kiegészítjük
                if not data:
                    return default
                while len(data) < 10:
                    data.append({"name": "---", "score": 0})
                return data
        except:
            return default

    def add_score(self, name, score):
        self.scores.append({"name": name.upper(), "score": score})
        self.scores = sorted(self.scores, key=lambda x: x["score"], reverse=True)[:10]
        self.save()

    def save(self):
        with open(self.FILE_PATH, "w") as f:
            json.dump(self.scores, f)

    def is_highscore(self, score):
        return score > self.scores[-1]["score"]

    def remove_at(self, index):
        """Egy bejegyzes torlese (szerviz menu), a lista utana ujra 10-re
        van toltve '---'/0 helykitoltovel."""
        if 0 <= index < len(self.scores):
            del self.scores[index]
            while len(self.scores) < 10:
                self.scores.append({"name": "---", "score": 0})
            self.save()

    def reset(self):
        """Az egesz tabla nullazasa (szerviz menu)."""
        self.scores = [{"name": "---", "score": 0} for _ in range(10)]
        self.save()