"""Titkos szerviz menu: Ctrl+M-mel erheto el, VALODI (a Pi-hez
csatlakoztatott) billentyuzettel navigalhato - nem a jatek GameEvent-
rendszeret hasznalja (mint a mock_input.py), hanem kozvetlenul a nyers
pygame billentyu-eventeket dolgozza fel, amig aktiv (lasd main.py).

Kepernyok (self.screen):
- "main": fomenu
- "hiscore_edit" / "hiscore_delete_confirm": hiscore bejegyzesek torlese
- "thanks_edit" / "thanks_add_input": Special Thanks nevlista szerkesztese
- "input_test": az utobbi soros/mock esemenyek naploja (kapcsolo-teszt)
- "serial_monitor": a soros porton beerkezo NYERS sorok (parse-olatlanul is)
- "reset_confirm": osszes hiscore torlese az F1-es szerkesztobol
- "minigame_difficulty": minijatekonkenti hetfokozatu nehezseg
- "version_info": szoftver/verzio info
"""

import os
import re
import subprocess
import sys
import time

import pygame

import arduino_port

# A firmware effekt-tablaja innen olvasva (id + nev) - a Pi-n itt van a
# firmware repo, ebbol flashel az F7 is, tehat ez az igazsag forrasa.
LIGHT_EFFECTS_HEADER = os.path.expanduser("~/CnC_firmware4/effect_data.h")


