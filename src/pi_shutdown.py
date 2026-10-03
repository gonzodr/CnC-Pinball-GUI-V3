"""Request an orderly OS shutdown, only after the menu confirmation."""

import os
import subprocess
import sys


HELPER = "/usr/local/sbin/cnc-pinball-shutdown"


def request_shutdown(service_menu):
    if not sys.platform.startswith("linux") or not os.access(HELPER, os.X_OK):
        service_menu.status_message = "Pi leallitas nem erheto el ezen a gepen."
        return False
    try:
        result = subprocess.run(
            ["sudo", "-n", HELPER], capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True
        print(f"[shutdown] exit={result.returncode}: {result.stderr}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[shutdown] {exc}")
    service_menu.status_message = "Leallitasi hiba; reszletek a naploban."
    return False
