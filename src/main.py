"""Cheech & Chong Pinball - Raspberry Pi GUI/video vezerlo.

A teljes szoftver belepesi pontja: osszekoti a soros olvasot,
az allapotgepet, az mpv vezerlot es a pontszam-GUI-t egyetlen
futo event loopba.

FEJLESZTOI MOD: a billentyuzet a MockInputController-en keresztul
ugyanolyan GameEvent-eket general, mint amiket a Teensy kuldene
soros porton - igy a teljes GUI/allapotgep lanc tesztelheto valodi
hardver nelkul. A Teensy csatlakoztatasa utan ez a kod valtozas
nelkul tovabb mukodik, a billentyuzet es a soros port egyszerre
"elhet" egymas mellett.
"""

import time
import sys

from serial_reader import SerialReader
from state_machine import StateMachine, AppState
from mpv_controller import MpvController
from score_gui import ScoreGUI
from mock_input import MockInputController


# --- Konfiguracio ---
SERIAL_PORT = "/dev/ttyACM0"   # ellenorizd a Pi-n: `ls /dev/ttyACM*` vagy `/dev/ttyUSB*`
SERIAL_BAUDRATE = 115200
TARGET_FPS = 30                # 30 FPS bovven eleg egy pontszam-GUI-hoz


def main():
    print("[main] inditas...")

    mpv = MpvController()
    mpv.start()

    state = StateMachine(mpv)

    gui = ScoreGUI()
    gui.acquire_display()   # induláskor SCORE állapotban vagyunk, GUI kapja a kijelzőt

    serial_reader = SerialReader(SERIAL_PORT, SERIAL_BAUDRATE)
    serial_reader.start()

    mock_input = MockInputController()

    clock_interval = 1.0 / TARGET_FPS
    running = True

    try:
        while running:
            loop_start = time.time()

            # 1. Soros parancsok feldolgozasa (nem blokkol, queue-bol olvas)
            #for event in serial_reader.poll_events():
            #    state.handle_event(event)

            # 2. Video-vege detektalas
            state.tick()

            # 3. Pygame esemenyek
            pygame_events = gui.poll_pygame_events()
            if gui.has_quit_event(pygame_events):
                running = False
            for mock_event in mock_input.poll_events(pygame_events):
                state.handle_event(mock_event)

            # 4. Allapotvaltas kezelese
            transition = state.consume_transition()
            if transition:
                old_state, new_state = transition
                print(f"[main] allapotvaltas: {old_state.name} -> {new_state.name}")

                if new_state == AppState.SUMMARY:
                    gui.summary_anim_start = None
               #     gui.release_display()
               #     gui.acquire_display()

                elif new_state == AppState.VIDEO:
                    gui.release_display()
                    mpv.play(state.pending_video)
                    state.pending_video = None
                elif new_state == AppState.SCORE:
                    gui.acquire_display()
                    
            # 5. Rajzolas, ha SCORE allapotban vagyunk
            if state.state == AppState.SCORE:
                gui.render(state)
            elif state.state == AppState.SUMMARY:
                gui.render_summary(state.summary_data)

            # 6. Frame-utemezes tartasa (ne porgessuk feleslegesen a CPU-t)
            elapsed = time.time() - loop_start
            sleep_time = clock_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print("[main] Ctrl+C, leallas...")

    finally:
        print("[main] takaritas...")
        serial_reader.stop()
        gui.release_display()
        mpv.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    main()
