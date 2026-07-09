"""Soros port olvasása külön szálon, nem-blokkoló módon a fő loop felé."""

import serial
import threading
import queue
import time
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

    def __init__(self, port: str, baudrate: int = 115200):
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

    def start(self):
        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
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
                with serial.Serial(active_port, self.baudrate, timeout=1) as ser:
                    print(f"[serial] csatlakozva: {active_port}")
                    while not self._stop_flag.is_set():
                        raw = ser.readline()
                        if not raw:
                            continue  # timeout, nincs adat
                        try:
                            line = raw.decode("utf-8", errors="replace")
                        except Exception:
                            continue
                        self.raw_log.append((time.time(), line.strip()))
                        event = parse_line(line)
                        if event:
                            self.event_queue.put(event)
            except serial.SerialException as e:
                # Teensy kihúzva / USB hiba — várunk és próbálkozunk újra
                print(f"[serial] hiba: {e}, ujracsatlakozas 2s mulva")
                self.raw_log.append((time.time(), f"[HIBA] {e}"))
                self._stop_flag.wait(2)

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
