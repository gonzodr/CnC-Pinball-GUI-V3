"""Text-mode Pi configuration with a temporary display handoff only."""

import os
import subprocess
import sys

import pygame


HELPER = "/usr/local/sbin/cnc-raspi-config"


def run_raspi_config(gui, service_menu):
    """Keep serial open while the root-owned helper manages its own Linux VT."""
    if not sys.platform.startswith("linux") or not os.path.isfile("/usr/bin/raspi-config"):
        service_menu.status_message = "A raspi-config csak Raspberry Pi-n erheto el."
        return
    if not os.access(HELPER, os.X_OK):
        service_menu.status_message = "A raspi-config indito nincs telepitve (lasd README)."
        return

    gui.release_display()
    try:
        result = subprocess.run(
            ["sudo", "-n", HELPER], stderr=subprocess.PIPE, text=True,
        )
        if result.returncode:
            print(f"[raspi-config] exit={result.returncode}: {result.stderr}")
            service_menu.status_message = "Raspi-config inditasi hiba; reszletek a naploban."
        else:
            service_menu.status_message = "Raspberry Pi konfiguracio bezarva."
    except OSError as exc:
        print(f"[raspi-config] {exc}")
        service_menu.status_message = "Nem sikerult elinditani a raspi-configot."
    finally:
        gui.acquire_display()
        gui.cancel_fade_transition()
        pygame.event.clear()
