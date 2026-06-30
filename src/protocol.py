"""Teensy/Arduino soros parancsainak feldolgozása GameEvent objektumokká."""

from dataclasses import dataclass
from typing import Optional

@dataclass
class GameEvent:
    """Egy feldolgozott parancs a hardvertől vagy a mock inputtól."""
    kind: str          # "SCORE_UPDATE", "NEXT", "GAMEOVER", "VIDEO", "VIDEO_STOP", stb.
    args: tuple = ()

def parse_line(line: str) -> Optional[GameEvent]:
    """Egy nyers soros sort alakít GameEvent-té."""
    line = line.strip()
    if not line:
        return None

    parts = line.split(",")
    cmd = parts[0].upper()

    try:
        # Az Arduino SendData() ezt küldi: 
        # score, score_value, num_players, player, ball, bonus, bonusx
        if cmd == "SCORE" and len(parts) >= 7:
            return GameEvent("SCORE_UPDATE", (
                int(parts[1]), # score
                int(parts[2]), # num_players
                int(parts[3]), # player
                int(parts[4]), # ball
                int(parts[5]), # bonus
                int(parts[6])  # bonusx index
            ))

        elif cmd == "NEXT":
            return GameEvent("NEXT")

        elif cmd == "END":
            return GameEvent("GAMEOVER")

        elif cmd == "VIDEO" and len(parts) == 2:
            if parts[1].upper() == "STOP":
                return GameEvent("VIDEO_STOP")
            return GameEvent("VIDEO", (parts[1],))

        elif cmd in ["MULTIBALL_ON", "MULTIBALL_OFF", "ATTRACT", "PLAYERCOUNT_NEXT"]:
            return GameEvent(cmd)

        else:
            # Ha az Arduino csak egyetlen szót küldött (pl. "Drift", "Point1", "Jackpot2"),
            # és az nem a fenti parancsok egyike, akkor az egy VIDEÓ / EFFEKT trigger!
            if len(parts) == 1:
                return GameEvent("VIDEO", (parts[0],))

    except (ValueError, IndexError):
        # Hibás formátumú sor, ignoráljuk
        pass

    return None