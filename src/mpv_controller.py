"""mpv IPC vezérlés: háttérben futó mpv process indítása és irányítása socket-en."""

import socket
import json
import os
import subprocess
import sys
import time


class MpvController:
    """
    Az mpv-t egy mindig futó, háttérben idle-ben várakozó process-ként
    kezeli, JSON IPC socketen küldött parancsokkal vezéreljük.

    Ez azért fontos, mert ha minden videónál újraindítanánk az mpv-t,
    a process-indítás (néhány száz ms) jelentős, érezhető késleltetést
    okozna a trigger és a videó megjelenése között.
    """

    SOCKET_PATH = "/tmp/mpv-socket"
    VIDEO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "assets", "Videos")
    FAKE_VIDEO_DURATION_SEC = 3.0   # offline modban ennyi ido utan "er veget" egy fake video

    def __init__(self):
        self._proc = None
        self._sock = None
        # FEJLESZTOI MOD: ha True, minden metodus csak logol, nem csinal
        # semmit. Automatikusan True lesz, ha nincs mpv telepitve, vagy
        # ha az AF_UNIX socket nem elerheto (pl. Windows fejlesztoi gepen).
        # A Pi-n, ahol mind ketto rendelkezesre all, ez False marad, es
        # a teljes mpv-vezerles aktivan mukodik.
        self.offline = False
        # Offline modban ez tartja szamon, mikor "indult" a fake video,
        # hogy is_finished() szimulalni tudja a lejatszas veget.
        self._fake_video_started_at = None

    def start(self):
        """Elindítja az mpv-t idle módban, framebuffer/DRM kimenetre.

        Ha nincs mpv telepítve, vagy AF_UNIX socket nem elérhető
        (tipikusan Windows fejlesztői gépen), átkapcsol offline
        módba: a GUI ettől függetlenül tesztelhető marad.
        """
        if not hasattr(socket, "AF_UNIX"):
            print("[mpv] AF_UNIX nem elerheto ezen a rendszeren "
                  "(Windows-on ez varhato) - mpv offline mod")
            self.offline = True
            return

        if os.path.exists(self.SOCKET_PATH):
            os.remove(self.SOCKET_PATH)

        # A --vo=drm es --drm-connector csak akkor kell, ha tenyleges
        # headless Pi-n vagyunk (nincs X11/Wayland desktop). VM-en
        # vagy barmilyen Linux desktopon (pl. VirtualBox-os Debian)
        # ezek a flag-ek hibat adnanak, mert nincs DRM/KMS hozzaferes
        # - ilyenkor hagyjuk, hogy az mpv a sajat alapertelmezett
        # (X11-es) videokimenetet hasznalja, ablakban.
        has_x11_or_wayland = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        is_headless_linux = sys.platform.startswith("linux") and not has_x11_or_wayland

        mpv_args = [
                    "mpv",
                    "--hwdec=v4l2m2m-copy",
                    "--idle=yes",
                    "--fs=no",                 # <--- KIKAPCSOLVA: Ne a monitor natív felbontását erőltesse
                    "--geometry=640x480",      # <--- KÉNYSZERÍTVE: Maradjon 640x480-on
                    "--no-border",             # Ne legyen ablakkeret
                    "--no-osc",
                    "--no-input-default-bindings",
                    "--input-ipc-server=" + self.SOCKET_PATH,
                    "--keep-open=no",
                    "--vo=gpu",
                    "--gpu-api=opengl",
                    "--scale=nearest",         # <--- EZ A TITOK: 'Nearest neighbor' skálázás (nem terheli a GPU-t)
                    "--video-unscaled=yes"     # <--- Még egy tipp: ha nem kell skálázni, ne is tegye
                    ]
        if is_headless_linux:
            mpv_args.insert(2, "--vo=drm")
            mpv_args.insert(3, "--drm-connector=HDMI-A-1")  # allitsd a tenyleges csatlakozora (lsdrm-mel checkelheted)

        try:
            self._proc = subprocess.Popen(mpv_args)
        except FileNotFoundError:
            print("[mpv] mpv parancs nem talalhato - mpv offline mod "
                  "(a GUI-t igy is tudod tesztelni)")
            self.offline = True
            return

        # várjuk meg, amíg a socket létrejön (mpv indítása után pár tized mp)
        for _ in range(50):
            if os.path.exists(self.SOCKET_PATH):
                break
            time.sleep(0.1)

        try:
            self._connect()
        except OSError as e:
            print(f"[mpv] socket csatlakozas sikertelen: {e} - offline mod")
            self.offline = True

    def _connect(self):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(self.SOCKET_PATH)
        self._sock.settimeout(0.1)  # nem-blokkoló jellegű olvasáshoz

    def _send(self, command):
        """Egy IPC parancsot küld az mpv-nek."""
        if self.offline or not self._sock:
            return
        payload = json.dumps({"command": command}) + "\n"
        try:
            self._sock.sendall(payload.encode("utf-8"))
        except (BrokenPipeError, OSError):
            print("[mpv] socket megszakadt, ujracsatlakozas")
            self._connect()

    def play(self, video_name: str):
        """Betölti és azonnal lejátssza a megadott videót."""
        if self.offline:
            print(f"[mpv] (offline) play() hivva: {video_name} "
                  f"- fake lejatszas {self.FAKE_VIDEO_DURATION_SEC}s")
            self._fake_video_started_at = time.time()
            return
        path = os.path.join(self.VIDEO_DIR, video_name)
        if not path.endswith(".mp4"):
            path += ".mp4"
        self._send(["loadfile", path, "replace"])

    def stop(self):
        """Leállítja a lejátszást, visszamegy idle (fekete) állapotba."""
        if self.offline:
            print("[mpv] (offline) stop() hivva")
            self._fake_video_started_at = None
            return
        self._send(["stop"])

    def is_finished(self) -> bool:
        """
        Lekérdezi, hogy véget ért-e a jelenlegi videó.
        Az 'eof-reached' property-t nézzük.

        Offline módban (PC-s teszt, nincs valódi mpv) egy fake
        időzítőt szimulálunk: FAKE_VIDEO_DURATION_SEC másodperc után
        "véget ér" a videó, hogy a VIDEO -> SCORE visszaváltás
        tesztelhető legyen valódi hardver nélkül is.
        """
        if self.offline:
            if self._fake_video_started_at is None:
                return False
            elapsed = time.time() - self._fake_video_started_at
            if elapsed >= self.FAKE_VIDEO_DURATION_SEC:
                self._fake_video_started_at = None
                return True
            return False

        if not self._sock:
            return False
        request = json.dumps({
            "command": ["get_property", "eof-reached"],
            "request_id": 1
        }) + "\n"
        try:
            self._sock.sendall(request.encode("utf-8"))
            response = self._sock.recv(4096).decode("utf-8")
            for line in response.strip().split("\n"):
                if not line:
                    continue
                data = json.loads(line)
                if data.get("request_id") == 1:
                    return data.get("data", False) is True
        except (socket.timeout, OSError, json.JSONDecodeError):
            pass
        return False

    def shutdown(self):
        if self.offline:
            return
        if self._sock:
            self._sock.close()
        if self._proc:
            self._proc.terminate()
