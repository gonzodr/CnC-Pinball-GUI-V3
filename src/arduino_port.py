"""Arduino Mega port-detektalas (arduino-cli board list --json) es a
legutobb detektalt port perzisztalasa egy kis JSON fajlba.

Ket hely hivja a detect_port()-ot: a firmware_update.py (sikeres feltoltes
elott/utan) es a szerviz menu "Arduino keresese" pontja. A SerialReader
viszont SOSEM hivja az arduino-cli-t maga - csak a mar elmentett erteket
olvassa be (load_saved_port()), hogy ne kelljen masodpercenkent egy
kulso folyamatot inditania akkor is, ha nincs is Arduino csatlakoztatva."""

import json
import os
import subprocess

ARDUINO_CLI_PATH = os.environ.get("CNC_ARDUINO_CLI", os.path.expanduser("~/bin/arduino-cli"))
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "serial_port.json")


def detect_port():
    """Lekerdezi az arduino-cli-t. Visszaad egy (port, label) part siker
    eseten, vagy (None, indoklas)-t, ha nem talalt semmit."""
    if not os.path.exists(ARDUINO_CLI_PATH):
        return None, "arduino-cli nem talalhato"
    try:
        result = subprocess.run(
            [ARDUINO_CLI_PATH, "board", "list", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(result.stdout)
        for entry in data.get("detected_ports", []):
            port_info = entry.get("port", {})
            if port_info.get("protocol") != "serial":
                continue
            matched = entry.get("matching_boards") or []
            if not matched:
                continue
            return port_info.get("address"), matched[0].get("name", "ismeretlen eszkoz")
    except Exception as e:
        return None, str(e)
    return None, "nincs csatlakoztatva Arduino"


def load_saved_port():
    """A legutobb elmentett port, vagy None, ha meg sose talaltunk semmit."""
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f).get("port")
    except Exception:
        return None


def save_port(port):
    with open(CONFIG_PATH, "w") as f:
        json.dump({"port": port}, f)
