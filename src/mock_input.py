"""Billentyűzettel szimulált Teensy soros események, fejlesztői teszteléshez."""

import random
import pygame
from protocol import GameEvent

# Pontszám, amennyit egy gombnyomás hozzáad az aktív játékos pontjához.
SCORE_INCREMENT = 1000


class MockInputController:
    """
    Pygame KEYDOWN eseményekből generál GameEvent-eket, ugyanúgy, mintha
    a Teensy küldte volna soros porton.
    """

    def __init__(self):
        self._current_player = 1
        self._ball = 1
        self._active_player_count = 1
        self._player_scores = {1: 0, 2: 0, 3: 0, 4: 0}

        # Kiterjesztések nélkül, a mappádból kiszedett pontos nevekkel
        self._available_video_events = [
            "Multiball1", "Multiball2", "Multiball3", "Multiball4",
            "Point1", "Point2", "Point3", "Point4", "Point5", "Point6", "Point7", "Point8",
            "Weed", "Drift",
            "Jackpot1", "Jackpot2", "Jackpot3", "Jackpot4", "Jackpot5", "Jackpot6",
            "Bonus1", "Bonus2", "Bonus3", "Bonus4", "ExtraB",
            "Ufo1", "Ufo2", "Ufo3", "Ufo4", "Ufo5", "Ufo6", "Ufo7", "Ufo8", "Ufo9", "Ufo10", "Ufo11", "Ufo12", "Ufo13",
            "Beer1", "Beer2", "Beer3",
            "Combo1", "Combo2", "Combo3", "Combo4", "Combo5", "Combo6",
            "Danger", "Tilt",
            "ChongC1", "ChongC2", "ChongC3",
            "CheechC1", "CheechC2", "CheechC3"
        ]

    def poll_events(self, pygame_events) -> list[GameEvent]:
        events = []

        for pg_event in pygame_events:
            if pg_event.type != pygame.KEYDOWN:
                continue

            key = pg_event.key

            # --- Fizikai bemenetek a flipperen ---
            if key == pygame.K_LEFT:
                events.append(GameEvent("LEFT_FLIPPER"))

            elif key == pygame.K_RIGHT:
                events.append(GameEvent("RIGHT_FLIPPER"))

            elif key == pygame.K_s:
                events.append(GameEvent("START"))

            elif key == pygame.K_p:
                self._active_player_count = (self._active_player_count % 4) + 1
                events.append(GameEvent("PLAYERCOUNT_NEXT"))

            # --- Event kontrollerek / Játék logika szimuláció ---
            elif key == pygame.K_w:
                self._player_scores[self._current_player] += SCORE_INCREMENT
                events.append(GameEvent(
                    "SCORE", (self._current_player, self._player_scores[self._current_player])
                ))

            elif key == pygame.K_r:
                # KIZÁRÓLAG egy véletlenszerű videó eseményt küldünk el, semmi mást!
                random_video = random.choice(self._available_video_events)
                events.append(GameEvent("VIDEO", (random_video,)))

            elif key == pygame.K_b:
                if self._current_player >= self._active_player_count:
                    self._current_player = 1
                    self._ball += 1
                else:
                    self._current_player += 1

                events.append(GameEvent("PLAYER", (self._current_player,)))
                events.append(GameEvent("BALL", (self._ball,)))

        return events