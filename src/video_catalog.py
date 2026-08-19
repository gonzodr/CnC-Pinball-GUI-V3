"""Serial video trigger aliases for the external PNG sequence library.

The cabinet firmware still emits the short names inherited from the old
Unity/mp4 setup (for example ``Point1`` or ``Multiball2``).  The Pygame video
engine indexes sequence directory names instead.  Keeping the translation in
one small module makes the serial and local mock paths use the same catalog.

The table doubles as the registry of what ``CnC_firmware4`` can actually send:
every trigger below was read out of the sketch (literal ``Serial.println``
calls plus the ``Serial.print("Bonus"/"Multiball"/"Jackpot"/"Ufo")`` pairs and
the ``lotVid`` lottery table), so entries whose alias equals the sequence name
are kept on purpose - they document the trigger even though the resolver would
fall back to the bare name anyway.

Names the firmware never sends are marked; they stay because the GUI is also
driven by the keyboard mock and by older firmware builds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


# Values are ordered candidates.  The first existing sequence wins, so old
# historically-correct names can coexist with a temporary fallback while the
# PNG conversion set is still being completed.  A trigger whose candidates are
# all missing resolves to ``None``; state_machine logs it and skips the clip.
SERIAL_VIDEO_CANDIDATES: dict[str, tuple[str, ...]] = {
    # --- Pontertek-videok -------------------------------------------------
    # A V4 firmware csak Point1/Point2-t kuldi (a tobbi a Unity-korszakbol
    # maradt szamozas, ujabb firmware-hez keszen all).  Mind a nyolc klip
    # megvan, a 25000 is.
    "point1": ("2500",),
    "point2": ("5000",),
    "point3": ("7500",),
    "point4": ("10000",),
    "point5": ("15000",),
    "point6": ("20000",),
    "point7": ("25000",),
    "point8": ("30000",),

    # --- Beer / Bonus -----------------------------------------------------
    "beer1": ("BEEEER1",),
    "beer2": ("BEEEER2",),
    "beer3": ("BEEEER3",),
    # A firmware 1..4-et kuld, a klipek viszont a bonusz-szorzoval vannak
    # elnevezve (Bonus2/4/6/8).
    "bonus1": ("Bonus2",),
    "bonus2": ("Bonus4",),
    "bonus3": ("Bonus6",),
    "bonus4": ("Bonus8",),

    # --- UFO --------------------------------------------------------------
    # A lottery-tabla (lotVid) Ufo7/Ufo1/Ufo2/Ufo4/Ufo3/Ufo9-et kuld, az
    # Ufo5 a extraball-nyeremeny, az Ufo10..13 a pontlopas aldozata.
    # FIGYELEM, Unity-korszakbol orokolt ELCSUSZAS (a firmware megerositi):
    # az "Ufo6" trigger a "nem nyertel semmit" ag (CnC_firmware4.ino:3268,
    # effectID 4 = UFO FUCK), tehat az Ufofuck klip valo hozza; a "Ufo7"
    # triggerhez pedig az Ufo6 klip - ezert nincs Ufo7 nevu sequence.
    "ufo1": ("Ufo1",),
    "ufo2": ("Ufo2",),
    "ufo3": ("Ufo3",),
    "ufo4": ("Ufo4",),
    "ufo5": ("Ufo5",),
    "ufo6": ("Ufofuck",),
    "ufo7": ("Ufo6",),
    "ufo8": ("Ufo8",),  # a firmware sosem kuldi (arva klip)
    "ufo9": ("Ufo9",),  # SpaceCoke multiball
    "ufo10": ("Ufo10",),
    "ufo11": ("Ufo11",),
    "ufo12": ("Ufo12",),
    "ufo13": ("Ufo13",),

    # --- Karakter-klipek --------------------------------------------------
    "cheechc1": ("CheechC1",),
    "cheechc2": ("CheechC2",),
    "cheechc3": ("CheechC3",),
    "chongc1": ("ChongC1",),
    "chongc2": ("ChongC2",),
    "chongc3": ("ChongC3",),

    # --- Egyedi trigger-nevek ---------------------------------------------
    "weed": ("Weed",),
    "weed1": ("Weed",),  # regi alias, a V4 mar sima "Weed"-et kuld
    "drift": ("Drift",),
    "danger": ("Danger",),
    "jackpot1": ("PsychedelicJackpot", "PsyJackpot"),  # a V4 sosem kuldi
    "psyjackpot": ("PsychedelicJackpot", "PsyJackpot"),

    # --- Meg nincs PNG sequence -------------------------------------------
    # Ezeket a firmware kuldi (a Tilt-et es a Jackpot2..6/Combo/Multiball
    # harmast biztosan), de a Test/ konyvtarban meg nincs hozzajuk sorozat,
    # ezert egyelore nema marad a trigger.  Amint elkeszul a konverzio, a
    # bejegyzes valtoztatas nelkul elkezd mukodni.
    "tilt": ("Tilt",),
    "combo1": ("Combo2500",),
    "combo2": ("Combo5000",),
    "combo3": ("Combo7500",),
    "combo4": ("Combo10000",),
    "combo5": ("Combo15000",),
    "combo6": ("Combo20000",),
    "multiball1": ("Michokan",),
    "multiball2": ("Acapulco Gold",),
    "multiball3": ("Thai Stick",),
    "multiball4": ("Labrador",),
    "jackpot2": ("Jackpot2",),
    "jackpot3": ("Jackpot3",),
    "jackpot4": ("Jackpot4",),
    "jackpot5": ("Jackpot5",),
    "jackpot6": ("Jackpot6",),
    "extrab": ("Extraball",),  # a V4 sosem kuldi (extra ball nema)
}


def _without_video_extension(name: str) -> str:
    cleaned = name.strip()
    if Path(cleaned).suffix.casefold() in {".mp4", ".mov", ".mkv", ".avi"}:
        return str(Path(cleaned).with_suffix(""))
    return cleaned


def resolve_serial_video_name(
    requested_name: str,
    available_names: Iterable[str],
) -> str | None:
    """Resolve a firmware/legacy clip name to an existing sequence folder.

    Matching is case-insensitive, but the returned spelling always comes from
    the actual indexed directory so it can be passed straight to the player.
    Unknown or not-yet-converted commands return ``None``.
    """

    cleaned = _without_video_extension(str(requested_name))
    available = {name.casefold(): name for name in available_names}
    candidates = SERIAL_VIDEO_CANDIDATES.get(
        cleaned.casefold(),
        (cleaned,),
    )
    for candidate in candidates:
        existing = available.get(candidate.casefold())
        if existing is not None:
            return existing
    return None
