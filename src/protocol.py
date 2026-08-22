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

        elif cmd in ("MUNCHIES", "VUK_GAME"):
            # Regi, session-azonosito nelkuli VUK trigger. Az ures args
            # szandekos: igy a friss GUI a regi firmware-rel is hasznalhato.
            return GameEvent("MUNCHIES_START")

        elif cmd == "MG_START" and len(parts) == 2:
            session = int(parts[1])
            if not 1 <= session <= 0xFFFF:
                return None
            return GameEvent("MUNCHIES_START", (session,))

        elif cmd == "MG_INPUT" and len(parts) == 4:
            # MG_INPUT,<session>,<sequence>,<bitmask>
            # bit0 = bal flipper, bit1 = jobb flipper, bit2 = kilovo/sugar
            session, sequence, mask = map(int, parts[1:4])
            if not 1 <= session <= 0xFFFF or not 0 <= sequence <= 0xFFFF:
                return None
            if not 0 <= mask <= 7:
                return None
            return GameEvent("MUNCHIES_INPUT", (
                session, sequence, mask
            ))

        elif cmd == "MG_ACK" and len(parts) == 2:
            session = int(parts[1])
            if not 1 <= session <= 0xFFFF:
                return None
            return GameEvent("MUNCHIES_ACK", (session,))

        elif cmd == "MG_ABORT" and len(parts) >= 2:
            return GameEvent("MUNCHIES_ABORT", tuple(parts[1:]))

        # --- Analog bemenet-teszt (szerviz menu, h_analog_test.ino) ---
        # AT_INFO,<db>,<nev1>,...   a szenzorok szama es neve (belepeskor)
        # AT_VAL,<e1>,...           nyers ADC-ertekek, ~5 Hz-en
        # AT_THR,<k1>,...           a jelenleg ervenyes kuszobok
        # AT_SAVED                  minden kuszob atomian elmentve az EEPROM-ba
        # AT_ERR,<ok>               BUSY (nem attract) / RANGE / CMD
        elif cmd == "AT_INFO" and len(parts) >= 2:
            return GameEvent("ANALOG_INFO", (tuple(parts[2:]),))

        elif cmd == "AT_VAL" and len(parts) >= 2:
            return GameEvent("ANALOG_VALUES", (tuple(int(v) for v in parts[1:]),))

        elif cmd == "AT_THR" and len(parts) >= 2:
            return GameEvent("ANALOG_THRESHOLDS", (tuple(int(v) for v in parts[1:]),))

        elif cmd == "AT_SAVED":
            return GameEvent("ANALOG_SAVED")

        elif cmd == "AT_ERR" and len(parts) >= 2:
            return GameEvent("ANALOG_ERROR", (parts[1],))

        elif cmd == "AT_STOPPED":
            return GameEvent("ANALOG_STOPPED")

        elif cmd in ["MULTIBALL_ON", "MULTIBALL_OFF", "ATTRACT", "PLAYERCOUNT_NEXT",
                     "START", "FLIPPER_LEFT", "FLIPPER_RIGHT", "PLAYER_PRESS", "PLUNGER",
                     "FLIPPER_LEFT_DOWN", "FLIPPER_LEFT_UP", "FLIPPER_RIGHT_DOWN",
                     "FLIPPER_RIGHT_UP", "PLUNGER_DOWN", "PLUNGER_UP"]:
            return GameEvent(cmd)

        else:
            # Ha az Arduino csak egyetlen szót küldött (pl. "Drift", "Point1", "Jackpot2"),
            # és az nem a fenti parancsok egyike, akkor az egy VIDEÓ / EFFEKT trigger!
            # KIVÉVE az ismert nem-videó üzeneteket (a "Zero" a játékindítás jelzése,
            # sosem volt hozzá videófájl).
            if (len(parts) == 1 and cmd not in ("ZERO",)
                    and not cmd.startswith("MG_") and not cmd.startswith("AT_")):
                return GameEvent("VIDEO", (parts[0],))

    except (ValueError, IndexError):
        # Hibás formátumú sor, ignoráljuk
        pass

    return None
