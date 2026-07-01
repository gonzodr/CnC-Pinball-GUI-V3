import json
import os

class ScoreManager:
    FILE_PATH = "hiscores.json"

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