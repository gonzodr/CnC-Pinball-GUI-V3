"""Teensy/Arduino soros parancsainak feldolgozása GameEvent objektumokká."""

from dataclasses import dataclass
from typing import Optional
from game_modes import (
    GAME_MODE_COUNT,
    GAME_MODE_MASK_ALL,
    normalize_game_mode,
    sanitize_availability_mask,
)

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
            score_args = (
                int(parts[1]), # score
                int(parts[2]), # num_players
                int(parts[3]), # player
                int(parts[4]), # ball
                int(parts[5]), # bonus
                int(parts[6])  # bonusx index
            )
            # Uj firmware: a futas elejen lezart mod az utolso mezo. A regi
            # hetmezos SCORE sor tovabbra is teljesen kompatibilis marad.
            if len(parts) >= 8:
                running_mode = int(parts[7])
                if not 0 <= running_mode < GAME_MODE_COUNT:
                    return None
                score_args += (running_mode,)
            return GameEvent("SCORE_UPDATE", score_args)

        elif cmd == "GAME_MODE" and len(parts) == 3:
            mode_id = int(parts[1])
            availability_mask = sanitize_availability_mask(int(parts[2]))
            mode_id = normalize_game_mode(mode_id, availability_mask)
            return GameEvent("GAME_MODE_STATE", (mode_id, availability_mask))

        elif cmd == "GAME_MODE_CONFIRM" and len(parts) == 2:
            mode_id = int(parts[1])
            if not 0 <= mode_id < GAME_MODE_COUNT:
                return None
            return GameEvent("GAME_MODE_CONFIRM", (mode_id,))

        elif cmd == "GAME_START" and len(parts) == 3:
            mode_id, player_count = map(int, parts[1:3])
            if not 1 <= player_count <= 4:
                return None
            mode_id = normalize_game_mode(
                mode_id, GAME_MODE_MASK_ALL, player_count=player_count
            )
            return GameEvent("GAME_START", (mode_id, player_count))

        elif cmd == "MAYHEM_PLAYER" and len(parts) == 2:
            player = int(parts[1])
            if not 1 <= player <= 4:
                return None
            return GameEvent("MAYHEM_PLAYER", (player,))

        elif cmd == "MAYHEM_READY" and len(parts) == 3:
            player, countdown = map(int, parts[1:3])
            if not 1 <= player <= 4 or not 0 <= countdown <= 5:
                return None
            return GameEvent("MAYHEM_READY", (player, countdown))

        elif cmd == "MAYHEM_STAGE" and len(parts) == 5:
            player, stage, seconds, required = map(int, parts[1:5])
            if not 1 <= player <= 4 or not 1 <= stage <= 4:
                return None
            if not 1 <= seconds <= 120 or not 1 <= required <= 99:
                return None
            return GameEvent("MAYHEM_STAGE", (player, stage, seconds, required))

        elif cmd == "MAYHEM_PROGRESS" and len(parts) == 5:
            stage, jackpots, required, super_lit = map(int, parts[1:5])
            if not 1 <= stage <= 4 or jackpots < 0 or required < 1:
                return None
            if super_lit not in (0, 1):
                return None
            return GameEvent(
                "MAYHEM_PROGRESS", (stage, jackpots, required, bool(super_lit))
            )

        elif cmd == "MAYHEM_SUPER_LIT" and len(parts) == 1:
            return GameEvent("MAYHEM_SUPER_LIT")

        elif cmd == "MAYHEM_STAGE_END" and len(parts) == 4:
            stage, jackpots, bonus_seconds = map(int, parts[1:4])
            if not 1 <= stage <= 4 or jackpots < 0 or not 0 <= bonus_seconds <= 10:
                return None
            return GameEvent("MAYHEM_STAGE_END", (stage, jackpots, bonus_seconds))

        elif cmd == "MAYHEM_RESULT" and len(parts) == 3:
            player, total = map(int, parts[1:3])
            if not 1 <= player <= 4 or total < 0:
                return None
            return GameEvent("MAYHEM_RESULT", (player, total))

        elif cmd == "MAYHEM_FINISH" and len(parts) == 2:
            winner = int(parts[1])
            if not 1 <= winner <= 4:
                return None
            return GameEvent("MAYHEM_FINISH", (winner,))

        elif cmd == "MUNCHIES_PLAYER" and len(parts) == 2:
            player = int(parts[1])
            if not 1 <= player <= 4:
                return None
            return GameEvent("MUNCHIES_PLAYER", (player,))

        elif cmd == "MUNCHIES_RESULT" and len(parts) == 3:
            player, total = map(int, parts[1:3])
            if not 1 <= player <= 4 or total < 0:
                return None
            return GameEvent("MUNCHIES_RESULT", (player, total))

        elif cmd == "MUNCHIES_FINISH" and len(parts) == 2:
            winner = int(parts[1])
            if not 1 <= winner <= 4:
                return None
            return GameEvent("MUNCHIES_FINISH", (winner,))

        elif cmd == "NEXT":
            if len(parts) == 2:
                session = int(parts[1])
                if not 1 <= session <= 0xFFFF:
                    return None
                return GameEvent("NEXT", (session,))
            return GameEvent("NEXT")

        elif cmd == "END":
            return GameEvent("GAMEOVER")

        elif cmd == "VIDEO" and len(parts) == 2:
            if parts[1].upper() == "STOP":
                return GameEvent("VIDEO_STOP")
            return GameEvent("VIDEO", (parts[1],))

        elif cmd == "PARTY" and len(parts) == 6:
            # PARTY,<player>,<beer>,<joint>,<ufo-tier>,<weed-qualified>
            player, beers, joints, ufo_tier, weed_ready = map(int, parts[1:6])
            if not 1 <= player <= 4:
                return None
            if not 0 <= beers <= 3 or not 0 <= joints <= 3:
                return None
            if not 0 <= ufo_tier <= 4 or weed_ready not in (0, 1):
                return None
            return GameEvent("PARTY_STATE", (
                player, beers, joints, ufo_tier, bool(weed_ready)
            ))

        # HurryUp,<masodperc> = indul (a firmware kuldi a sajat hosszat),
        # HurryUp,0 = vege. A GUI ebbol rakja ki a lukteto 2X-et es szamolja
        # vissza az idot; igy a firmware idotartamanak atirasa eleg egy helyen.
        elif cmd == "HURRYUP" and len(parts) == 2:
            seconds = int(parts[1])
            if not 0 <= seconds <= 600:
                return None
            return GameEvent("HURRY_UP", (seconds,))

        elif cmd == "PARTYEVENT" and len(parts) == 3:
            player = int(parts[1])
            if not 1 <= player <= 4 or not parts[2]:
                return None
            return GameEvent("PARTY_EVENT", (player, parts[2].upper()))

        elif cmd == "STEAL" and len(parts) == 3:
            victim, amount = map(int, parts[1:3])
            if not 1 <= victim <= 4 or amount < 0:
                return None
            return GameEvent("SCORE_STEAL", (victim, amount))

        elif cmd in ("WHEELSTART", "WHEEL_START") and len(parts) == 3:
            session = int(parts[1])
            result = parts[2].upper()
            if not 1 <= session <= 0xFFFF:
                return None
            if result not in ("EXTRABALL", "HURRYUP", "MUNCHIES"):
                return None
            return GameEvent("UFO_WHEEL_START", (session, result))

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

        # --- Hatterben futo szenzor-diagnosztikai naplozas ---------------
        # SENSOR_LOG,STARTED,ms,a0,...,a5,stableMask,trustedBIS,rawBIS,
        #                    stateFlags,<dinamikus diagnosztikai mezok...>
        # a firmware visszaigazolasa es a CSV mezok nevei. A meresi sor
        # mindig hat nyers ADC-erteket tartalmaz:
        # SENSOR_DATA,<millis>,<median1>...<median6>,<stableMask>,<trustedBIS>,
        #             <rawBIS>,<stateFlags>,<dinamikus ertekek...>
        # Ezek kulon esemenyek, nehogy az altalanos video-trigger agra
        # essenek. A nagy frekvenciaju SENSOR_DATA sorokat a SerialReader
        # sajat hatterszalan irja fajlba, a fo GUI-queue-ba nem teszi be.
        elif cmd == "SENSOR_LOG" and len(parts) >= 2:
            action = parts[1].upper()
            if action == "STARTED":
                # Minimum schema: ms + 6 ADC + stableMask + trusted/raw BIS
                # + stateFlags. Az ezutan jovo mezoket a fejléc nevezi el,
                # ezert kesobbi firmware-boviteshez nem kell uj parser.
                if len(parts) < 13:
                    return None
                names = tuple(name.strip() for name in parts[2:] if name.strip())
                return GameEvent("SENSOR_LOG_STARTED", (names,))
            if action == "STOPPED":
                return GameEvent("SENSOR_LOG_STOPPED")
            if action == "ERROR":
                reason = ",".join(parts[2:]).strip() or "?"
                return GameEvent("SENSOR_LOG_ERROR", (reason,))
            return None

        elif cmd == "SENSOR_DATA" and len(parts) >= 12:
            firmware_millis = int(parts[1], 0)
            raw_values = tuple(int(value, 0) for value in parts[2:8])
            stable_mask = int(parts[8], 0)
            trusted_bis = int(parts[9], 0)
            raw_bis = int(parts[10], 0)
            state_flags = int(parts[11], 0)
            trailing_values = tuple(int(value, 0) for value in parts[12:])
            return GameEvent("SENSOR_DATA", (
                firmware_millis,
                raw_values,
                stable_mask,
                trusted_bis,
                raw_bis,
                state_flags,
                trailing_values,
            ))

        elif cmd in ("SUMMARY_ACK", "SUMMARY_TIMEOUT"):
            # Firmware-statusz, nem video-trigger. Az ACK a kovetkezo golyo
            # kiadasi kapujanak feloldasat, a TIMEOUT a tartalek utat jelzi.
            if len(parts) == 2:
                session = int(parts[1])
                if not 1 <= session <= 0xFFFF:
                    return None
                return GameEvent(cmd, (session,))
            return GameEvent(cmd)

        elif cmd in ["MULTIBALL_ON", "MULTIBALL_OFF", "ATTRACT", "PLAYERCOUNT_NEXT",
                     "START", "FLIPPER_LEFT", "FLIPPER_RIGHT", "PLAYER_PRESS", "PLUNGER",
                     "SERVICE_MENU_ENTER", "SERVICE_LEFT", "SERVICE_RIGHT",
                     "SERVICE_CONFIRM", "SERVICE_BACK", "SERVICE_ARMED",
                     "SERVICE_DISARMED", "SERVICE_DRAINED",
                     "FLIPPER_LEFT_DOWN", "FLIPPER_LEFT_UP", "FLIPPER_RIGHT_DOWN",
                     "FLIPPER_RIGHT_UP", "PLUNGER_DOWN", "PLUNGER_UP"]:
            return GameEvent(cmd)

        else:
            # Ha az Arduino csak egyetlen szót küldött (pl. "Drift", "Point1", "Jackpot2"),
            # és az nem a fenti parancsok egyike, akkor az egy VIDEÓ / EFFEKT trigger!
            # KIVÉVE az ismert nem-videó üzeneteket (a "Zero" a játékindítás jelzése,
            # sosem volt hozzá videófájl).
            if (len(parts) == 1 and cmd not in ("ZERO",)
                    and not cmd.startswith("MG_") and not cmd.startswith("AT_")
                    and not cmd.startswith("SENSOR_")):
                return GameEvent("VIDEO", (parts[0],))

    except (ValueError, IndexError):
        # Hibás formátumú sor, ignoráljuk
        pass

    return None
