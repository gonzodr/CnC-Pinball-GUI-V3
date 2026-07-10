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
        self._recv_buffer = ""
        self._playing = False
        self._play_started_at = None
        # Csak akkor fogadunk el EOF-ot, ha a lejatszas BIZONYITOTTAN
        # elindult (time-pos > 0) - fajlbetoltes kozben az eof-reached
        # meg az elozo/idle allapotot mutathatja (igazat!), amitol a GUI
        # a video legelejen visszavette a kijelzot es osszeomlott.
        self._playback_confirmed = False
        self._request_id = 10

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
            "--hwdec=v4l2m2m",
            "--idle=yes",
            "--fullscreen",
            "--no-osc",
            "--no-input-default-bindings",
            "--input-ipc-server=" + self.SOCKET_PATH,
            "--keep-open=yes",
            "--keep-open-pause=no",   # <-- ÚJ: EOF után ne kapcsoljon globális pause-t
            "--drm-mode=640x480@60",
            "--drm-format=xrgb8888",
            "--keepaspect=yes",
            "--keepaspect-window=yes",
            "--profile=fast",
            "--ao=alsa",
            ]
        if is_headless_linux:
            mpv_args.insert(2, "--vo=drm")
            mpv_args.insert(3, "--drm-connector=HDMI-A-1")
            mpv_args.insert(4, "--drm-mode=640x480@60")

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
        if self.offline:
            print(f"[mpv] (offline) play() hivva: {video_name} "
                  f"- fake lejatszas {self.FAKE_VIDEO_DURATION_SEC}s")
            self._fake_video_started_at = time.time()
            return
        self._recv_buffer = ""          # eldobjuk az esetleges elavult IPC valaszokat
        path = os.path.join(self.VIDEO_DIR, video_name)
        if not path.endswith(".mp4"):
            path += ".mp4"
        self._send(["loadfile", path, "replace"])
        self._send(["set_property", "pause", False])
        self._playing = True
        self._playback_confirmed = False
        self._play_started_at = time.time()

    
    def stop(self):
        """Lejátszás leállítása - az mpv lebontja a videokimenetét, és
        elengedi a DRM kijelzőt (a VIDEO watchdog és a VIDEO_STOP is ezt hívja).
        MEGVÁRJA, amíg az mpv ténylegesen idle lesz, különben a pygame még
        azelőtt venné vissza a kijelzőt, hogy az mpv elengedte volna - ez a
        versenyhelyzet okozta a videó utáni hamis QUIT-okat/szaggatást."""
        if self.offline:
            self._fake_video_started_at = None
            return
        self._send(["stop"])
        self._playing = False
        self._wait_idle(timeout_sec=3.0)

    def _wait_idle(self, timeout_sec=1.0):
        """Az 'idle-active' property-t pollozza, amig az mpv le nem bontotta
        a lejatszast (max timeout_sec-ig - ha addig sem, tovabbmegyunk)."""
        if self.offline or not self._sock:
            return
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self._query_property("idle-active") is True:
                return
            time.sleep(0.05)

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

        if self._proc and self._proc.poll() is not None:
            # Az mpv process MEGHALT - ne ragadjunk orokre VIDEO allapotban:
            # a videot "befejezettnek" tekintjuk, es offline (fake) modra
            # valtunk, hogy a GUI videok nelkul is eletben maradjon.
            print("[mpv] az mpv process meghalt - offline modra valtas")
            self.offline = True
            return True

        if not self._sock:
            return False
        if not self._playing:
            return False

        # 1. fazis: megvarjuk, hogy a lejatszas TENYLEGESEN elinduljon.
        # Fajlbetoltes kozben (~0.5-1 mp a Pi-n) az eof-reached meg a
        # korabbi/idle allapot "igaz"-at mutathatja!
        if not self._playback_confirmed:
            t = self._query_property("time-pos")
            if isinstance(t, (int, float)) and t > 0:
                self._playback_confirmed = True
            elif self._play_started_at and (time.time() - self._play_started_at) > 5.0:
                # 5 mp alatt sem indult el (pl. hianyzo/serult fajl) - feladjuk
                print("[mpv] a video nem indult el 5 mp alatt - feladjuk")
                self.stop()
                return True
            return False

        # 2. fazis: mar fut a video - most mar hiheto az EOF
        if self._query_property("eof-reached") is True:
            self.stop()  # megvarja az idle-t is -> tiszta kijelzo-atadas
            return True
        return False

    def _query_property(self, name):
        """Egy mpv property lekerdezese IPC-n. None, ha nem elerheto.
        IPC-hiba eseten ujracsatlakozik (a "Connection reset" utan
        kulonben SOHA nem latnank meg az EOF-ot -> orok VIDEO allapot)."""
        if self.offline or not self._sock:
            return None
        self._request_id += 1
        req_id = self._request_id
        request = json.dumps({
            "command": ["get_property", name],
            "request_id": req_id
        }) + "\n"
        try:
            self._sock.sendall(request.encode("utf-8"))
            deadline = time.time() + 0.15
            while time.time() < deadline:
                self._recv_buffer += self._sock.recv(4096).decode("utf-8")
                while "\n" in self._recv_buffer:
                    line, self._recv_buffer = self._recv_buffer.split("\n", 1)
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("request_id") == req_id:
                        return data.get("data")
        except socket.timeout:
            pass
        except OSError:
            print("[mpv] IPC socket megszakadt, ujracsatlakozas...")
            try:
                self._connect()
            except OSError:
                pass
        return None

    def hard_reset(self):
        """Vegso mentsvar: az mpv process teljes ujrainditasa. A process
        halalaval a kernel GARANTALTAN visszaadja a DRM kijelzot - akkor
        hivjuk, ha az mpv a stop utan sem engedi el a kepernyot."""
        if self.offline:
            return
        print("[mpv] hard reset: mpv process ujrainditasa")
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
            self._proc = None
        self._playing = False
        self.start()

    def shutdown(self):
        if self.offline:
            return
        if self._sock:
            self._sock.close()
        if self._proc:
            self._proc.terminate()
