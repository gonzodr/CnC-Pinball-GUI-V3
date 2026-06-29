"""Soros port olvasása külön szálon, nem-blokkoló módon a fő loop felé."""

import serial
import threading
import queue
from protocol import parse_line, GameEvent


class SerialReader:
    """
    Külön szálon olvassa a soros portot, és a feldolgozott
    GameEvent-eket egy thread-safe queue-ba teszi.

    Külön szálon kell futnia, mert a soros olvasás blokkoló,
    és nem akarjuk, hogy ez lefagyassza a GUI/video render loopot.
    """

    def __init__(self, port: str, baudrate: int = 115200):
        self.port = port
        self.baudrate = baudrate
        self.event_queue: "queue.Queue[GameEvent]" = queue.Queue()
        self._stop_flag = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_flag.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop_flag.is_set():
            try:
                with serial.Serial(self.port, self.baudrate, timeout=1) as ser:
                    print(f"[serial] csatlakozva: {self.port}")
                    while not self._stop_flag.is_set():
                        raw = ser.readline()
                        if not raw:
                            continue  # timeout, nincs adat
                        try:
                            line = raw.decode("utf-8", errors="replace")
                        except Exception:
                            continue
                        event = parse_line(line)
                        if event:
                            self.event_queue.put(event)
            except serial.SerialException as e:
                # Teensy kihúzva / USB hiba — várunk és próbálkozunk újra
                print(f"[serial] hiba: {e}, ujracsatlakozas 2s mulva")
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
