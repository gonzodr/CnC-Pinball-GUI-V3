"""A fő állapotgép: GUI, videó, minijáték és eredményállapotok váltása."""

import time
from collections import deque
from enum import Enum, auto
from protocol import GameEvent
from score_manager import ScoreManager
from name_entry import NameEntryController
from thanks_names_manager import ThanksNamesManager
from service_menu import ServiceMenuController
from minigame_settings import MinigameSettingsManager
from munchies_abduction import MunchiesAbductionGame
from video_catalog import resolve_serial_video_name

class AppState(Enum):
    SCORE = auto()
    SUMMARY = auto()
    NAME_ENTRY = auto()
    HIGHSCORE = auto()
    PRESS_START = auto()
    SPECIAL_THANKS = auto()
    FINAL_SCORES = auto()
    LOGO = auto()
    BEAT_SCORE = auto()
    SERVICE_MENU = auto()
    MINIGAME = auto()
    PNG_VIDEO = auto()

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
    # meg - jatek kozben (SUMMARY/NAME_ENTRY/FINAL_SCORES/PNG_VIDEO) nem, hogy
    # ne szakithassa felbe veletlenul egy elo kort.
    SERVICE_MENU_ALLOWED_STATES = (
        AppState.SCORE, AppState.LOGO, AppState.PRESS_START,
        AppState.SPECIAL_THANKS, AppState.HIGHSCORE, AppState.BEAT_SCORE,
    )

    MINIGAME_HEARTBEAT_SEC = 0.50
    MINIGAME_DONE_RETRY_SEC = 0.25
    MINIGAME_DONE_RETRY_WINDOW_SEC = 3.0

    def __init__(self, serial_reader=None):
        self.serial_reader = serial_reader  # csak a szerviz menu Serial Monitor kepernyojehez
        self.score_manager = ScoreManager()
        self.thanks_manager = ThanksNamesManager()
        self.minigame_settings = MinigameSettingsManager()
        self.state = AppState.SCORE
        self._previous_state = AppState.SCORE
        
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
            self.score_manager,
            self.thanks_manager,
            self.recent_events,
            self.serial_reader,
            minigame_settings=self.minigame_settings,
        )
        self.minigame = None
        self._preloaded_minigame = None
        self.last_minigame_result = None
        self._minigame_last_tick = 0.0
        self._minigame_session = None
        self._minigame_last_input_seq = None
        self._minigame_next_heartbeat = 0.0
        self._minigame_pending_done = None
        # A Pygame-alapu PNG sequence motor a kijelzo letrehozasa utan
        # kerul ide a main.py-bol (a Surface.convert() aktiv displayt ker).
        self.png_video_player = None

        # Indulaskor rogton az attract-loop fut (Press Play -> Special
        # Thanks -> Press Play -> Hiscore -> elolrol), amig Start ki nem
        # lepteti SCORE-ba - nem a SCORE kepernyovel indulunk.
        self._enter_attract_loop()

    def preload_minigame(self, progress_callback=None):
        """Build the dormant VUK game after the display is available."""
        if self._preloaded_minigame is not None or self.minigame is not None:
            return
        started = time.monotonic()
        self._preloaded_minigame = MunchiesAbductionGame(
            autoplay_intro=False,
            progress_callback=progress_callback,
            difficulty=self.minigame_settings.get_difficulty("munchies_abduction"),
            sound_hook=self._handle_minigame_sound,
        )
        print(
            f"[munchies] preloaded in {time.monotonic() - started:.2f}s; "
            "VUK trigger is armed"
        )

    def _get_multiplier(self, bonusx_index):
        if bonusx_index == 1: return 2
        if bonusx_index == 2: return 4
        if bonusx_index == 3: return 6
        if bonusx_index == 4: return 8
        return 1

    ANALOG_TEST_EVENTS = (
        "ANALOG_INFO", "ANALOG_VALUES", "ANALOG_THRESHOLDS",
        "ANALOG_SAVED", "ANALOG_ERROR", "ANALOG_STOPPED",
    )

    def handle_event(self, event: GameEvent):
        # Az analog teszt valaszai kizarolag a szerviz menue: ~5 Hz-en jonnek,
        # ezert nem naplozzuk oket a recent_events-be (elmosnak minden mast),
        # es nincs jatek-allapotra gyakorolt hatasuk sem.
        if event.kind in self.ANALOG_TEST_EVENTS:
            self.service_menu.handle_analog_event(event)
            return

        if event.kind not in ("SCORE_UPDATE", "VIDEO", "VIDEO_STOP"):
            # Kapcsolo-teszthez (szerviz menu / input_test) - minden "valodi
            # gomb" jellegu esemenyt naplozunk, a zajos SCORE_UPDATE/VIDEO-t nem.
            self.recent_events.append((time.time(), event.kind))

        if event.kind == "MUNCHIES_ACK":
            session = event.args[0] if event.args else None
            if self._minigame_pending_done is not None:
                pending_session = self._minigame_pending_done[0]
                if session == pending_session:
                    print(f"[munchies] firmware ACK, session={session}")
                    self._minigame_pending_done = None
            return

        if event.kind == "MUNCHIES_INPUT":
            if self.state != AppState.MINIGAME or self.minigame is None:
                return
            session, sequence, mask = event.args
            if session != self._minigame_session:
                return
            # Soros vonalon sorrendtartoak a csomagok. A modulo-16 bites
            # vizsgalat a sequence atfordulasat is helyesen kezeli.
            if self._minigame_last_input_seq is not None:
                delta = (sequence - self._minigame_last_input_seq) & 0xFFFF
                if delta == 0 or delta >= 0x8000:
                    return
            self._minigame_last_input_seq = sequence
            self.minigame.set_hardware_input(mask)
            return

        if event.kind == "MUNCHIES_ABORT":
            session = None
            reason = event.args[1] if len(event.args) > 1 else "UNKNOWN"
            if event.args:
                try:
                    session = int(event.args[0])
                except (TypeError, ValueError):
                    pass
            if (
                self.state == AppState.MINIGAME
                and (session is None or session == self._minigame_session)
            ):
                print(
                    f"[munchies] firmware megszakitas, "
                    f"session={session}, reason={reason}"
                )
                self._abort_minigame()
            return

        # A minijatek alatt a flipper/plunger hardveres el-es felengedes
        # esemenyei kozvetlenul a jatekmenethez tartoznak.
        if self.state == AppState.MINIGAME and self.minigame is not None and (
            "FLIPPER" in event.kind or "PLUNGER" in event.kind or event.kind == "PLAYER_PRESS"
        ):
            self.minigame.handle_event(event)
            return

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
                AppState.MINIGAME,
                AppState.PNG_VIDEO,
            ):
                self.state = AppState.SCORE
                self._in_attract_loop = False

        elif event.kind == "NEXT" or event.kind == "GAMEOVER":
            # A Tilt sequence introja egyszer fut, majd a LOOP_INFO szerinti
            # resz addig ismetlodik, amig a firmware a drain utan NEXT/END-et
            # nem kuld. Ugyanez a biztos leallitas mas, drain kozben meg futo
            # PNG-klipnel is helyes.
            if self.state == AppState.PNG_VIDEO and self.png_video_player is not None:
                self.png_video_player.stop()

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
                # 1. A GYOZTES (legmagasabb pontszamu) jatekos pontjat merjuk
                #    a hiscore-tablahoz - NEM az utolsokent befejezo jatekosét!
                #    (Tobbjatekos modban a game over mindig az utolso jatekos
                #    utolso golyojanal jon, es korabban az o pontja ment be.)
                #    A tenyleges mentes csak a nevbeiras utan tortenik meg
                #    (lasd tick() / NAME_ENTRY allapot).
                winner = max(
                    range(1, self.active_player_count + 1),
                    key=lambda p: self.players.get(p, 0),
                )
                self._pending_highscore_check = self.players[winner]
                self.pending_highscore_player = winner
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

        elif event.kind == "MUNCHIES_START":
            session = event.args[0] if event.args else None

            # A firmware a READY megjoveteleig ujrakuldi a START-ot. Egy mar
            # futo, azonos sessionre ezert csak megismetli a valaszt.
            if self.state == AppState.MINIGAME and self.minigame is not None:
                if session is not None and session == self._minigame_session:
                    self._send_minigame_line(f"MG_READY,{session}")
                    self._arm_minigame_heartbeat(session)
                return

            # Az UFO elsobbseget kap egy eppen futo klippel szemben: a golyo
            # mar fizikailag a VUK-ban all, nem varhat egy video vegere.
            if self.state in (AppState.SCORE, AppState.PNG_VIDEO):
                if self._preloaded_minigame is not None:
                    self.minigame = self._preloaded_minigame
                    self._preloaded_minigame = None
                    self.minigame.set_difficulty(
                        self.minigame_settings.get_difficulty("munchies_abduction")
                    )
                    self.minigame.activate()
                else:
                    # Fallback for an early trigger if boot preloading failed
                    # or was deliberately disabled.
                    self.minigame = MunchiesAbductionGame(
                        difficulty=self.minigame_settings.get_difficulty(
                            "munchies_abduction"
                        ),
                        sound_hook=self._handle_minigame_sound,
                    )
                self._minigame_session = session
                self._minigame_last_input_seq = None
                self._minigame_pending_done = None
                now = time.monotonic()
                # A READY utan rogton kuldunk egy ALIVE-ot is. Ez lezárja azt
                # az inditasi rest, amelyben egy lassabb elso render vagy USB
                # utemezes miatt a firmware meg az elso 500 ms-os heartbeat
                # elott watchdogot kezdhetne szamolni.
                self._minigame_next_heartbeat = now
                self._minigame_last_tick = now
                self._in_attract_loop = False
                self.state = AppState.MINIGAME
                if session is not None:
                    self._send_minigame_line(f"MG_READY,{session}")
                    self._service_minigame_protocol(now)
                    self._arm_minigame_heartbeat(session)
            elif session is not None:
                self._send_minigame_line(f"MG_BUSY,{session}")

        elif event.kind == "PNG_VIDEO_RANDOM":
            # Fejlesztoi PNG-videoag: nyugalmi SCORE-bol indulhat, egy mar
            # futo PNG-videot pedig ugyanazzal a triggerrel azonnal lecserel.
            # Mas allapot (summary, minijatek, menu) foglaltnak
            # szamit, ott a trigger szandekosan nem csinal semmit.
            if self.state in (AppState.SCORE, AppState.PNG_VIDEO) and self.png_video_player is not None:
                selected = self.png_video_player.start_random()
                if selected is not None:
                    self._in_attract_loop = False
                    self.state = AppState.PNG_VIDEO
                elif self.state == AppState.PNG_VIDEO:
                    # Ha egy hibas/mar eltunt sequence-re valtaskor nem tud
                    # elindulni az uj klip, ne maradjunk fekete videostate-ben.
                    self.state = AppState.SCORE

        elif event.kind == "VIDEO":
            requested_name = str(event.args[0])

            # Ufo10..13 = az UFO "pontlopas" nyeremenye: a firmware a
            # KIRABOLT jatekos pontjabol vont le 10000-et, de a score
            # uzenetben mindig csak az aktualis jatekos pontja jon -
            # itt szinkronizaljuk a kijelzett pontszamot is (0-nal nem
            # megy lejjebb, ugyanugy, ahogy a firmware-ben).
            if requested_name.casefold() in ("ufo10", "ufo11", "ufo12", "ufo13"):
                victim = int(requested_name[3:]) - 9  # Ufo10 -> 1 ... Ufo13 -> 4
                self.players[victim] = max(0, self.players[victim] - 10000)

            # Minden platform ugyanazt a Pygame PNG-sequence motort hasznalja.
            # Egy uj soros trigger a mar futo klipet is azonnal lecsereli.
            if self.state in (AppState.SCORE, AppState.PNG_VIDEO):
                if self.png_video_player is None:
                    print(f"[png-video] trigger kihagyva, a motor meg nincs kesz: {requested_name}")
                    return
                video_name = resolve_serial_video_name(
                    requested_name,
                    self.png_video_player.clip_names,
                )
                if video_name is None:
                    print(
                        f"[png-video] nincs sequence a soros parancshoz: "
                        f"{requested_name}"
                    )
                    return
                if self.png_video_player.start(video_name):
                    self._in_attract_loop = False
                    self.state = AppState.PNG_VIDEO

        elif event.kind == "VIDEO_STOP":
            if self.state == AppState.PNG_VIDEO and self.png_video_player is not None:
                self.png_video_player.stop()
                self.state = AppState.SCORE

    def _send_exit_to_firmware(self, variant: str):
        """A jatekveg-folyam legvegen szolunk a firmware-nek, hogy vege -
        az Arduino erre az A13-as vonalon ujrainditja magat, es friss
        attract modban var. E nelkul a gep ORORKRE a hiscore-modban
        (intmon == 2) ragadna minden jatek utan!"""
        if self.serial_reader is not None and hasattr(self.serial_reader, "send_raw"):
            self.serial_reader.send_raw(variant)

    def _send_minigame_line(self, text: str) -> bool:
        if self.serial_reader is None:
            return False
        if hasattr(self.serial_reader, "send_line"):
            return self.serial_reader.send_line(text)
        # Minimal fake/older SerialReader compatibility for tests and local
        # tools. A real recent reader always takes the send_line branch.
        if hasattr(self.serial_reader, "send_raw"):
            return self.serial_reader.send_raw(text + "\n")
        return False

    def _handle_minigame_sound(self, name: str):
        """Forward gameplay outcomes to the cabinet light controller."""
        session = self._minigame_session
        if session is None or self.state != AppState.MINIGAME:
            return
        if name == "collect":
            self._send_minigame_line(f"MG_PICKUP,{session},GOOD")
        elif name == "bad_pickup":
            self._send_minigame_line(f"MG_PICKUP,{session},BAD")
        elif name == "collection_complete":
            self._send_minigame_line(f"MG_COLLECTION,{session}")

    def _abort_minigame(self):
        if self.minigame is None:
            return
        self._disarm_minigame_heartbeat()
        aborted = self.minigame
        self.minigame = None
        aborted.prepare_for_replay()
        self._preloaded_minigame = aborted
        self._minigame_session = None
        self._minigame_last_input_seq = None
        self.state = AppState.SCORE

    def _arm_minigame_heartbeat(self, session):
        if self.serial_reader is not None and hasattr(
                self.serial_reader, "start_minigame_heartbeat"):
            self.serial_reader.start_minigame_heartbeat(session)

    def _disarm_minigame_heartbeat(self):
        if self.serial_reader is not None and hasattr(
                self.serial_reader, "stop_minigame_heartbeat"):
            self.serial_reader.stop_minigame_heartbeat()

    def _service_minigame_protocol(self, now: float):
        session = self._minigame_session
        if (
            session is not None
            and self.state == AppState.MINIGAME
            and now >= self._minigame_next_heartbeat
        ):
            self._send_minigame_line(f"MG_ALIVE,{session}")
            self._minigame_next_heartbeat = now + self.MINIGAME_HEARTBEAT_SEC

        pending = self._minigame_pending_done
        if pending is None:
            return
        done_session, bonus, deadline, next_send = pending
        if now >= deadline:
            print(f"[munchies] firmware ACK timeout, session={done_session}")
            self._minigame_pending_done = None
        elif now >= next_send:
            self._send_minigame_line(f"MG_DONE,{done_session},{bonus}")
            self._minigame_pending_done = (
                done_session, bonus, deadline,
                now + self.MINIGAME_DONE_RETRY_SEC,
            )

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
                self._send_exit_to_firmware("Exit")  # rekord nelkuli valtozat
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
        protocol_now = time.monotonic()
        if self.state == AppState.SERVICE_MENU:
            self.service_menu.tick(protocol_now)
        self._service_minigame_protocol(protocol_now)

        if self.state == AppState.MINIGAME and self.minigame is not None:
            now = protocol_now
            self.minigame.update(now - self._minigame_last_tick)
            self._minigame_last_tick = now
            if self.minigame.finished:
                result = self.minigame.result_dict()
                self.last_minigame_result = result
                bonus = result["total_bonus"]
                self.players[self.current_player] += bonus
                if self._minigame_session is not None:
                    session = self._minigame_session
                    deadline = now + self.MINIGAME_DONE_RETRY_WINDOW_SEC
                    self._minigame_pending_done = (session, bonus, deadline, now)
                    # Az elso DONE ne varjon a kovetkezo GUI frame-ig.
                    self._service_minigame_protocol(now)
                elif self.serial_reader is not None and hasattr(self.serial_reader, "send_raw"):
                    # Regi, session nelkuli firmware kompatibilitasa.
                    self.serial_reader.send_raw(f"MunchiesBonus,{bonus}")
                completed_game = self.minigame
                self.minigame = None
                self._disarm_minigame_heartbeat()
                # Reuse decoded sprites, portraits, fonts, UI and all voice/SFX
                # samples. Re-arming only creates a fresh lightweight road
                # streamer, so later VUK entries remain just as immediate.
                completed_game.prepare_for_replay()
                self._preloaded_minigame = completed_game
                self._minigame_session = None
                self._minigame_last_input_seq = None
                self.state = AppState.SCORE
            return

        if self.state == AppState.PNG_VIDEO and self.png_video_player is not None:
            self.png_video_player.update()
            if self.png_video_player.finished:
                self.state = AppState.SCORE
            return

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

        if self.state == AppState.SUMMARY:
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
                self._send_exit_to_firmware("Exit1")  # volt rekord -> hangos valtozat
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
