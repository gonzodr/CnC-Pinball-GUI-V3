"""A fő állapotgép: SCORE <-> VIDEO váltás, és a játékállás nyilvántartása."""

from enum import Enum, auto
from protocol import GameEvent
from mpv_controller import MpvController


class AppState(Enum):
    SCORE = auto()   # alap állapot: pontszám-GUI látható
    VIDEO = auto()   # videólejátszás aktív, GUI elrejtve


class StateMachine:
    def __init__(self, mpv: MpvController):
        self.mpv = mpv
        self.state = AppState.SCORE
        self._previous_state = AppState.SCORE  # váltás detektálásához

        # GUI-nak releváns adat, amit a Score renderer fog kirajzolni
        self.players = {1: 0, 2: 0, 3: 0, 4: 0}
        self.current_player = 1
        self.current_ball = 1
        self.multiball_active = False

        # Hány jatekos-kartya legyen lathato (1-4). A fizikai player
        # selector gomb minden megnyomasra +1-et lep, 4 utan visszaall
        # 1-re. Ez FUGGETLEN a current_player-tol: az dontia el, MELYIK
        # kartya van kiemelve, ez pedig, HANY kartya latszik egyaltalan.
        self.active_player_count = 1
        self._previous_player_count = 1  # animacio-trigger detektalashoz

    def handle_event(self, event: GameEvent):
        """Egy beérkezett Teensy parancs hatására frissíti az állapotot."""

        if event.kind == "SCORE":
            player, score = event.args
            self.players[player] = score

        elif event.kind == "PLAYER":
            self.current_player = event.args[0]

        elif event.kind == "BALL":
            self.current_ball = event.args[0]

        elif event.kind == "VIDEO":
            video_name = event.args[0]
            self.mpv.play(video_name)
            self.state = AppState.VIDEO

        elif event.kind == "VIDEO_STOP":
            self.mpv.stop()
            self.state = AppState.SCORE

        elif event.kind == "MULTIBALL_ON":
            self.multiball_active = True

        elif event.kind == "MULTIBALL_OFF":
            self.multiball_active = False

        elif event.kind == "GAMEOVER":
            self.state = AppState.SCORE
            # ide jöhet majd "game over" felirat/animáció a GUI-ban

        elif event.kind == "ATTRACT":
            self.state = AppState.SCORE
            # ide jöhet majd attract-mode logika (demo pontszámok, animáció)

        elif event.kind == "PLAYERCOUNT_NEXT":
            # ciklikus lepes: 1 -> 2 -> 3 -> 4 -> 1 -> ...
            self.active_player_count = (self.active_player_count % 4) + 1

    def tick(self):
        """
        Minden frame-ben meghívva: ellenőrzi, hogy a videó véget ért-e.
        Ha igen, automatikusan visszavált SCORE állapotba — anélkül,
        hogy a Teensy-nek explicit VIDEO,STOP-ot kellene küldenie.
        """
        if self.state == AppState.VIDEO and self.mpv.is_finished():
            self.state = AppState.SCORE

    def consume_transition(self):
        """
        Visszaadja, hogy történt-e állapotváltás az előző hívás óta,
        és milyen irányban. A fő loop ezt hívja minden frame-ben,
        és csak akkor reagál (acquire/release display), ha van változás.
        """
        if self.state != self._previous_state:
            transition = (self._previous_state, self.state)
            self._previous_state = self.state
            return transition
        return None

    def consume_player_count_change(self):
        """
        Visszaadja (régi, új) párost, ha az active_player_count
        megváltozott az előző hívás óta, különben None.

        A GUI ezt hívja minden frame-ben, és ha van változás, elindítja
        a megfelelő be-/kicsúszó animációt a kártyákon.
        """
        if self.active_player_count != self._previous_player_count:
            change = (self._previous_player_count, self.active_player_count)
            self._previous_player_count = self.active_player_count
            return change
        return None