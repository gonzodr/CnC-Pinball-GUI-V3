"""Teensy soros parancsainak feldolgozása GameEvent objektumokká."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class GameEvent:
    """Egy feldolgozott parancs a Teensy-től."""
    kind: str          # "SCORE", "PLAYER", "BALL", "VIDEO", "VIDEO_STOP",
                        # "MULTIBALL_ON", "MULTIBALL_OFF", "GAMEOVER", "ATTRACT",
                        # "PLAYERCOUNT_NEXT"
    args: tuple = ()


def parse_line(line: str) -> Optional[GameEvent]:
    """Egy nyers soros sort alakít GameEvent-té. None, ha ismeretlen/hibás."""
    line = line.strip()
    if not line:
        return None

    parts = line.split(",")
    cmd = parts[0].upper()

    try:
        if cmd == "SCORE" and len(parts) == 3:
            player = int(parts[1])
            score = int(parts[2])
            return GameEvent("SCORE", (player, score))

        elif cmd == "PLAYER" and len(parts) == 2:
            return GameEvent("PLAYER", (int(parts[1]),))

        elif cmd == "BALL" and len(parts) == 2:
            return GameEvent("BALL", (int(parts[1]),))

        elif cmd == "VIDEO" and len(parts) == 2:
            if parts[1].upper() == "STOP":
                return GameEvent("VIDEO_STOP")
            return GameEvent("VIDEO", (parts[1],))

        elif cmd == "MULTIBALL" and len(parts) == 2:
            if parts[1].upper() == "ON":
                return GameEvent("MULTIBALL_ON")
            elif parts[1].upper() == "OFF":
                return GameEvent("MULTIBALL_OFF")

        elif cmd == "GAMEOVER":
            return GameEvent("GAMEOVER")

        elif cmd == "ATTRACT":
            return GameEvent("ATTRACT")

        elif cmd == "PLAYERCOUNT_NEXT":
            # A fizikai player-selector gomb minden megnyomására a
            # Teensy ezt küldi. A Pi dönti el ciklikusan (1->2->3->4->1),
            # hogy hány kártya legyen látható.
            return GameEvent("PLAYERCOUNT_NEXT")

    except (ValueError, IndexError):
        # rossz formátumú sor — logoljuk, de nem dobunk exceptiont,
        # mert a Teensy oldal sosem vár választ, és egy hibás sor
        # miatt nem szabad leállnia a Pi szoftvernek
        pass

    return None