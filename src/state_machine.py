"""A fő állapotgép: SCORE <-> VIDEO <-> SUMMARY <-> HIGHSCORE váltás."""

import time
from enum import Enum, auto
from protocol import GameEvent
from mpv_controller import MpvController
from score_manager import ScoreManager 

class AppState(Enum):
    SCORE = auto()
    VIDEO = auto()
    SUMMARY = auto()
    HIGHSCORE = auto()

class StateMachine:
    SUMMARY_DURATION_SEC = 4.0

    def __init__(self, mpv: MpvController):
        self.mpv = mpv
        self.score_manager = ScoreManager() 
        self.state = AppState.SCORE
        self._previous_state = AppState.SCORE
        self.pending_video = None
        
        self.players = {1: 0, 2: 0, 3: 0, 4: 0}
        self.current_player = 1
        self.current_ball = 1
        self.active_player_count = 1
        self._previous_player_count = 1
        self.multiball_active = False
        
        self.current_bonus = 0
        self.current_bonusx = 0

        self.summary_data = {
            "player": 1,
            "old_score": 0,
            "multiplier": 1,
            "bonus_points": 0
        }
        self._summary_end_time = 0.0
        self._highscore_end_time = 0.0 
        self._pending_highscore_check = None # Ebbe mentjük a végleges pontot

    def _get_multiplier(self, bonusx_index):
        if bonusx_index == 1: return 2
        if bonusx_index == 2: return 4
        if bonusx_index == 3: return 6
        if bonusx_index == 4: return 8
        return 1

    def handle_event(self, event: GameEvent):
        if event.kind == "SCORE_UPDATE":
            score, num_players, player, ball, bonus, bonusx = event.args
            self.players[player] = score
            self.active_player_count = num_players
            self.current_player = player
            self.current_ball = ball
            self.current_bonus = bonus
            self.current_bonusx = bonusx
            
            if self.state != AppState.SUMMARY and self.state != AppState.HIGHSCORE:
                self.state = AppState.SCORE

        elif event.kind == "NEXT" or event.kind == "GAMEOVER":
            mult = self._get_multiplier(self.current_bonusx)
            bonus_total = self.current_bonus * mult
            
            self.summary_data = {
                "player": self.current_player,
                "old_score": self.players[self.current_player],
                "multiplier": mult,
                "bonus_points": bonus_total
            }
            
            self.players[self.current_player] += bonus_total
            self._start_summary()
            
            if event.kind == "GAMEOVER":
                # 1. Elmentjük a pontot a későbbi csekkoláshoz
                self._pending_highscore_check = self.players[self.current_player]
                
                # 2. Rekord mentése
                self.score_manager.add_score("MRC", self.players[self.current_player])
                
                # 3. Csak EZUTÁN nullázzuk a változókat
                self.players = {1: 0, 2: 0, 3: 0, 4: 0}
                self.current_player = 1
                self.current_ball = 1
                self.active_player_count = 1
                self.current_bonus = 0
                self.current_bonusx = 0
            else:
                self._pending_highscore_check = None

        elif event.kind == "VIDEO":
            if self.state == AppState.SCORE:
                self.pending_video = event.args[0]
                self.state = AppState.VIDEO

        elif event.kind == "VIDEO_STOP":
            if self.state == AppState.VIDEO:
                self.mpv.stop()
                self.state = AppState.SCORE

    def _start_summary(self):
        self._summary_end_time = time.time() + self.SUMMARY_DURATION_SEC
        self.state = AppState.SUMMARY

    def tick(self):
        if self.state == AppState.VIDEO and self.mpv.is_finished():
            self.state = AppState.SCORE
            
        elif self.state == AppState.SUMMARY:
            if time.time() >= self._summary_end_time:
                # CSAK AKKOR nézzük a rekordot, ha ez egy GAMEOVER volt
                if self._pending_highscore_check is not None and self.score_manager.is_highscore(self._pending_highscore_check):
                    self._highscore_end_time = time.time() + 5.0
                    self.state = AppState.HIGHSCORE
                else:
                    self.state = AppState.SCORE
                
                # Töröljük a memóriából
                self._pending_highscore_check = None

        elif self.state == AppState.HIGHSCORE:
            if time.time() >= self._highscore_end_time:
                self.state = AppState.SCORE

    def consume_transition(self):
        if self.state != self._previous_state:
            transition = (self._previous_state, self.state)
            self._previous_state = self.state
            return transition
        return None