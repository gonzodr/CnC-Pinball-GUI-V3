"""A fő állapotgép: SCORE <-> VIDEO <-> SUMMARY <-> NAME_ENTRY <-> HIGHSCORE váltás."""

import time
from collections import deque
from enum import Enum, auto
from protocol import GameEvent
from mpv_controller import MpvController
from score_manager import ScoreManager
from name_entry import NameEntryController
from thanks_names_manager import ThanksNamesManager
from service_menu import ServiceMenuController

class AppState(Enum):
    SCORE = auto()
    VIDEO = auto()
    SUMMARY = auto()
    NAME_ENTRY = auto()
    HIGHSCORE = auto()
    PRESS_START = auto()
    SPECIAL_THANKS = auto()
    FINAL_SCORES = auto()
    LOGO = auto()
    BEAT_SCORE = auto()
    SERVICE_MENU = auto()

class StateMachine:
    SUMMARY_DURATION_SEC = 8.0  # buvos 8 mp, mint a tobbi attract-kepernyonel - a reveal 4.6s-nal kesz, utana meg ~3.4s allva marad
    FINAL_SCORES_DURATION_SEC = 8.0  # ugyanaz a buvos 8 mp

    # Attract-mode loop: Logo -> Press Play -> Special Thanks -> Press Play
    # -> Hiscore -> Press Play -> Beat This Score -> elolrol. Barmely
    # pontjan a Start kilepteti SCORE-ba. Az idozitesek finomithatok, ha
    # eles kepen nem stimmelnek.
    ATTRACT_SEQUENCE = [
        (AppState.LOGO, 8.0),
        (AppState.PRESS_START, 8.0),
        (AppState.SPECIAL_THANKS, 8.0),
        (AppState.PRESS_START, 8.0),
        (AppState.HIGHSCORE, 8.0),
        (AppState.PRESS_START, 8.0),
        (AppState.BEAT_SCORE, 8.0),
    ]
    # Game over utan (nem a bootnal es nem a kezi ATTRACT triggernel) a
    # loop ne a Logoval, hanem az elso Press Play-jel kezdodjon ujra.
    ATTRACT_INDEX_AFTER_GAMEOVER = next(
        i for i, (state, _) in enumerate(ATTRACT_SEQUENCE) if state == AppState.PRESS_START
    )

    # A titkos szerviz menu (Ctrl+M) csak ezekbol az allapotokbol nyithato
    # meg - jatek kozben (SUMMARY/NAME_ENTRY/FINAL_SCORES/VIDEO) nem, hogy
    # ne szakithassa felbe veletlenul egy elo kort.
    SERVICE_MENU_ALLOWED_STATES = (
        AppState.SCORE, AppState.LOGO, AppState.PRESS_START,
        AppState.SPECIAL_THANKS, AppState.HIGHSCORE, AppState.BEAT_SCORE,
    )

    # A Unity-korszakos video-hozzarendeles ket elcsuszott UFO-parancsa
    # (reszletek: firmware repo, VIDEO_MAP.md). A tobbi triggernel a
    # video fajlneve megegyezik a parancs nevevel.
    VIDEO_NAME_REMAP = {"Ufo6": "Ufofuck", "Ufo7": "Ufo6"}

    # VIDEO watchdog: ennel tovabb egyetlen video sem tarthat - ha megis
    # (beragadt mpv/IPC), kenyszerrel visszaterunk a SCORE kepernyore.
    VIDEO_MAX_DURATION_SEC = 45.0

    def __init__(self, mpv: MpvController, serial_reader=None):
        self.mpv = mpv
        self.serial_reader = serial_reader  # csak a szerviz menu Serial Monitor kepernyojehez
        self.score_manager = ScoreManager()
        self.thanks_manager = ThanksNamesManager()
        self.state = AppState.SCORE
        self._previous_state = AppState.SCORE
        self.pending_video = None
        self._video_started_at = 0.0  # a VIDEO watchdoghoz
        
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
        self._pending_highscore_check = None  # Ebbe mentjük a végleges pontot
        self._pending_game_over = False  # True, ha ez a SUMMARY valodi GAMEOVER-bol jott (NEXT-nel False)
        self.pending_highscore_player = 1    # melyik player ért GAMEOVER-t
        self.name_entry = NameEntryController()

        # Tobb-jatekos vegeredmeny kepernyohoz (FINAL_SCORES) - a players
        # dict pillanatkepe GAMEOVER-kor, MIELOTT nullazodik.
        self.final_scores = {1: 0, 2: 0, 3: 0, 4: 0}
        self.final_player_count = 1
        self._final_scores_end_time = 0.0

        self._in_attract_loop = False
        self._attract_index = 0
        self._attract_state_end_time = 0.0

        # Az utobbi (nem SCORE_UPDATE/VIDEO) esemenyek naploja a szerviz
        # menu input-teszt kepernyojehez (kapcsolo-teszt).
        self.recent_events = deque(maxlen=12)
        self.service_menu = ServiceMenuController(
            self.score_manager, self.thanks_manager, self.recent_events, self.serial_reader
        )

        # Indulaskor rogton az attract-loop fut (Press Play -> Special
        # Thanks -> Press Play -> Hiscore -> elolrol), amig Start ki nem
        # lepteti SCORE-ba - nem a SCORE kepernyovel indulunk.
        self._enter_attract_loop()

    def _get_multiplier(self, bonusx_index):
        if bonusx_index == 1: return 2
        if bonusx_index == 2: return 4
        if bonusx_index == 3: return 6
        if bonusx_index == 4: return 8
        return 1

    def handle_event(self, event: GameEvent):
        if event.kind not in ("SCORE_UPDATE", "VIDEO", "VIDEO_STOP"):
            # Kapcsolo-teszthez (szerviz menu / input_test) - minden "valodi
            # gomb" jellegu esemenyt naplozunk, a zajos SCORE_UPDATE/VIDEO-t nem.
            self.recent_events.append((time.time(), event.kind))

        if event.kind == "SCORE_UPDATE":
            score, num_players, player, ball, bonus, bonusx = event.args
            self.players[player] = score
            self.active_player_count = num_players
            self.current_player = player
            self.current_ball = ball
            self.current_bonus = bonus
            self.current_bonusx = bonusx
            
            if self.state not in (
                AppState.SUMMARY, AppState.HIGHSCORE, AppState.NAME_ENTRY,
                AppState.PRESS_START, AppState.SPECIAL_THANKS, AppState.LOGO,
                AppState.BEAT_SCORE, AppState.SERVICE_MENU,
            ):
                self.state = AppState.SCORE
                self._in_attract_loop = False

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
                # 1. Elmentjük a pontot és a játékost a NAME_ENTRY-hez -
                #    a tényleges mentés csak a névbeírás után történik meg
                #    (lásd tick() / NAME_ENTRY állapot).
                self._pending_highscore_check = self.players[self.current_player]
                self.pending_highscore_player = self.current_player
                self._pending_game_over = True

                # 1b. Pillanatkep mindenki vegso allasarol a FINAL_SCORES
                #     kepernyohoz, MIELOTT a players dict nullazodik.
                self.final_scores = dict(self.players)
                self.final_player_count = self.active_player_count

                # 2. Csak EZUTÁN nullázzuk a változókat
                self.players = {1: 0, 2: 0, 3: 0, 4: 0}
                self.current_player = 1
                self.current_ball = 1
                self.active_player_count = 1
                self.current_bonus = 0
                self.current_bonusx = 0
            else:
                self._pending_highscore_check = None
                self._pending_game_over = False

        elif event.kind == "FLIPPER_LEFT":
            if self.state == AppState.NAME_ENTRY:
                self.name_entry.prev_char()

        elif event.kind == "FLIPPER_RIGHT":
            if self.state == AppState.NAME_ENTRY:
                self.name_entry.next_char()

        elif event.kind == "PLAYER_PRESS":
            if self.state == AppState.NAME_ENTRY:
                self.name_entry.confirm()

        elif event.kind == "START":
            if self.state == AppState.NAME_ENTRY:
                self.name_entry.skip()
            elif self.state in (AppState.PRESS_START, AppState.SPECIAL_THANKS, AppState.LOGO, AppState.BEAT_SCORE) or \
                    (self._in_attract_loop and self.state == AppState.HIGHSCORE):
                # Barmely attract-kepernyorol (akar a teljes loopban, akar
                # egy-egy kepernyo onallo dev-tesztelesekor) a Start
                # kilepteti a jatekost a SCORE kepernyore.
                self._in_attract_loop = False
                self.state = AppState.SCORE

        elif event.kind == "ATTRACT":
            # Elinditja a teljes attract-loopot: Press Play -> Special
            # Thanks -> Press Play -> Hiscore -> elolrol, amig Start ki
            # nem lepteti (lasd fent).
            if self.state == AppState.SCORE:
                self._enter_attract_loop()

        elif event.kind == "ESCAPE_TO_ATTRACT":
            # Globalis "vissza az attract-loopba" gyorsgomb (Esc): barmikor
            # hasznalhato, amikor NEM mar az attract-loop fut (pl. dev
            # elonezeti kepernyon, jatek utani Hiscore-on, stb.). Ha mar
            # loopban vagyunk, nem csinal semmit.
            if not self._in_attract_loop:
                self._enter_attract_loop()

        elif event.kind == "SERVICE_MENU_ENTER":
            # Titkos szerviz menu (Ctrl+M) - csak nyugalmi/attract
            # allapotokbol nyithato, jatek kozben nem.
            if self.state in self.SERVICE_MENU_ALLOWED_STATES:
                self._in_attract_loop = False
                self.service_menu.reset()
                self.state = AppState.SERVICE_MENU

        elif event.kind == "DEV_THX":
            # IDEIGLENES teszt-esemeny: csak a Special Thanks kepernyo
            # onallo, loopon kivuli elonezetehez (gyors vizualis check).
            if self.state == AppState.SCORE:
                self.state = AppState.SPECIAL_THANKS

        elif event.kind == "DEV_LOGO":
            # IDEIGLENES teszt-esemeny: csak a Logo kepernyo onallo,
            # loopon kivuli elonezetehez (gyors vizualis check). Meg
            # nincs bekotve az attract-loopba.
            if self.state == AppState.SCORE:
                self.state = AppState.LOGO

        elif event.kind == "DEV_BEAT_SCORE":
            # IDEIGLENES teszt-esemeny: csak a Beat This Score kepernyo
            # onallo, loopon kivuli elonezetehez. Meg nincs bekotve az
            # attract-loopba.
            if self.state == AppState.SCORE:
                self.state = AppState.BEAT_SCORE

        elif event.kind == "VIDEO":
            video_name = event.args[0]

            # Ufo10..13 = az UFO "pontlopas" nyeremenye: a firmware a
            # KIRABOLT jatekos pontjabol vont le 10000-et, de a score
            # uzenetben mindig csak az aktualis jatekos pontja jon -
            # itt szinkronizaljuk a kijelzett pontszamot is (0-nal nem
            # megy lejjebb, ugyanugy, ahogy a firmware-ben).
            if video_name in ("Ufo10", "Ufo11", "Ufo12", "Ufo13"):
                victim = int(video_name[3:]) - 9  # Ufo10 -> 1 ... Ufo13 -> 4
                self.players[victim] = max(0, self.players[victim] - 10000)

            # A Unity-korszakbol orokolt elcsuszas (lasd a firmware repo
            # VIDEO_MAP.md-jet): a "Ufo6" trigger a Ufofuck.mp4-et, a
            # "Ufo7" pedig az Ufo6.mp4-et jelenti - Ufo7.mp4 nem letezik!
            video_name = self.VIDEO_NAME_REMAP.get(video_name, video_name)

            if self.state == AppState.SCORE:
                self.pending_video = video_name
                self._video_started_at = time.time()  # a VIDEO watchdoghoz
                self.state = AppState.VIDEO

        elif event.kind == "VIDEO_STOP":
            if self.state == AppState.VIDEO:
                self.mpv.stop()
                self.state = AppState.SCORE

    def _start_summary(self):
        self._summary_end_time = time.time() + self.SUMMARY_DURATION_SEC
        self.state = AppState.SUMMARY

    def _resolve_after_summary(self):
        """A SUMMARY (es tobb-jatekos eseten a rautan kovetkezo
        FINAL_SCORES) vege utan donti el, hova lepjunk: CSAK AKKOR
        nezzuk a rekordot, ha ez egy GAMEOVER volt."""
        if self._pending_highscore_check is not None and self.score_manager.is_highscore(self._pending_highscore_check):
            self.name_entry.reset()
            self.state = AppState.NAME_ENTRY
            # _pending_highscore_check-et NEM töröljük - kell még
            # a NAME_ENTRY végén a tényleges mentéshez.
        else:
            self._pending_highscore_check = None
            if self._pending_game_over:
                # Valodi jatekveg volt, csak nem lett rekord - nincs
                # NAME_ENTRY/HIGHSCORE kiterulo, vissza az attract-loopba
                # (Press Play-tol, nem a Logotol), nem a SCORE kepernyore.
                self._pending_game_over = False
                self._enter_attract_loop(self.ATTRACT_INDEX_AFTER_GAMEOVER)
            else:
                # Ez csak egy NEXT (labdavaltas) volt, a jatek folytatodik
                self.state = AppState.SCORE

    def _enter_attract_loop(self, start_index=0):
        self._in_attract_loop = True
        self._attract_index = start_index
        self._goto_attract_step()

    def _advance_attract_loop(self):
        self._attract_index = (self._attract_index + 1) % len(self.ATTRACT_SEQUENCE)
        self._goto_attract_step()

    def _goto_attract_step(self):
        state, duration = self.ATTRACT_SEQUENCE[self._attract_index]
        self.state = state
        self._attract_state_end_time = time.time() + duration

    def tick(self):
        # Attract-loop lepteto: barmelyik loop-kepernyon (LOGO, PRESS_START,
        # SPECIAL_THANKS, HIGHSCORE, BEAT_SCORE) ez donti el, mikor kell
        # tovabblepni a kovetkezo elemre - MEGELOZI az egyes allapotok sajat
        # (nem-loop) tick-logikajat, hogy a ket eset (attract HIGHSCORE vs.
        # jatek utani HIGHSCORE) ne zavarja egymast.
        if self._in_attract_loop and self.state in (
            AppState.LOGO, AppState.PRESS_START, AppState.SPECIAL_THANKS,
            AppState.HIGHSCORE, AppState.BEAT_SCORE,
        ):
            if time.time() >= self._attract_state_end_time:
                self._advance_attract_loop()
            return

        if self.state == AppState.VIDEO:
            # Vedohalo: ha az mpv/IPC barmiert beragad (halott socket,
            # kijelzo-problema), ne ragadjunk orokre a VIDEO allapotban.
            timed_out = time.time() - self._video_started_at > self.VIDEO_MAX_DURATION_SEC
            if timed_out:
                print("[state] VIDEO watchdog: tul regota fut, kenyszer-stop")
                self.mpv.stop()
            if timed_out or self.mpv.is_finished():
                self.state = AppState.SCORE
            
        elif self.state == AppState.SUMMARY:
            if time.time() >= self._summary_end_time:
                if self._pending_game_over and self.final_player_count > 1:
                    # Tobb jatekos jatszott, es ez valodi jatekveg volt -
                    # eloszor mindenki vegso allasat mutatjuk (FINAL_SCORES),
                    # csak utana jon a hiscore-check.
                    self._final_scores_end_time = time.time() + self.FINAL_SCORES_DURATION_SEC
                    self.state = AppState.FINAL_SCORES
                else:
                    self._resolve_after_summary()

        elif self.state == AppState.FINAL_SCORES:
            if time.time() >= self._final_scores_end_time:
                self._resolve_after_summary()

        elif self.state == AppState.NAME_ENTRY:
            if self.name_entry.done:
                self.score_manager.add_score(self.name_entry.get_name(), self._pending_highscore_check)
                self._pending_highscore_check = None
                self._highscore_end_time = time.time() + 5.0
                self.state = AppState.HIGHSCORE

        elif self.state == AppState.HIGHSCORE:
            if time.time() >= self._highscore_end_time:
                # Jatek utani hiscore-megjelenites vege - vissza az
                # attract-loopba (Press Play-tol, nem Logotol, nem SCORE-ba),
                # amig ujra Start nem jon.
                self._pending_game_over = False
                self._enter_attract_loop(self.ATTRACT_INDEX_AFTER_GAMEOVER)

        elif self.state == AppState.SERVICE_MENU:
            if self.service_menu.should_exit:
                # A szerviz menubol mindig az attract-loopba terunk vissza
                # (nem a SCORE kepernyore) - real pinball gepeken is igy
                # mukodik a szerviz menu utan.
                self._enter_attract_loop()

    def consume_transition(self):
        if self.state != self._previous_state:
            transition = (self._previous_state, self.state)
            self._previous_state = self.state
            return transition
        return None