"""Soros port olvasása külön szálon, nem-blokkoló módon a fő loop felé."""

import atexit
import csv
import os
import re
import serial
import threading
import queue
import time
from datetime import datetime
from collections import deque
from protocol import parse_line, GameEvent
import arduino_port


class SerialReader:
    """
    Külön szálon olvassa a soros portot, és a feldolgozott
    GameEvent-eket egy thread-safe queue-ba teszi.

    Külön szálon kell futnia, mert a soros olvasás blokkoló,
    és nem akarjuk, hogy ez lefagyassza a GUI/video render loopot.
    """

    RAW_LOG_MAXLEN = 30  # a szerviz menu Serial Monitor kepernyojehez
    MINIGAME_HEARTBEAT_SEC = 0.35
    SENSOR_LOG_START_RETRY_SEC = 0.75
    SENSOR_LOG_STOP_RETRY_SEC = 0.75
    SENSOR_LOG_STABLE_TROUGH_BITS = 5
    SENSOR_LOG_FALLBACK_NAMES = tuple(f"sensor_{i}" for i in range(1, 7))
    SENSOR_LOG_FALLBACK_EXTRA_NAMES = (
        "bip",
        "intmon",
        "firstplay",
        "sidelaneSave",
        "arrivalArmed",
        "faultCode",
        "observedCount",
        "feedSeq",
        "lastFeedReason",
    )
    SENSOR_LOG_FLAG_NAMES = (
        "measurement_inhibited",
        "shoot",
        "kick",
        "shooter_lane_closed",
        "ufo_busy",
        "ballsave",
        "multiball",
        "drain_armed",
    )

    def __init__(self, port: str, baudrate: int = 115200,
                 diagnostic_log_dir: str = None):
        self.port = port  # fallback, ha az auto-detektalas nem talal semmit
        self.baudrate = baudrate
        self.event_queue: "queue.Queue[GameEvent]" = queue.Queue()
        self._stop_flag = threading.Event()
        self._thread = None
        # (timestamp, nyers sor) parok - MINDEN beerkezo sor, fuggetlenul
        # attol, hogy sikerult-e ervenyes GameEvent-te alakitani. Deque
        # append/iterate szalak kozott a GIL miatt biztonsagos ebben az
        # egyszeru, egy-irou/egy-olvaso esetben.
        self.raw_log = deque(maxlen=self.RAW_LOG_MAXLEN)
        self._ser = None  # az elo kapcsolat (send_raw hasznalja; a szal kezeli)
        self._write_lock = threading.Lock()
        # Tartalek heartbeat a soros olvasoszalon. A state machine tovabbra is
        # kuld sajat ALIVE-ot, de egy lassu render/audio/file-IO frame igy nem
        # tudja lejaratni az Arduino watchdogjat.
        self._minigame_heartbeat_session = None
        self._minigame_heartbeat_next = 0.0
        self._heartbeat_lock = threading.Lock()

        # A hosszu, jatek kozben is futo szenzorlogot kozvetlenul ezen a
        # hatterszalon irjuk. Igy a render/video loop soha nem var fajl-I/O-ra,
        # es a telemetria nem kerul a jatekesemenyek koze.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.diagnostic_log_dir = diagnostic_log_dir or os.path.join(
            project_root, "logs", "diagnostics"
        )
        self._sensor_log_lock = threading.Lock()
        self._sensor_log_file = None
        self._sensor_log_writer = None
        self._sensor_log_header_written = False
        self._sensor_log_names = self.SENSOR_LOG_FALLBACK_NAMES
        self._sensor_log_extra_names = self.SENSOR_LOG_FALLBACK_EXTRA_NAMES
        self._sensor_log_path = None
        self.last_sensor_log_path = None
        self.sensor_log_last_error = ""
        self._sensor_log_desired_active = False
        self._sensor_log_paused_for_port_release = False
        self._sensor_log_acknowledged = False
        self._sensor_log_start_next_retry = 0.0
        self._sensor_log_stop_pending = False
        self._sensor_log_stop_next_retry = 0.0
        atexit.register(self._close_sensor_log)

    def start(self):
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, preserve_sensor_log=False):
        self.stop_minigame_heartbeat()
        # Firmware/light editor elott a portot ideiglenesen engedjuk el: a
        # firmware streamet megallitjuk, de ugyanazt a CSV-t ujranyitas utan
        # folytatjuk. A normal stop alkalmazasleallas, ott flush/close kell.
        if self.sensor_logging_active and preserve_sensor_log:
            self._sensor_log_paused_for_port_release = bool(preserve_sensor_log)
            with self._sensor_log_lock:
                self._sensor_log_acknowledged = False
                self._sensor_log_start_next_retry = 0.0
            self.send_line("SENSOR_LOG,STOP")
        elif self.sensor_logging_active:
            self.stop_sensor_logging()
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _resolve_port(self):
        """A legutobb elmentett (firmware_update.py vagy a szerviz menu
        "Arduino keresese" pontja altal detektalt) portot hasznalja, ha van
        ilyen - kulonben a konstruktorban kapott alapertelmezettre esik
        vissza. Csak egy kis JSON fajlt olvas be, NEM hiv arduino-cli-t -
        azt csak a ket fenti, deliberalt/ritka eset teszi, kulonben feleslegesen
        futna masodpercenkent akkor is, ha nincs Arduino csatlakoztatva."""
        saved = arduino_port.load_saved_port()
        return saved if saved else self.port

    def _run(self):
        while not self._stop_flag.is_set():
            active_port = self._resolve_port()
            try:
                with serial.Serial(active_port, self.baudrate, timeout=0.1) as ser:
                    print(f"[serial] csatlakozva: {active_port}")
                    self._ser = ser
                    with self._sensor_log_lock:
                        if self._sensor_log_desired_active:
                            self._sensor_log_acknowledged = False
                            self._sensor_log_start_next_retry = 0.0
                        elif self._sensor_log_stop_pending:
                            self._sensor_log_stop_next_retry = 0.0
                    self._service_sensor_log_start(time.monotonic())
                    self._service_sensor_log_stop(time.monotonic())
                    fast_empty = 0
                    while not self._stop_flag.is_set():
                        self._service_minigame_heartbeat(time.monotonic())
                        self._service_sensor_log_start(time.monotonic())
                        self._service_sensor_log_stop(time.monotonic())
                        t0 = time.monotonic()
                        raw = ser.readline()
                        if not raw:
                            # Ures olvasas: normal esetben a timeout (~1s) utan
                            # jon. Ha viszont AZONNAL ures, a device ujra-
                            # enumeralodott (pl. Arduino reset/ujrachatlakozas)
                            # es a regi fd HALOTT - orokre nema maradna,
                            # kivetel nelkul! Ilyenkor ujracsatlakozunk.
                            if time.monotonic() - t0 < 0.05:
                                fast_empty += 1
                                if fast_empty > 20:
                                    raise serial.SerialException(
                                        "halott fd (azonnali ures olvasasok) - ujracsatlakozas"
                                    )
                            else:
                                fast_empty = 0
                            continue  # timeout, nincs adat
                        fast_empty = 0
                        try:
                            line = raw.decode("utf-8", errors="replace")
                        except Exception:
                            continue
                        self.raw_log.append((time.time(), line.strip()))
                        self._process_line(line)
            except (serial.SerialException, OSError) as e:
                # Arduino kihuzva / reset / USB hiba - varunk es ujraprobalkozunk.
                # (OSError is: a pyserial nehany hibaútja nyers OSError-t dob,
                # ami korabban csendben megolte ezt a szalat.)
                print(f"[serial] hiba: {e}, ujracsatlakozas 2s mulva")
                self.raw_log.append((time.time(), f"[HIBA] {e}"))
                self._stop_flag.wait(2)
            finally:
                self._ser = None

    def send_raw(self, text: str) -> bool:
        """Nyers byte-szöveg küldése; a hívó adja meg a sorvéget is.

        Az új firmware egyetlen nem blokkoló, újsoros parsert használ, ezért
        parancshoz általában a :meth:`send_line` való.
        """
        ser = self._ser
        if ser is None:
            print(f"[serial] send_raw('{text}') kihagyva - nincs elo kapcsolat")
            return False
        try:
            with self._write_lock:
                ser.write(text.encode("utf-8"))
                ser.flush()
            display_text = text.rstrip("\r\n")
            print(f"[serial] kuldve: {display_text}")
            self.raw_log.append((time.time(), f"[KULDVE] {display_text}"))
            return True
        except (serial.SerialException, OSError) as e:
            print(f"[serial] send_raw hiba: {e}")
            return False

    def send_line(self, text: str) -> bool:
        """Ujsoros, nem blokkolo firmware-protokoll parancs kuldese.

        A regi hiscore ``Exit`` uzenetek miatt a :meth:`send_raw` viselkedeset
        nem valtoztatjuk meg. Az MG_* protokoll viszont mindig sorhataros, igy
        az Arduino karakterenkenti parserenek nem kell timeoutra varnia.
        """
        return self.send_raw(text.rstrip("\r\n") + "\n")

    @property
    def sensor_logging_active(self):
        with self._sensor_log_lock:
            return self._sensor_log_desired_active

    @property
    def sensor_log_path(self):
        with self._sensor_log_lock:
            return self._sensor_log_path

    def start_sensor_logging(self):
        """CSV megnyitasa es a firmware telemetriajanak bekapcsolasa.

        A fajlt a parancs elott nyitjuk meg, igy a legelso meresi sor sem
        veszhet el. Sikertelen soros kuldesnel az ures fajlt bezarjuk es a
        hivo ``None``-t kap.
        """
        with self._sensor_log_lock:
            if self._sensor_log_desired_active and self._sensor_log_file is not None:
                return self._sensor_log_path
            try:
                os.makedirs(self.diagnostic_log_dir, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                path = os.path.join(
                    self.diagnostic_log_dir, f"sensor_log_{stamp}.csv"
                )
                handle = open(path, "w", newline="", encoding="utf-8")
            except OSError as exc:
                self.sensor_log_last_error = str(exc)
                return None
            self._sensor_log_file = handle
            self._sensor_log_writer = csv.writer(handle)
            self._sensor_log_header_written = False
            self._sensor_log_names = self.SENSOR_LOG_FALLBACK_NAMES
            self._sensor_log_extra_names = self.SENSOR_LOG_FALLBACK_EXTRA_NAMES
            self._sensor_log_path = path
            self.sensor_log_last_error = ""
            self._sensor_log_desired_active = True
            self._sensor_log_paused_for_port_release = False
            self._sensor_log_acknowledged = False
            self._sensor_log_start_next_retry = 0.0
            # Gyors OFF -> ON eseten a regi STOP ACK meg johet, de a kivant
            # allapot mar ON; az ACK kezelo ilyenkor uj START ciklust indit.
            self._sensor_log_stop_pending = False
            self._sensor_log_stop_next_retry = 0.0

        # Ha epp ujracsatlakozik a port, a keres nyitva marad: a hatterszal
        # 750 ms-onkent probalja, amig STARTED ACK nem erkezik.
        self._service_sensor_log_start(time.monotonic())
        return path

    def stop_sensor_logging(self):
        """Firmware stream leallitasa, majd a CSV flush/close."""
        if not self.sensor_logging_active:
            return self.last_sensor_log_path
        with self._sensor_log_lock:
            self._sensor_log_desired_active = False
            self._sensor_log_paused_for_port_release = False
            self._sensor_log_acknowledged = False
            self._sensor_log_start_next_retry = 0.0
            self._sensor_log_stop_pending = True
            self._sensor_log_stop_next_retry = 0.0
        path = self._close_sensor_log()
        self._service_sensor_log_stop(time.monotonic())
        return path

    def _service_sensor_log_start(self, now):
        """Idempotens START retry, amig a firmware STARTED ACK-ja megjon."""
        if self._stop_flag.is_set():
            return False
        with self._sensor_log_lock:
            if (self._sensor_log_file is None
                    or not self._sensor_log_desired_active
                    or self._sensor_log_acknowledged
                    or now < self._sensor_log_start_next_retry):
                return False
            self._sensor_log_start_next_retry = (
                now + self.SENSOR_LOG_START_RETRY_SEC
            )
        sent = self.send_line("SENSOR_LOG,START")
        if not sent:
            self.sensor_log_last_error = "varakozas a soros kapcsolatra"
        return sent

    def _service_sensor_log_stop(self, now):
        """Idempotens STOP retry, amig a firmware STOPPED ACK-ja megjon."""
        if self._stop_flag.is_set():
            return False
        with self._sensor_log_lock:
            if (self._sensor_log_desired_active
                    or not self._sensor_log_stop_pending
                    or now < self._sensor_log_stop_next_retry):
                return False
            self._sensor_log_stop_next_retry = (
                now + self.SENSOR_LOG_STOP_RETRY_SEC
            )
        sent = self.send_line("SENSOR_LOG,STOP")
        if not sent:
            self.sensor_log_last_error = "STOP varakozik a soros kapcsolatra"
        return sent

    def _write_sensor_log_header_locked(self):
        if self._sensor_log_header_written or self._sensor_log_writer is None:
            return
        names = self._sensor_log_names
        if len(names) != 6:
            names = self.SENSOR_LOG_FALLBACK_NAMES
        safe_names = []
        for index, name in enumerate(names, 1):
            safe = re.sub(r"[^0-9A-Za-z_]+", "_", name.strip()).strip("_")
            safe_names.append(safe or f"sensor_{index}")
        self._sensor_log_writer.writerow([
            "host_time_iso",
            "host_unix_ms",
            "firmware_millis",
            *(f"adc_median7_{name}" for name in safe_names),
            "stable_mask",
            *(f"stable_{name}" for name in safe_names[
                :self.SENSOR_LOG_STABLE_TROUGH_BITS
            ]),
            "trusted_bis",
            "raw_bis",
            "state_flags",
            *self.SENSOR_LOG_FLAG_NAMES,
            *self._sensor_log_extra_names,
        ])
        self._sensor_log_file.flush()
        self._sensor_log_header_written = True

    def _record_sensor_data(self, event):
        with self._sensor_log_lock:
            if self._sensor_log_file is None:
                return
            self._write_sensor_log_header_locked()
            (firmware_millis, raw_values, stable_mask, trusted_bis,
             raw_bis, state_flags, trailing_values) = event.args
            expected_extra_count = len(self._sensor_log_extra_names)
            if len(trailing_values) != expected_extra_count:
                self.sensor_log_last_error = (
                    "telemetria mezoeltérés: "
                    f"{len(trailing_values)} != {expected_extra_count}"
                )
                return
            now = time.time()
            self._sensor_log_writer.writerow([
                datetime.fromtimestamp(now).astimezone().isoformat(
                    timespec="milliseconds"
                ),
                round(now * 1000),
                firmware_millis,
                *raw_values,
                stable_mask,
                *(1 if stable_mask & (1 << bit) else 0
                  for bit in range(self.SENSOR_LOG_STABLE_TROUGH_BITS)),
                trusted_bis,
                raw_bis,
                state_flags,
                *(1 if state_flags & (1 << bit) else 0
                  for bit in range(len(self.SENSOR_LOG_FLAG_NAMES))),
                *trailing_values,
            ])
            # Egy gepfagyas/aramvesztes se vigye magaval az egesz tesztkort.
            self._sensor_log_file.flush()

    def _close_sensor_log(self, remember_path=True):
        with self._sensor_log_lock:
            if self._sensor_log_file is None:
                return self.last_sensor_log_path
            path = self._sensor_log_path
            if not self._sensor_log_header_written:
                self._write_sensor_log_header_locked()
            try:
                self._sensor_log_file.flush()
                self._sensor_log_file.close()
            except OSError as exc:
                self.sensor_log_last_error = str(exc)
            self._sensor_log_file = None
            self._sensor_log_writer = None
            self._sensor_log_header_written = False
            self._sensor_log_path = None
            if remember_path:
                self.last_sensor_log_path = path
            return path

    def _handle_sensor_log_event(self, event):
        """True, ha az esemenyt nem kell a fo GUI-queue-ba tovabbitani."""
        if event.kind == "SENSOR_LOG_STARTED":
            fields = tuple(event.args[0]) if event.args else ()
            # A vegleges firmware-fejlec a teljes wire formatot nevezi meg:
            # ms,a0..a5,stableMask,trustedBIS,rawBIS,stateFlags utan a
            # firmware altal elnevezett extra diagnosztikai mezok jonnek.
            names = fields[1:7]
            extra_names = fields[11:]
            with self._sensor_log_lock:
                desired_active = self._sensor_log_desired_active
                if (desired_active and self._sensor_log_file is not None
                        and not self._sensor_log_header_written):
                    self._sensor_log_names = (
                        names if len(names) == 6 else self.SENSOR_LOG_FALLBACK_NAMES
                    )
                    self._sensor_log_extra_names = extra_names
                if desired_active:
                    self._sensor_log_acknowledged = self._sensor_log_file is not None
                    self._sensor_log_start_next_retry = 0.0
                    self._sensor_log_paused_for_port_release = False
                    self.sensor_log_last_error = ""
                else:
                    # Keson beerkezo STARTED egy gyors ON -> OFF utan. A
                    # desired state OFF, tehat tovabbra is STOP-ot kerunk.
                    self._sensor_log_stop_pending = True
                    self._sensor_log_stop_next_retry = 0.0
            if not desired_active:
                self._service_sensor_log_stop(time.monotonic())
                return True
            return False
        if event.kind == "SENSOR_DATA":
            self._record_sensor_data(event)
            with self._sensor_log_lock:
                desired_active = self._sensor_log_desired_active
                if not desired_active:
                    self._sensor_log_stop_pending = True
                    self._sensor_log_stop_next_retry = 0.0
            if not desired_active:
                self._service_sensor_log_stop(time.monotonic())
            return True
        if (event.kind == "SENSOR_LOG_STOPPED"
                and self._sensor_log_paused_for_port_release):
            # Belső, ideiglenes portelengedes: a CSV nyitva marad, es a
            # statusz sem allitja OFF-ra a felhasznalo altal kert naplozast.
            return True
        if event.kind == "SENSOR_LOG_STOPPED":
            with self._sensor_log_lock:
                desired_active = self._sensor_log_desired_active
                self._sensor_log_stop_pending = False
                self._sensor_log_stop_next_retry = 0.0
                if desired_active:
                    # Kesoi STOPPED egy gyors OFF -> ON utan: ON a kivant
                    # allapot, ezert uj START kell ACK-ig.
                    self._sensor_log_acknowledged = False
                    self._sensor_log_start_next_retry = 0.0
            if desired_active:
                self._service_sensor_log_start(time.monotonic())
                return True
            return False
        if event.kind == "SENSOR_LOG_ERROR":
            # A firmware sajat maga is leallithatja a streamet (pl. protokoll-
            # hiba); ilyenkor se maradjon nyitva egy felig kiirt fajl.
            with self._sensor_log_lock:
                self._sensor_log_desired_active = False
                self._sensor_log_acknowledged = False
                self._sensor_log_stop_pending = False
            self._close_sensor_log()
            return False
        return False

    def _process_line(self, line):
        event = parse_line(line)
        if event is None:
            return
        if self._handle_sensor_log_event(event):
            return
        self.event_queue.put(event)

    def start_minigame_heartbeat(self, session: int):
        """A renderlooptol fuggetlen tartalek-heartbeat bekapcsolasa."""
        with self._heartbeat_lock:
            self._minigame_heartbeat_session = int(session)
            self._minigame_heartbeat_next = (
                time.monotonic() + self.MINIGAME_HEARTBEAT_SEC
            )

    def stop_minigame_heartbeat(self):
        with self._heartbeat_lock:
            self._minigame_heartbeat_session = None
            self._minigame_heartbeat_next = 0.0

    def _service_minigame_heartbeat(self, now: float):
        with self._heartbeat_lock:
            session = self._minigame_heartbeat_session
            if session is None or now < self._minigame_heartbeat_next:
                return
            self._minigame_heartbeat_next = now + self.MINIGAME_HEARTBEAT_SEC
        self.send_line(f"MG_ALIVE,{session}")

    def poll_events(self):
        """Nem-blokkoló: visszaadja az összes várakozó eventet."""
        events = []
        while True:
            try:
                events.append(self.event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def get_raw_log(self):
        """A legutobbi nyers sorok masolata (legrégebbi elöl), a szerviz
        menu Serial Monitor kepernyojehez."""
        return list(self.raw_log)
