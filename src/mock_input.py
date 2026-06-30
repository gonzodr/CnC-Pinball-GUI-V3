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

            # 1. Player szám választás (P)
            if key == pygame.K_p:
                self._num_players = (self._num_players % 4) + 1
                # Ezt az eseményt küldjük, hogy a StateMachine tudja: váltani kell
                events.append(GameEvent("PLAYERCOUNT_NEXT", ()))
                events.append(self._generate_score_event())

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
                videos = ["Drift", "Point1", "Multiball1", "Jackpot2"]
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