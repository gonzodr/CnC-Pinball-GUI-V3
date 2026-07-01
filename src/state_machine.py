"""A fő állapotgép: SCORE <-> VIDEO <-> SUMMARY váltás, és a játékállás nyilvántartása."""

import time
from enum import Enum, auto
from protocol import GameEvent
from mpv_controller import MpvController

class AppState(Enum):
    SCORE = auto()     # alap állapot: pontszám-GUI látható
    VIDEO = auto()     # videólejátszás aktív, GUI elrejtve
    SUMMARY = auto()   # bónusz / kör végi összegző képernyő


class StateMachine:
    SUMMARY_DURATION_SEC = 4.0  # Ennyi ideig látszódik a bónusz képernyő

    def __init__(self, mpv: MpvController):
        self.mpv = mpv
        self.state = AppState.SCORE
        self._previous_state = AppState.SCORE
        self.pending_video = None
        
        self.players = {1: 0, 2: 0, 3: 0, 4: 0}
        self.current_player = 1
        self.current_ball = 1
        self.active_player_count = 1
        self._previous_player_count = 1
        self.multiball_active = False
        
        # Bónusz adatok nyilvántartása
        self.current_bonus = 0
        self.current_bonusx = 0

        self.summary_data = {
            "player": 1,
            "old_score": 0,
            "multiplier": 1,
            "bonus_points": 0
        }
        self._summary_end_time = 0.0

    def _get_multiplier(self, bonusx_index):
        """Átváltja az Arduino bonusx indexét (0-4) igazi szorzóvá (1,2,4,6,8)."""
        if bonusx_index == 1: return 2
        if bonusx_index == 2: return 4
        if bonusx_index == 3: return 6
        if bonusx_index == 4: return 8
        return 1

    def handle_event(self, event: GameEvent):
        """Egy beérkezett Teensy/Mock parancs hatására frissíti az állapotot."""

        if event.kind == "SCORE_UPDATE":
            score, num_players, player, ball, bonus, bonusx = event.args
            
            self.players[player] = score
            self.active_player_count = num_players
            self.current_player = player
            self.current_ball = ball
            self.current_bonus = bonus
            self.current_bonusx = bonusx
            
            # Ha épp nem SUMMARY módban vagyunk, a GUI ezt fogja mutatni
            if self.state != AppState.SUMMARY:
                self.state = AppState.SCORE

        elif event.kind == "NEXT" or event.kind == "GAMEOVER":
            # 1. Kiszámoljuk a szorzót és a végleges bónuszt a mentett értékekből
            mult = self._get_multiplier(self.current_bonusx)
            bonus_total = self.current_bonus * mult
            
            # 2. Összerakjuk a bónusz képernyő (summary) adatait
            self.summary_data = {
                "player": self.current_player,
                "old_score": self.players[self.current_player],
                "multiplier": mult,
                "bonus_points": bonus_total
            }
            
            # 3. Hozzáadjuk a bónuszt a játékoshoz
            self.players[self.current_player] += bonus_total

            # 4. Elindítjuk az összegző animációt
            self._start_summary()
            
            # 5. HA GAME OVER: A háttérben azonnal lenullázzuk az összes pontot és állapotot.
            if event.kind == "GAMEOVER":
                self.players = {1: 0, 2: 0, 3: 0, 4: 0}
                self.current_player = 1
                self.current_ball = 1
                self.active_player_count = 1
                self.current_bonus = 0
                self.current_bonusx = 0

        elif event.kind == "VIDEO":
            if self.state == AppState.SCORE:
                self.pending_video = event.args[0]
                self.state = AppState.VIDEO

        elif event.kind == "VIDEO_STOP":
            if self.state == AppState.VIDEO:
                self.mpv.stop()
                self.state = AppState.SCORE

        elif event.kind == "PLAYERCOUNT_NEXT":
            # 1->2->3->4->1 ciklikus léptetés
            new_count = self.active_player_count + 1
            if new_count > 4:
                new_count = 1
            self.active_player_count = new_count

    def _start_summary(self):
        """Belső segédfüggvény a bónusz mód élesítésére."""
        self._summary_end_time = time.time() + self.SUMMARY_DURATION_SEC
        self.state = AppState.SUMMARY

    def tick(self):
        """Minden frame-ben ellenőrzi az időzítéseket."""
        if self.state == AppState.VIDEO and self.mpv.is_finished():
            self.state = AppState.SCORE
            
        elif self.state == AppState.SUMMARY:
            if time.time() >= self._summary_end_time:
                self.state = AppState.SCORE

    def consume_transition(self):
        if self.state != self._previous_state:
            transition = (self._previous_state, self.state)
            self._previous_state = self.state
            return transition
        return None

    def consume_player_count_change(self):
        if self.active_player_count != self._previous_player_count:
            change = (self._previous_player_count, self.active_player_count)
            self._previous_player_count = self.active_player_count
            return change
        return None