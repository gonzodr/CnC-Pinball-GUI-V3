"""Non-blocking, fast-forward-only GUI repository update. No stash/reset."""

from pathlib import Path
import os
import subprocess
import threading


# Runtime data may differ on a cabinet; code/asset edits must be saved first.
RUNTIME_FILES = {
    "src/hiscores.json", "src/thanks_names.json", "src/munchies_hiscores.json",
    "src/particle_settings.json", "src/minigame_settings.json",
    "src/serial_port.json",
}


class GuiUpdateWorker:
    def __init__(self, repo_dir=None):
        self.repo_dir = Path(repo_dir) if repo_dir else Path(__file__).resolve().parents[1]
        self.done = False
        self.success = False
        self._lines = []
        self._lock = threading.Lock()

    def log(self, line):
        print(f"[gui-update] {line}")
        with self._lock:
            self._lines.append(line)

    def get_lines(self):
        with self._lock:
            return list(self._lines)

    def start(self):
        # A normal quit must not abandon an in-flight Git write.
        threading.Thread(target=self.run, daemon=False).start()

    def _git(self, *args):
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        return subprocess.run(
            ["git", *args], cwd=self.repo_dir, env=env,
            capture_output=True, text=True, timeout=60,
        )

    def run(self):
        try:
            self.log("Helyi modositasok ellenorzese...")
            status = self._git("diff", "HEAD", "--name-only")
            if status.returncode:
                self.log(status.stderr.strip() or "Nem erheto el a GUI Git repo.")
                return
            changed = set(status.stdout.splitlines()) - RUNTIME_FILES
            if changed:
                self.log("Helyi kod/asset modositas van; frissites leallitva.")
                for name in sorted(changed):
                    self.log(name)
                self.log("Elobb mentsd/commitold a modositasokat. Nem irjuk felul oket.")
                return
            self.log("GUI letoltes: git pull --ff-only...")
            result = self._git("pull", "--ff-only")
            for line in (result.stdout + result.stderr).splitlines():
                self.log(line)
            if result.returncode:
                self.log("Frissites sikertelen; a GUI nem indul ujra.")
                return
            self.success = True
            self.log("Kesz. Zold/Enter: GUI ujrainditasa; piros/Esc: vissza.")
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.log(f"Frissitesi hiba: {exc}")
        finally:
            self.done = True
