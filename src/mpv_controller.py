"""mpv vezerles: MINDEN videohoz friss mpv process indul.

Korabban egyetlen, allandoan futo idle mpv-t vezereltunk IPC socketen -
az gyorsabb inditast igert, de a gyakorlatban torekeny volt:
 - a pygame/SDL kmsdrm hasznalata utan az mpv vo=gpu atomic commitjai
   EINVAL-lal zaporoztak ("Failed to commit atomic request"), a vo=drm
   pedig lassu (CPU-blit); a kezi, processzenkenti mpv viszont
   bizonyitottan hibatlanul es ~valos idoben jatszott le
 - az eof-reached property fajlbetoltes kozben hazudott, az IPC socket
   megszakadt, az idle mpv nem engedte el a DRM kijelzot...

A processz-per-video modell mindezt megoldja:
 - EOF-detektalas = a processz kilepese (keep-open nelkul az mpv a video
   vegen kilep) - nem lehet felreertelmezni
 - a kijelzot a processz halalakor a KERNEL adja vissza, garantaltan
Ara: ~2-2.5 mp inditasi kesleltetes videonkent a Pi 3B+-on.
"""

import os
import subprocess
import sys
import time


class MpvController:

    VIDEO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "assets", "Videos")
    FAKE_VIDEO_DURATION_SEC = 3.0   # offline modban ennyi ido utan "er veget" egy fake video

    def __init__(self):
        self._proc = None
        # FEJLESZTOI MOD: ha True, minden metodus csak logol. Automatikusan
        # True lesz, ha nincs mpv telepitve (pl. Windows fejlesztoi gep).
        self.offline = False
        self._fake_video_started_at = None
        self._playing = False
        self._missing = False

    def _mpv_args(self, path):
        # A --vo=gpu/--gpu-context=drm csak tenyleges headless Pi-n kell
        # (nincs X11/Wayland) - desktopon az mpv sajat alapertelmezese fut.
        has_x11_or_wayland = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        is_headless_linux = sys.platform.startswith("linux") and not has_x11_or_wayland

        args = [
            "mpv",
            "--hwdec=no",  # szoftveres dekodolas: a hwdec drm_prime HW-overlay
                           # sikja a pygame utani DRM-allapoton EINVAL-t kap
                           # (fekete kep); swdec = sima GL-render, es a merese
                           # ugyanolyan gyors volt (5.5s vs 5.1s / 90 frame)
            "--fullscreen",
            "--no-osc",
            "--no-input-default-bindings",
            "--keepaspect=yes",
            "--keepaspect-window=yes",
            "--profile=fast",
            "--ao=alsa",
        ]
        if is_headless_linux:
            # SZANDEKOSAN NINCS --drm-mode: a GUI (pygame/SDL) is 640x480-at
            # allit be, az mpv csak atveszi az aktualis modot. A sajat
            # modvaltasi kiserlete a pygame utani DRM-allapoton EINVAL
            # atomic-hibazaporba fulladt (fekete kepernyo, mikozben a
            # video "lejatszodott") - modvaltas nelkul nincs utkozes.
            args.insert(1, "--vo=gpu")
            args.insert(2, "--gpu-context=drm")
            args.insert(3, "--drm-connector=HDMI-A-1")
        args.append(path)
        return args

    def start(self):
        """Kompatibilitasi belepesi pont (main.py hivja indulaskor).
        Processz-per-video modellben nincs mit elore inditani - csak azt
        derinti ki, hogy van-e egyaltalan mpv (kulonben offline mod)."""
        try:
            subprocess.run(["mpv", "--version"], capture_output=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print("[mpv] mpv parancs nem talalhato - mpv offline mod "
                  "(a GUI-t igy is tudod tesztelni)")
            self.offline = True

    def play(self, video_name: str):
        if self.offline:
            print(f"[mpv] (offline) play() hivva: {video_name} "
                  f"- fake lejatszas {self.FAKE_VIDEO_DURATION_SEC}s")
            self._fake_video_started_at = time.time()
            return
        self.stop()  # ha meg futna egy elozo video, eloszor tisztan lezarjuk
        time.sleep(0.3)  # hagyjuk, hogy a pygame/SDL teljesen elengedje a DRM-et
        path = os.path.join(self.VIDEO_DIR, video_name)
        if not path.endswith(".mp4"):
            path += ".mp4"
        if not os.path.exists(path):
            # Ne is inditsunk processzt - a VIDEO allapot azonnal veget er,
            # a jatek megy tovabb (a hianyzo video csak naplo-tema).
            print(f"[mpv] nincs ilyen video: {path} - kihagyva")
            self._missing = True
            self._playing = False
            self._proc = None
            return
        self._proc = subprocess.Popen(self._mpv_args(path))
        self._playing = True

    def is_finished(self) -> bool:
        """A video akkor er veget, amikor az mpv processz kilep (keep-open
        nelkul az mpv EOF-nal magatol kilep). Offline modban fake idozito."""
        if self.offline:
            if self._fake_video_started_at is None:
                return False
            if time.time() - self._fake_video_started_at >= self.FAKE_VIDEO_DURATION_SEC:
                self._fake_video_started_at = None
                return True
            return False

        if self._missing:
            self._missing = False  # hianyzo fajl: a VIDEO allapot azonnal lezarul
            return True
        if not self._playing:
            # FONTOS: a state_machine tick()-je MEG A play() ELOTT is
            # lekerdez (az allapotvaltast a main csak utana dolgozza fel) -
            # ilyenkor NEM "kesz", hanem meg el sem indult!
            return False
        if self._proc is None or self._proc.poll() is not None:
            self._proc = None
            self._playing = False
            return True
        return False

    def stop(self):
        """Lejatszas azonnali leallitasa (watchdog / VIDEO_STOP / uj video)."""
        if self.offline:
            self._fake_video_started_at = None
            return
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None
        self._playing = False

    def hard_reset(self):
        """Kompatibilitas (main.py display-visszaveteli veszhelyzete):
        itt egyszeruen a futo video kilovese."""
        print("[mpv] hard reset: futo mpv leallitasa")
        self.stop()

    def shutdown(self):
        self.stop()
