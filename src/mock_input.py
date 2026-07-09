"""Billentyűzettel szimulált Teensy/Arduino események."""

import random
import pygame
from protocol import GameEvent

class MockInputController:
    def __init__(self):
        # Az Arduino-ban is ezek a változók vannak, itt is ezeket tartjuk karban
        self._num_players = 1
        self._player = 1
        self._ball = 1
        self._scores = {1: 0, 2: 0, 3: 0, 4: 0}
        self._bonus = 0
        self._bonusx = 0  # 0..4 (x1, x2, x4, x6, x8)

    def poll_events(self, pygame_events) -> list[GameEvent]:
        events = []
        for pg_event in pygame_events:
            if pg_event.type != pygame.KEYDOWN:
                continue

            key = pg_event.key

            # 0. Titkos szerviz menu (Ctrl+M) - VALODI, a Pi-hez csatlakoztatott
            # billentyuzettel nyithato meg, barmikor. Amig a szerviz menu aktiv,
            # a main.py mar nem is ezen a fuggvenyen keresztul kuldi tovabb a
            # billentyu-eventeket (lasd main.py), ugyhogy itt nincs utkozes a
            # tobbi (W/R/B/P/stb.) tesztgombbal.
            if key == pygame.K_m and (pg_event.mod & pygame.KMOD_CTRL):
                events.append(GameEvent("SERVICE_MENU_ENTER", ()))
                continue

            # 0b. Esc - "vissza az attract-loopba" gyorsgomb: barmikor,
            # amikor NEM az attract-loop fut (pl. teszteles kozben
            # beragadtal egy dev-elonezeti kepernyon), Esc visszadob a
            # loop elejere. Ha mar a loopban vagyunk, nem csinal semmit.
            if key == pygame.K_ESCAPE:
                events.append(GameEvent("ESCAPE_TO_ATTRACT", ()))
                continue

            # 1. Player szám választás (P - a valódi gépen a zöld/shoot
            # gomb) - EGYBEN a NAME_ENTRY "következő betű" (PLAYER_PRESS)
            # gombja is. A state_machine dönti el az aktuális állapot
            # alapján, hogy melyik viselkedés érvényes.
            if key == pygame.K_p:
                self._num_players = (self._num_players % 4) + 1
                # Ezt az eseményt küldjük, hogy a StateMachine tudja: váltani kell
                events.append(GameEvent("PLAYERCOUNT_NEXT", ()))
                events.append(self._generate_score_event())
                events.append(GameEvent("PLAYER_PRESS", ()))

            # 1b. NAME_ENTRY betűváltás (bal/jobb nyíl - a valódi flipper
            # gombok helyett, amíg a teljes flipper-bekötés nincs kész)
            elif key == pygame.K_LEFT:
                events.append(GameEvent("FLIPPER_LEFT", ()))

            elif key == pygame.K_RIGHT:
                events.append(GameEvent("FLIPPER_RIGHT", ()))

            # 1c. Start gomb (a valodi gepen piros Start gomb, itt S) -
            # NAME_ENTRY-ben: skip, attract-loopban: kilepes SCORE-ba
            elif key == pygame.K_s:
                events.append(GameEvent("START", ()))

            # 1d. IDEIGLENES teszt-gomb (I) a PRESS_START attract-kepernyo
            # elovezetesehez - a teljes attract-loop meg nincs megepitve,
            # ez csak addig kell, amig ki nem alakul a vegleges belepesi mod.
            elif key == pygame.K_i:
                events.append(GameEvent("ATTRACT", ()))

            # 1e. IDEIGLENES teszt-gomb (T) a SPECIAL_THANKS attract-kepernyo
            # elonezetehez - ugyanugy ideiglenes, mint az (I), amig a teljes
            # attract-loop nincs osszerakva.
            elif key == pygame.K_t:
                events.append(GameEvent("DEV_THX", ()))

            # 1f. IDEIGLENES teszt-gomb (L) a LOGO attract-kepernyo
            # elonezetehez - ugyanugy ideiglenes, mint az (I)/(T).
            elif key == pygame.K_l:
                events.append(GameEvent("DEV_LOGO", ()))

            # 1g. IDEIGLENES teszt-gomb (K) a BEAT_SCORE attract-kepernyo
            # elonezetehez - ugyanugy ideiglenes, mint az (I)/(T)/(L).
            elif key == pygame.K_k:
                events.append(GameEvent("DEV_BEAT_SCORE", ()))

            # 2. Pontszerzés (W)
            elif key == pygame.K_w:
                self._scores[self._player] += 1500
                self._bonus += 250
                # Szorzó növelése (néha)
                if random.random() > 0.8 and self._bonusx < 4:
                    self._bonusx += 1
                events.append(self._generate_score_event())

            # 3. Videó trigger (R)
            elif key == pygame.K_r:
                videos = ["Drift", "Danger", "5000", "BEEEER3", "BEEEER2", "BEEEER1"]
                events.append(GameEvent("VIDEO", (random.choice(videos),)))

            # 4. Labda leesik (B) - EZ A KULCS
            elif key == pygame.K_b:
                # 1. Küldünk egy SCORE_UPDATE-et a VÉGSŐ pontszámmal (Summary-hoz)
                events.append(self._generate_score_event())

                # 2. Döntés: NEXT vagy GAMEOVER
                if self._player == self._num_players and self._ball >= 3:
                    events.append(GameEvent("GAMEOVER", ()))
                    # Reset
                    self._scores = {1: 0, 2: 0, 3: 0, 4: 0}
                    self._player = 1
                    self._ball = 1
                else:
                    events.append(GameEvent("NEXT", ()))
                    
                    # Logika: hozzáadjuk a bónuszt a jelenlegihez
                    mult = self._get_multiplier(self._bonusx)
                    self._scores[self._player] += (self._bonus * mult)
                    
                    # Következő játékos / labda
                    if self._player >= self._num_players:
                        self._player = 1
                        self._ball += 1
                    else:
                        self._player += 1
                    
                    # Bónuszok nullázása
                    self._bonus = 0
                    self._bonusx = 0
                    
                    # Küldjük az új állást a következő játékosnak
                    events.append(self._generate_score_event())

        return events

    def _generate_score_event(self):
        """Hajszálpontosan azt a formátumot küldi, amit a StateMachine vár."""
        return GameEvent("SCORE_UPDATE", (
            self._scores[self._player],
            self._num_players,
            self._player,
            self._ball,
            self._bonus,
            self._bonusx
        ))

    def _get_multiplier(self, bonusx_index):
        if bonusx_index == 1: return 2
        if bonusx_index == 2: return 4
        if bonusx_index == 3: return 6
        if bonusx_index == 4: return 8
        return 1