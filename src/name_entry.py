"""Hiscore név-beíró logika: állítható hosszúságú név kiválasztása.

Vezérlés (a state_machine köti be a tényleges eseményekhez):
- bal/jobb flipper: az aktuális pozíción lévő karakter előre/hátra léptetése
- Player (P) gomb: az aktuális karakter megerősítése, ugrás a következőre
  (a 3. karakter után a név kész - `done` True lesz)
- Start gomb: azonnali skip, a jelenlegi állással lezárja a nevet
"""

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
TEAM_ALPHABET = ALPHABET + " "
NAME_LENGTH = 3


class NameEntryController:
    def __init__(self):
        self.reset()

    def reset(self, length=NAME_LENGTH, allow_space=False):
        """Új név-beírás indítása, minden pozíció "A"-ra áll."""
        self.length = max(1, int(length))
        self.alphabet = TEAM_ALPHABET if allow_space else ALPHABET
        self.char_indices = [0] * self.length
        self.cursor = 0
        self.done = False

    def prev_char(self):
        if self.done:
            return
        i = self.cursor
        self.char_indices[i] = (self.char_indices[i] - 1) % len(self.alphabet)

    def next_char(self):
        if self.done:
            return
        i = self.cursor
        self.char_indices[i] = (self.char_indices[i] + 1) % len(self.alphabet)

    def confirm(self):
        """Lezárja az aktuális karaktert, a kurzor a következőre lép.
        A 3. karakter után a név készen van."""
        if self.done:
            return
        self.cursor += 1
        if self.cursor >= self.length:
            self.done = True

    def skip(self):
        """Start gomb: a jelenlegi állással azonnal lezárja a nevet."""
        self.done = True

    def get_chars(self) -> list:
        return [self.alphabet[i] for i in self.char_indices]

    def get_name(self) -> str:
        return "".join(self.get_chars()).rstrip()