class ServiceMenuController:
    MAIN_ITEMS = [
        ("hiscore_edit", "F1 - Hiscore szerkesztes / torles"),
        ("thanks_edit", "F2 - Special Thanks nevek"),
        ("diagnostics", "F3 - Diagnosztika (teszt-kepernyok)"),
        ("particle_editor", "F4 - Particle szerkeszto"),
        ("find_arduino", "F5 - Arduino keresese"),
        ("firmware_update", "F6 - Firmware update"),
        ("minigame_difficulty", "F7 - Minigame difficulty"),
        ("version_info", "F8 - Verzio info"),
        ("light_editor", "F9 - Light editor (fenyeffekt szerkeszto)"),
        ("exit", "F10 - Kilepes"),
    ]

    # A negy teszt-kepernyo egy helyen. Mindegyik "nezd meg, mit csinal a
    # gep" jellegu, ezert kerultek ossze - a fomenu igy rovidebb, es marad
    # hely uj menupontoknak.
    DIAGNOSTIC_ITEMS = [
        ("input_test", "Input / gomb teszt (feldolgozott esemenyek)"),
        ("serial_monitor", "Serial Monitor (nyers sorok)"),
        ("light_test", "Light test (fenyeffektek)"),
        ("analog_test", "Analog bemenet-teszt (infra szenzorok)"),
    ]

    # F-billentyu -> fomenu menupont, sorrendben (F1 = elso menupont...).
    # VAK hasznalatra: monitor nelkul (pl. powerbankos pince-frissiteskor)
    # eleg egyetlen F-gombot megnyomni - a main.py barmely nyugalmi
    # allapotbol megnyitja a szerviz menut ES vegrehajtja a menupontot.
    FKEYS = [
        pygame.K_F1, pygame.K_F2, pygame.K_F3, pygame.K_F4, pygame.K_F5,
        pygame.K_F6, pygame.K_F7, pygame.K_F8, pygame.K_F9, pygame.K_F10,
        pygame.K_F11, pygame.K_F12,
    ]

    def __init__(self, score_manager, thanks_manager, recent_events,
                 serial_reader=None, particle_settings=None,
                 minigame_settings=None):
        self.score_manager = score_manager
        self.thanks_manager = thanks_manager
        self.recent_events = recent_events  # deque, csak olvassuk (input_test kepernyohoz)
        self.serial_reader = serial_reader  # csak olvassuk (serial_monitor kepernyohoz)
        self.particle_settings = particle_settings  # ParticleSettingsManager (particle_editor kepernyohoz)
        self.minigame_settings = minigame_settings

        self.should_exit = False
        # main.py figyeli ezt a flaget - ha True, elengedi a kijelzot/soros
        # portot, elinditja a firmware_update.py-t kulon programkent, es
        # amikor az visszater, ujra magahoz veszi oket (lasd main.py).
        self.should_launch_firmware_update = False
        # Ugyanaz a minta, mint a firmware update-nel: main.py figyeli, es
        # ha True, elengedi a kijelzot/soros portot, elinditja a kulon
        # CnC Light Editor pygame-appot, majd visszaveszi oket.
        self.should_launch_light_editor = False
        self.screen = "main"
        self.cursor = 0
        self.status_message = ""

        self._text_input_buffer = ""
        self._pending_delete_index = None

        # Light test kepernyo: (id, nev) parok az effect_data.h-bol.
        self.light_effects = []

        # Analog teszt kepernyo. A nevek/kuszobok a firmware-tol jonnek
        # (AT_INFO / AT_THR), az ertekek ~5 Hz-en frissulnek (AT_VAL) -
        # igy uj szenzor felvetelehez itt nem kell hozzanyulni semmihez.
        self.analog_names = []
        self.analog_values = []
        self.analog_thresholds = []
        self.analog_thresholds_saved = []
        self.analog_dirty = False
        self.analog_save_pending = False
        self.analog_save_snapshot = None
        self.analog_last_update = 0.0
        self.analog_streaming = False

    def reset(self):
        """Minden belepeskor (Ctrl+M) a fomenurol indulunk ujra."""
        self._leave_analog_test_if_needed()
        self.should_exit = False
        self.screen = "main"
        self.cursor = 0
        self.status_message = ""
        self._text_input_buffer = ""
        self._pending_delete_index = None

    def _go_main(self):
        self.screen = "main"
        self.cursor = 0

    # --- esemeny feldolgozas ---

    @classmethod
    def fkey_in_events(cls, pygame_events):
        """Az elso F1..F10 KEYDOWN a listaban, vagy None (main.py hasznalja
        a menun KIVULI, globalis F-gomb figyeleshez)."""
        for event in pygame_events:
            if event.type == pygame.KEYDOWN and event.key in cls.FKEYS:
                return event.key
        return None

    def handle_fkey(self, key):
        """F-billentyu: kurzor a menupontra + azonnali vegrehajtas.
        Barmelyik al-kepernyorol is hivjak, a fomenurol indul ujra."""
        idx = self.FKEYS.index(key)
        if idx >= len(self.MAIN_ITEMS):
            return
        # Ha a light-test kepernyorol lepunk ki egy F-gombbal, allitsuk le
        # a firmware loopjat (kulonben a gepen tovabb menne az effekt).
        self._leave_light_test_if_needed()
        self._leave_analog_test_if_needed()
        self.screen = "main"
        self.cursor = idx
        self._activate_main_item()

    def _activate_main_item(self):
        """A kurzoron allo fomenu-pont vegrehajtasa (Enter es F-gomb kozos utja)."""
        target, _ = self.MAIN_ITEMS[self.cursor]
        if target == "exit":
            self.should_exit = True
        elif target == "firmware_update":
            # Nem valt sub-screen-re - main.py meg ebben a korben eszreveszi
            # a flaget es atadja a vezerlest a kulon firmware_update.py-nak.
            self.should_launch_firmware_update = True
        elif target == "light_editor":
            # Ugyanaz a mechanizmus, mint a firmware update-nel: main.py
            # inditja el a kulon szerkeszto-appot es adja vissza a kijelzot.
            self.should_launch_light_editor = True
        elif target == "find_arduino":
            self._handle_find_arduino()
        else:
            self.screen = target
            self.cursor = 0

    def handle_pygame_events(self, pygame_events):
        for event in pygame_events:
            if event.type != pygame.KEYDOWN:
                continue
            self.status_message = ""
            # F-gombok a menu BARMELY kepernyojerol mukodnek
            if event.key in self.FKEYS:
                self.handle_fkey(event.key)
                continue
            handler = getattr(self, f"_handle_{self.screen}", None)
            if handler:
                handler(event)

    def _handle_main(self, event):
        if event.key == pygame.K_UP:
            self.cursor = (self.cursor - 1) % len(self.MAIN_ITEMS)
        elif event.key == pygame.K_DOWN:
            self.cursor = (self.cursor + 1) % len(self.MAIN_ITEMS)
        elif event.key == pygame.K_RETURN:
            self._activate_main_item()
        elif event.key == pygame.K_ESCAPE:
            self.should_exit = True

    def _handle_diagnostics(self, event):
        count = len(self.DIAGNOSTIC_ITEMS)
        if event.key == pygame.K_ESCAPE:
            self._go_main()
        elif event.key == pygame.K_UP:
            self.cursor = (self.cursor - 1) % count
        elif event.key == pygame.K_DOWN:
            self.cursor = (self.cursor + 1) % count
        elif event.key == pygame.K_RETURN:
            self._activate_diagnostic_item()

    def _activate_diagnostic_item(self):
        target, _ = self.DIAGNOSTIC_ITEMS[self.cursor]
        if target == "light_test":
            self._enter_light_test()
        elif target == "analog_test":
            self._enter_analog_test()
        else:
            self.screen = target
            self.cursor = 0

    def _go_diagnostics(self):
        """Vissza az almenube (nem a fomenube) - a teszt-kepernyokrol ESC."""
        self.screen = "diagnostics"
        self.cursor = 0

    # --- Analog bemenet-teszt -------------------------------------------
    # A firmware csak attractban engedi (AT_ERR,BUSY kulonben), es magatol
    # leall, ha a gep jatekba lep - lasd h_analog_test.ino.

    def _enter_analog_test(self):
        self.screen = "analog_test"
        self.cursor = 0
        self.analog_names = []
        self.analog_values = []
        self.analog_thresholds = []
        self.analog_thresholds_saved = []
        self.analog_dirty = False
        self.analog_save_pending = False
        self.analog_save_snapshot = None
        self.analog_last_update = 0.0
        self.analog_streaming = False
        if self.serial_reader is None:
            self.status_message = "Nincs soros kapcsolat"
            return
        self._start_analog_stream()

    def _start_analog_stream(self):
        if self.serial_reader is None:
            self.status_message = "Nincs soros kapcsolat"
            self.analog_streaming = False
            return
        self.analog_streaming = self.serial_reader.send_raw("AT,START\n")
        self.status_message = (
            "Kapcsolodas a firmware-hez..."
            if self.analog_streaming
            else "A parancs nem ment ki - R: ujraprobalas"
        )

    def _leave_analog_test_if_needed(self):
        if self.screen == "analog_test" and self.analog_streaming:
            if self.serial_reader is not None:
                self.serial_reader.send_raw("AT,STOP\n")
            self.analog_streaming = False

    def handle_analog_event(self, event):
        """A firmware AT_* valaszai (a state_machine iranyitja ide)."""
        kind = event.kind
        if kind == "ANALOG_INFO":
            self.analog_names = list(event.args[0])
            self.status_message = f"{len(self.analog_names)} szenzor"
        elif kind == "ANALOG_VALUES":
            self.analog_values = list(event.args[0])
            self.analog_last_update = time.time()
        elif kind == "ANALOG_THRESHOLDS":
            self.analog_thresholds = list(event.args[0])
            self.analog_thresholds_saved = list(self.analog_thresholds)
            self.analog_dirty = False
            self.analog_save_pending = False
        elif kind == "ANALOG_SAVED":
            saved = self.analog_save_snapshot or self.analog_thresholds
            self.analog_thresholds_saved = list(saved)
            self.analog_dirty = self.analog_thresholds != self.analog_thresholds_saved
            self.analog_save_pending = False
            self.analog_save_snapshot = None
            self.status_message = "Minden kuszob elmentve az EEPROM-ba"
        elif kind == "ANALOG_ERROR":
            self.analog_save_pending = False
            self.analog_save_snapshot = None
            reason = event.args[0] if event.args else "?"
            if reason == "BUSY":
                self.status_message = "A gep nem attractban van - eloszor fejezd be a jatekot"
            elif reason == "RANGE":
                self.status_message = "Ervenytelen ertek (0-1023)"
            else:
                self.status_message = f"Firmware hiba: {reason}"
        elif kind == "ANALOG_STOPPED":
            self.analog_streaming = False

    def _adjust_analog_threshold(self, delta):
        if not self.analog_thresholds:
            return
        idx = self.cursor
        if not 0 <= idx < len(self.analog_thresholds):
            return
        new_value = max(0, min(1023, self.analog_thresholds[idx] + delta))
        if new_value == self.analog_thresholds[idx]:
            return
        self.analog_thresholds[idx] = new_value
        self.analog_dirty = self.analog_thresholds != self.analog_thresholds_saved
        self.status_message = "Nincs mentve - S: osszes kuszob mentese"

    def _save_analog_thresholds(self):
        """Az osszes kuszobot egyetlen, atomi firmware-paranccsal menti.

        A nyilak csak a GUI helyi masolatat allitjak. Igy nincs billentyunkent
        EEPROM-iras, es kesve visszaerkezo nyugtazas sem tudja felulirni az
        eppen szerkesztett erteket.
        """
        if not self.analog_thresholds or self.serial_reader is None:
            self.status_message = "Nincs mentheto adat vagy soros kapcsolat"
            return
        if self.analog_save_pending:
            self.status_message = "A mentes mar folyamatban van"
            return
        payload = ",".join(str(value) for value in self.analog_thresholds)
        if self.serial_reader.send_raw(f"AT,SAVE,{payload}\n"):
            self.analog_save_pending = True
            self.analog_save_snapshot = list(self.analog_thresholds)
            self.status_message = "Mentes..."
        else:
            self.status_message = "Mentes sikertelen - nincs soros kapcsolat"

    def _handle_analog_test(self, event):
        count = max(len(self.analog_names), len(self.analog_thresholds))
        if event.key == pygame.K_ESCAPE:
            self._leave_analog_test_if_needed()
            self._go_diagnostics()
        elif event.key == pygame.K_r:
            self._start_analog_stream()
        elif not count:
            return
        elif event.key == pygame.K_UP:
            self.cursor = (self.cursor - 1) % count
        elif event.key == pygame.K_DOWN:
            self.cursor = (self.cursor + 1) % count
        elif event.key == pygame.K_LEFT:
            self._adjust_analog_threshold(-1)
        elif event.key == pygame.K_RIGHT:
            self._adjust_analog_threshold(1)
        elif event.key == pygame.K_PAGEDOWN:
            self._adjust_analog_threshold(-10)
        elif event.key == pygame.K_PAGEUP:
            self._adjust_analog_threshold(10)
        elif event.key == pygame.K_s:
            self._save_analog_thresholds()
        elif event.key == pygame.K_RETURN:
            # A jelenlegi mert ertek koze allitas: gyors "tanitas" - a mert
            # ertek es a kuszob kozotti felezopontot veszi at.
            if self.cursor < len(self.analog_values) and self.cursor < len(self.analog_thresholds):
                measured = self.analog_values[self.cursor]
                target = max(0, min(1023, measured + 40))
                delta = target - self.analog_thresholds[self.cursor]
                self._adjust_analog_threshold(delta)

    def _handle_hiscore_edit(self, event):
        # The final row is the full-table reset action.  This replaces the old
        # dedicated F8 entry without hiding destructive behaviour behind a
        # single blind shortcut.
        score_count = len(self.score_manager.scores)
        count = score_count + 1
        if event.key == pygame.K_UP:
            self.cursor = (self.cursor - 1) % count
        elif event.key == pygame.K_DOWN:
            self.cursor = (self.cursor + 1) % count
        elif event.key in (pygame.K_DELETE, pygame.K_RETURN):
            if self.cursor == score_count:
                self.screen = "reset_confirm"
            else:
                self._pending_delete_index = self.cursor
                self.screen = "hiscore_delete_confirm"
        elif event.key == pygame.K_ESCAPE:
            # Innen kozvetlenul kilep a teljes szerviz menubol (nem csak
            # a fomenube lep vissza), egyenesen az attract-loopba.
            self.should_exit = True

    def _handle_hiscore_delete_confirm(self, event):
        if event.key in (pygame.K_y, pygame.K_RETURN):
            self.score_manager.remove_at(self._pending_delete_index)
            self.status_message = "Torolve!"
            self.cursor = min(self.cursor, len(self.score_manager.scores) - 1)
        self._pending_delete_index = None
        self.screen = "hiscore_edit"

    def _handle_thanks_edit(self, event):
        count = len(self.thanks_manager.names)
        if event.key == pygame.K_UP and count:
            self.cursor = (self.cursor - 1) % count
        elif event.key == pygame.K_DOWN and count:
            self.cursor = (self.cursor + 1) % count
        elif event.key == pygame.K_a:
            self._text_input_buffer = ""
            self.screen = "thanks_add_input"
        elif event.key == pygame.K_DELETE and count:
            self.thanks_manager.remove_at(self.cursor)
            self.status_message = "Torolve!"
            self.cursor = max(0, min(self.cursor, len(self.thanks_manager.names) - 1))
        elif event.key == pygame.K_ESCAPE:
            self._go_main()

    def _handle_thanks_add_input(self, event):
        if event.key == pygame.K_RETURN:
            if self._text_input_buffer.strip():
                self.thanks_manager.add(self._text_input_buffer)
                self.status_message = "Hozzaadva!"
            self._text_input_buffer = ""
            self.screen = "thanks_edit"
        elif event.key == pygame.K_ESCAPE:
            self._text_input_buffer = ""
            self.screen = "thanks_edit"
        elif event.key == pygame.K_BACKSPACE:
            self._text_input_buffer = self._text_input_buffer[:-1]
        elif event.unicode and event.unicode.isprintable() and len(self._text_input_buffer) < 24:
            self._text_input_buffer += event.unicode

    def _handle_input_test(self, event):
        if event.key == pygame.K_ESCAPE:
            self._go_diagnostics()

    def _handle_find_arduino(self):
        """Nem sub-screen, hanem egy azonnali akcio: lefuttatja az
        arduino-cli-s port-detektalast (max ~1-2s, elfogadhato egy
        deliberalt, ritka menupontnal), elmenti a talalt portot (ha van),
        es a status_message-ben mutatja az eredmenyt."""
        port, label = arduino_port.detect_port()
        if port:
            arduino_port.save_port(port)
            self.status_message = f"Talalva: {port} ({label})"
        else:
            self.status_message = f"Nincs Arduino ({label})"

    def _handle_serial_monitor(self, event):
        if event.key == pygame.K_ESCAPE:
            self._go_diagnostics()

    def _handle_particle_editor(self, event):
        keys = self.particle_settings.keys_in_order()
        if event.key == pygame.K_UP:
            self.cursor = (self.cursor - 1) % len(keys)
        elif event.key == pygame.K_DOWN:
            self.cursor = (self.cursor + 1) % len(keys)
        elif event.key == pygame.K_LEFT:
            self.particle_settings.adjust(keys[self.cursor], -1)
        elif event.key == pygame.K_RIGHT:
            self.particle_settings.adjust(keys[self.cursor], +1)
        elif event.key == pygame.K_r:
            self.particle_settings.reset_defaults()
            self.status_message = "Alaperelmezesek visszaallitva!"
        elif event.key == pygame.K_ESCAPE:
            self._go_main()

    def _handle_minigame_difficulty(self, event):
        if self.minigame_settings is None:
            if event.key == pygame.K_ESCAPE:
                self._go_main()
            return
        games = self.minigame_settings.GAMES
        if event.key == pygame.K_UP:
            self.cursor = (self.cursor - 1) % len(games)
        elif event.key == pygame.K_DOWN:
            self.cursor = (self.cursor + 1) % len(games)
        elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
            game_id, _label = games[self.cursor]
            direction = -1 if event.key == pygame.K_LEFT else 1
            self.minigame_settings.adjust_difficulty(game_id, direction)
            self.status_message = (
                f"{self.minigame_settings.difficulty_label(game_id)} elmentve"
            )
        elif event.key == pygame.K_r:
            game_id, _label = games[self.cursor]
            self.minigame_settings.reset_normal(game_id)
            self.status_message = "NORMAL visszaallitva"
        elif event.key == pygame.K_ESCAPE:
            self._go_main()

    # --- Light test (fenyeffekt teszt a gepen, soros LT parancsokkal) ---

    def _load_effect_list(self):
        """(id, nev) parok az effect_data.h bakedEffects tablajabol.
        A 'fx_' adat-pointer teszi egyertelmuve, hogy tabla-sort matchelunk
        (nem a struct-definiciot vagy kommentet)."""
        effects = []
        try:
            with open(LIGHT_EFFECTS_HEADER, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            for m in re.finditer(r'\{\s*(\d+)\s*,\s*"([^"]*)"\s*,\s*fx_', text):
                effects.append((int(m.group(1)), m.group(2)))
        except OSError:
            pass
        return effects

    def _send_light_test(self, effect_id):
        """LT,<id> + sorvege - a firmware nem-blokkolo olvasoja \\n-ig gyujt."""
        if self.serial_reader is not None:
            self.serial_reader.send_raw(f"LT,{effect_id}\n")

    def _send_light_stop(self):
        if self.serial_reader is not None:
            self.serial_reader.send_raw("LT,S\n")

    def _enter_light_test(self):
        self.light_effects = self._load_effect_list()
        self.screen = "light_test"
        self.cursor = 0
        if self.light_effects:
            self._send_light_test(self.light_effects[0][0])
            eid, name = self.light_effects[0]
            self.status_message = f"Jatszik: {name} (ID {eid})"
        else:
            self.status_message = "Nincs effekt (effect_data.h nem talalhato/ures)"

    def _leave_light_test_if_needed(self):
        """Ha a light-test kepernyon vagyunk, allitsuk le a firmware loopjat."""
        if self.screen == "light_test":
            self._send_light_stop()

    def _handle_light_test(self, event):
        count = len(self.light_effects)
        if event.key == pygame.K_ESCAPE:
            self._send_light_stop()
            self._go_diagnostics()
        elif count:
            if event.key in (pygame.K_LEFT, pygame.K_UP):
                self.cursor = (self.cursor - 1) % count
            elif event.key in (pygame.K_RIGHT, pygame.K_DOWN):
                self.cursor = (self.cursor + 1) % count
            else:
                return
            eid, name = self.light_effects[self.cursor]
            self._send_light_test(eid)
            self.status_message = f"Jatszik: {name} (ID {eid})"

    def _handle_reset_confirm(self, event):
        if event.key in (pygame.K_y, pygame.K_RETURN):
            self.score_manager.reset()
            self.status_message = "Az osszes hiscore torolve!"
        self.screen = "hiscore_edit"
        self.cursor = len(self.score_manager.scores)

    def _handle_version_info(self, event):
        if event.key == pygame.K_ESCAPE:
            self._go_main()

    # --- tartalom-eloallitas a render_service_menu-hoz ---

    def get_version_info_lines(self):
        lines = [
            "CnC Pinball GUI V3",
            f"Python {sys.version.split()[0]}",
            f"pygame-ce {pygame.version.ver}",
        ]
        try:
            repo_dir = os.path.dirname(os.path.abspath(__file__))
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_dir, capture_output=True, text=True, timeout=2
            )
            commit = result.stdout.strip()
            if commit:
                lines.append(f"git: {commit}")
        except Exception:
            pass
        return lines
