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
- "reset_confirm": osszes hiscore torlese, megerositessel
- "version_info": szoftver/verzio info
"""

import os
import subprocess
import sys

import pygame


class ServiceMenuController:
    MAIN_ITEMS = [
        ("hiscore_edit", "Hiscore szerkesztes / torles"),
        ("thanks_edit", "Special Thanks nevek"),
        ("input_test", "Input / gomb teszt"),
        ("serial_monitor", "Serial Monitor (raw)"),
        ("particle_editor", "Particle szerkeszto"),
        ("reset_confirm", "OSSZES hiscore torlese"),
        ("version_info", "Verzio info"),
        ("exit", "Kilepes"),
    ]

    def __init__(self, score_manager, thanks_manager, recent_events, serial_reader=None, particle_settings=None):
        self.score_manager = score_manager
        self.thanks_manager = thanks_manager
        self.recent_events = recent_events  # deque, csak olvassuk (input_test kepernyohoz)
        self.serial_reader = serial_reader  # csak olvassuk (serial_monitor kepernyohoz)
        self.particle_settings = particle_settings  # ParticleSettingsManager (particle_editor kepernyohoz)

        self.should_exit = False
        self.screen = "main"
        self.cursor = 0
        self.status_message = ""

        self._text_input_buffer = ""
        self._pending_delete_index = None

    def reset(self):
        """Minden belepeskor (Ctrl+M) a fomenurol indulunk ujra."""
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

    def handle_pygame_events(self, pygame_events):
        for event in pygame_events:
            if event.type != pygame.KEYDOWN:
                continue
            self.status_message = ""
            handler = getattr(self, f"_handle_{self.screen}", None)
            if handler:
                handler(event)

    def _handle_main(self, event):
        if event.key == pygame.K_UP:
            self.cursor = (self.cursor - 1) % len(self.MAIN_ITEMS)
        elif event.key == pygame.K_DOWN:
            self.cursor = (self.cursor + 1) % len(self.MAIN_ITEMS)
        elif event.key == pygame.K_RETURN:
            target, _ = self.MAIN_ITEMS[self.cursor]
            if target == "exit":
                self.should_exit = True
            else:
                self.screen = target
                self.cursor = 0
        elif event.key == pygame.K_ESCAPE:
            self.should_exit = True

    def _handle_hiscore_edit(self, event):
        count = len(self.score_manager.scores)
        if event.key == pygame.K_UP:
            self.cursor = (self.cursor - 1) % count
        elif event.key == pygame.K_DOWN:
            self.cursor = (self.cursor + 1) % count
        elif event.key in (pygame.K_DELETE, pygame.K_RETURN):
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
            self._go_main()

    def _handle_serial_monitor(self, event):
        if event.key == pygame.K_ESCAPE:
            self._go_main()

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

    def _handle_reset_confirm(self, event):
        if event.key in (pygame.K_y, pygame.K_RETURN):
            self.score_manager.reset()
            self.status_message = "Az osszes hiscore torolve!"
        self._go_main()

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
