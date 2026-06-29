"""Billentyűzettel szimulált Teensy soros események, fejlesztői teszteléshez.

Mivel ilyenkor nincs valódi Teensy/soros port, ez a modul a pygame
billentyű-eseményeit alakítja ugyanolyan GameEvent objektumokká, mint
amiket a SerialReader generálna a valódi soros adatokból.

A main.py ezt a SerialReader MELLETT hívja meg minden frame-ben - tehát
a billentyűzet és a soros port egyszerre is "élhet", ez segít abban,
hogy a hardver csatlakoztatása után se kelljen semmit kikapcsolni.

Billentyű-kiosztás:
  1 / 2 / 3 / 4    - aktív jatekos valtasa (PLAYER,<n>)
  Q / W / E / R    - az aktiv jatekos pontszamat noveli (SCORE)
  B                - kovetkezo labda (BALL,<n+1>)
  M                - multiball ON/OFF kapcsolasa
  V                - teszt-video lejatszasa (VIDEO,test)
  S                - video leallitasa (VIDEO,STOP)
  G                - GAMEOVER esemeny
  A                - ATTRACT esemeny
  P                - player selector gomb (PLAYERCOUNT_NEXT) - ciklikusan
                     1 -> 2 -> 3 -> 4 -> 1 lathato kartyak szama
"""

import pygame
from protocol import GameEvent


# Pontszám, amennyit egy gombnyomás hozzáad az aktív játékos pontjához.
# Ez csak teszteléshez kell, hogy gyorsan lássunk változó számokat a GUI-n.
SCORE_INCREMENT = 1000000


class MockInputController:
    """
    Pygame KEYDOWN eseményekből generál GameEvent-eket, ugyanúgy, mintha
    a Teensy küldte volna soros porton.

    Belső állapotot tart (jelenlegi labda száma, multiball be/ki), mert
    a billentyűknek "emelkedő/kapcsoló" jellegű viselkedést kell adniuk
    (pl. B mindig a KÖVETKEZŐ labdára vált, nem egy fix számra).
    """

    def __init__(self):
        self._ball = 1
        self._multiball_on = False
        self._current_player = 1

    def poll_events(self, pygame_events) -> list[GameEvent]:
        """
        A pygame.event.get() által visszaadott eseménylistából szűri ki
        a KEYDOWN eseményeket, és GameEvent-ekre fordítja őket.

        Fontos: a pygame eseménysort csak EGY helyen szabad kiolvasni
        (pygame.event.get() "elfogyasztja" a sort) - ezért ez a metódus
        nem hívja meg saját maga a pygame.event.get()-et, hanem a
        main.py-tól kapja meg a már lekérdezett listát.
        """
        events = []

        for pg_event in pygame_events:
            if pg_event.type != pygame.KEYDOWN:
                continue

            key = pg_event.key

            if key == pygame.K_1:
                self._current_player = 1
                events.append(GameEvent("PLAYER", (1,)))
            elif key == pygame.K_2:
                self._current_player = 2
                events.append(GameEvent("PLAYER", (2,)))
            elif key == pygame.K_3:
                self._current_player = 3
                events.append(GameEvent("PLAYER", (3,)))
            elif key == pygame.K_4:
                self._current_player = 4
                events.append(GameEvent("PLAYER", (4,)))

            elif key in (pygame.K_q, pygame.K_w, pygame.K_e, pygame.K_r):
                # Q/W/E/R mind az AKTIV jatekos pontszamat noveli - igy
                # egy kez a billentyuzeten elfer a teszteleshez, nem kell
                # tudni melyik jatekos van soron.
                events.append(GameEvent(
                    "SCORE", (self._current_player, SCORE_INCREMENT)
                ))

            elif key == pygame.K_b:
                self._ball += 1
                events.append(GameEvent("BALL", (self._ball,)))

            elif key == pygame.K_m:
                self._multiball_on = not self._multiball_on
                kind = "MULTIBALL_ON" if self._multiball_on else "MULTIBALL_OFF"
                events.append(GameEvent(kind))

            elif key == pygame.K_v:
                events.append(GameEvent("VIDEO", ("test",)))

            elif key == pygame.K_s:
                events.append(GameEvent("VIDEO_STOP"))

            elif key == pygame.K_g:
                events.append(GameEvent("GAMEOVER"))

            elif key == pygame.K_a:
                events.append(GameEvent("ATTRACT"))

            elif key == pygame.K_p:
                events.append(GameEvent("PLAYERCOUNT_NEXT"))

        return events