"""Firmware update - onallo program, NEM resze a fo pygame event loopnak.

A szerviz menubol inditva: main.py elengedi a DRM kijelzot es a soros
portot, majd subprocess-kent elinditja ezt a scriptet es MEGVARJA (blokkolva),
amig ez a program vissza nem ter - utana main.py ujra magahoz veszi a
kijelzot/portot es folytatja a GUI-t onnan, ahol abbahagyta.

Sajat, egyszeru pygame ablakot nyit (ugyanazzal a kmsdrm-auto-detektalassal,
mint a score_gui.py), es elo logkent mutatja: Arduino port keresese,
git pull a firmware repoban, arduino-cli compile, arduino-cli upload.
A tenyleges munka kulon szalon fut, hogy a kepernyo a hosszabb lepesek
(forditas, feltoltes) alatt is frissuljon, ne fagyjon le.
"""

import json
import os
import subprocess
import sys
import threading

import pygame

_has_x11_or_wayland = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
if sys.platform.startswith("linux") and not _has_x11_or_wayland:
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")

FIRMWARE_DIR = os.environ.get("CNC_FIRMWARE_DIR", os.path.expanduser("~/CnC_firmware4"))
ARDUINO_CLI = os.environ.get("CNC_ARDUINO_CLI", os.path.expanduser("~/bin/arduino-cli"))
FQBN = "arduino:avr:mega"

SCREEN_W, SCREEN_H = 640, 480
BG_COLOR = (12, 18, 34)
TEXT_COLOR = (220, 220, 225)
DIM_COLOR = (140, 140, 155)
OK_COLOR = (120, 220, 120)
ERR_COLOR = (255, 110, 110)
WARN_COLOR = (255, 210, 60)


class Worker:
    def __init__(self):
        self._lines = []
        self._lock = threading.Lock()
        self.done = False
        self.success = None

    def log(self, text, color=TEXT_COLOR):
        with self._lock:
            self._lines.append((text, color))
        print(text)

    def get_lines(self):
        with self._lock:
            return list(self._lines)

    def run_cmd(self, args, cwd=None):
        self.log("$ " + " ".join(args), DIM_COLOR)
        try:
            proc = subprocess.Popen(
                args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except FileNotFoundError as e:
            self.log(f"HIBA: {e}", ERR_COLOR)
            return 1
        for line in proc.stdout:
            self.log(line.rstrip())
        proc.wait()
        return proc.returncode

    def detect_port(self):
        self.log("Arduino port keresese...", WARN_COLOR)
        try:
            result = subprocess.run(
                [ARDUINO_CLI, "board", "list", "--json"],
                capture_output=True, text=True, timeout=15,
            )
            data = json.loads(result.stdout)
            for entry in data.get("detected_ports", []):
                port_info = entry.get("port", {})
                if port_info.get("protocol") != "serial":
                    continue
                matched = entry.get("matching_boards") or []
                label = matched[0]["name"] if matched else "ismeretlen eszkoz"
                address = port_info.get("address")
                self.log(f"Talalva: {address} ({label})", OK_COLOR)
                return address
        except Exception as e:
            self.log(f"Port-detektalas hiba: {e}", ERR_COLOR)
        self.log("Nem talalhato csatlakoztatott Arduino.", WARN_COLOR)
        return None

    def run(self):
        self.log("=== Firmware update inditasa ===", WARN_COLOR)
        self.log("")

        port = self.detect_port()

        self.log("")
        self.log("Git pull...", WARN_COLOR)
        if self.run_cmd(["git", "pull"], cwd=FIRMWARE_DIR) != 0:
            self.log("Git pull sikertelen!", ERR_COLOR)
            self._finish(False)
            return

        self.log("")
        self.log("Forditas...", WARN_COLOR)
        if self.run_cmd([ARDUINO_CLI, "compile", "--fqbn", FQBN, FIRMWARE_DIR]) != 0:
            self.log("Forditas sikertelen!", ERR_COLOR)
            self._finish(False)
            return
        self.log("Forditas OK.", OK_COLOR)

        if not port:
            self.log("")
            self.log("Feltoltes kihagyva (nincs csatlakoztatott Arduino).", WARN_COLOR)
            self._finish(True)
            return

        self.log("")
        self.log(f"Feltoltes ({port})...", WARN_COLOR)
        if self.run_cmd([ARDUINO_CLI, "upload", "-p", port, "--fqbn", FQBN, FIRMWARE_DIR]) != 0:
            self.log("Feltoltes sikertelen!", ERR_COLOR)
            self._finish(False)
            return
        self.log("Feltoltes OK.", OK_COLOR)
        self._finish(True)

    def _finish(self, success):
        self.success = success
        self.log("")
        self.log("Kesz! Nyomj egy gombot a visszateres...", (255, 255, 255))
        self.done = True


def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("Firmware Update")
    font = pygame.font.Font(None, 20)
    title_font = pygame.font.Font(None, 30)
    clock = pygame.time.Clock()

    worker = Worker()
    threading.Thread(target=worker.run, daemon=True).start()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and worker.done:
                running = False

        screen.fill(BG_COLOR)
        title = title_font.render("FIRMWARE UPDATE", True, (255, 255, 255))
        screen.blit(title, (20, 16))
        pygame.draw.line(screen, (80, 80, 90), (20, 50), (SCREEN_W - 20, 50), 2)

        lines = worker.get_lines()
        line_h = 18
        max_visible = (SCREEN_H - 70) // line_h
        for i, (text, color) in enumerate(lines[-max_visible:]):
            surf = font.render(text[:76], True, color)
            screen.blit(surf, (20, 60 + i * line_h))

        pygame.display.flip()
        clock.tick(20)

    pygame.quit()
    sys.exit(0 if worker.success else 1)


if __name__ == "__main__":
    main()
