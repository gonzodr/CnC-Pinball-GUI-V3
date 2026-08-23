"""Regression tests for the cabinet-side Munchies serial handshake."""

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from protocol import GameEvent, parse_line
from serial_reader import SerialReader
from service_menu import ServiceMenuController
from state_machine import AppState, StateMachine


class FakeSerialPort:
    def __init__(self):
        self.writes = []

    def write(self, payload):
        self.writes.append(payload)

    def flush(self):
        pass


class FakeReader:
    def __init__(self):
        self.lines = []
        self.heartbeat_sessions = []
        self.heartbeat_stops = 0

    def send_line(self, text):
        self.lines.append(text)
        return True

    def start_minigame_heartbeat(self, session):
        self.heartbeat_sessions.append(session)

    def stop_minigame_heartbeat(self):
        self.heartbeat_stops += 1


class FakeRawReader:
    def __init__(self):
        self.raw = []

    def send_raw(self, text):
        self.raw.append(text)
        return True


class FakeGame:
    def __init__(self):
        self.masks = []
        self.difficulty = None
        self.activated = False

    def set_hardware_input(self, mask):
        self.masks.append(mask)

    def set_difficulty(self, value):
        self.difficulty = value

    def activate(self):
        self.activated = True


class FakeSettings:
    def get_difficulty(self, _name):
        return 2


class FakeMpv:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class ProtocolParserTests(unittest.TestCase):
    def test_session_messages(self):
        self.assertEqual(parse_line("MG_START,42"), GameEvent("MUNCHIES_START", (42,)))
        self.assertEqual(
            parse_line("MG_INPUT,42,65535,7"),
            GameEvent("MUNCHIES_INPUT", (42, 65535, 7)),
        )
        self.assertEqual(parse_line("MG_ACK,42"), GameEvent("MUNCHIES_ACK", (42,)))

    def test_bad_mask_and_unknown_mg_message_are_not_video_triggers(self):
        self.assertIsNone(parse_line("MG_INPUT,42,1,8"))
        self.assertIsNone(parse_line("MG_INPUT,0,1,1"))
        self.assertIsNone(parse_line("MG_INPUT,42,65536,1"))
        self.assertIsNone(parse_line("MG_START,65536"))
        self.assertIsNone(parse_line("MG_NOISE"))

    def test_legacy_trigger_remains_supported(self):
        self.assertEqual(parse_line("MUNCHIES"), GameEvent("MUNCHIES_START"))
        self.assertEqual(parse_line("VUK_GAME"), GameEvent("MUNCHIES_START"))

    def test_atomic_analog_save_ack(self):
        self.assertEqual(parse_line("AT_SAVED"), GameEvent("ANALOG_SAVED"))


class SerialWriterTests(unittest.TestCase):
    def test_send_line_adds_exactly_one_newline(self):
        reader = SerialReader("unused")
        port = FakeSerialPort()
        reader._ser = port
        self.assertTrue(reader.send_line("MG_READY,9\r\n"))
        self.assertEqual(port.writes, [b"MG_READY,9\n"])

    def test_background_heartbeat_is_independent_of_render_loop(self):
        reader = SerialReader("unused")
        port = FakeSerialPort()
        reader._ser = port
        reader.start_minigame_heartbeat(9)
        reader._minigame_heartbeat_next = 0.0
        reader._service_minigame_heartbeat(10.0)
        self.assertEqual(port.writes, [b"MG_ALIVE,9\n"])
        reader.stop_minigame_heartbeat()
        reader._service_minigame_heartbeat(20.0)
        self.assertEqual(port.writes, [b"MG_ALIVE,9\n"])


class AnalogServiceMenuTests(unittest.TestCase):
    def test_adjustments_are_local_until_saves_are_requested(self):
        menu = ServiceMenuController.__new__(ServiceMenuController)
        menu.serial_reader = FakeRawReader()
        menu.screen = "analog_test"
        menu.cursor = 0
        menu.status_message = ""
        menu.analog_thresholds = [90, 90]
        menu.analog_thresholds_saved = [90, 90]
        menu.analog_dirty = False
        menu.analog_save_pending = False
        menu.analog_save_snapshot = None
        menu.analog_save_attempts = 0
        menu.analog_save_next_retry = 0.0
        menu.analog_save_deadline = 0.0
        menu.analog_streaming = True

        menu._adjust_analog_threshold(10)
        self.assertEqual(menu.analog_thresholds, [100, 90])
        self.assertEqual(menu.serial_reader.raw, [])
        self.assertTrue(menu.analog_dirty)

        menu._save_analog_thresholds()
        self.assertEqual(menu.serial_reader.raw, ["AT,SAVE,100,90\n"])
        self.assertTrue(menu.analog_save_pending)

        # Nyugtazas nelkul ujrakuldi ugyanazt az idempotens EEPROM.update
        # csomagot; elveszett WS2812/serial-utkozes utan ez gyogyitja magat.
        menu.analog_save_next_retry = 0.0
        menu.tick(menu.analog_save_deadline - 1.0)
        self.assertEqual(
            menu.serial_reader.raw,
            ["AT,SAVE,100,90\n", "AT,SAVE,100,90\n"],
        )

        menu.handle_analog_event(GameEvent("ANALOG_SAVED"))
        self.assertEqual(
            menu.serial_reader.raw,
            ["AT,SAVE,100,90\n", "AT,SAVE,100,90\n"],
        )
        self.assertFalse(menu.analog_save_pending)
        self.assertFalse(menu.analog_dirty)


class StateMachineProtocolTests(unittest.TestCase):
    @staticmethod
    def make_state():
        state = StateMachine.__new__(StateMachine)
        state.serial_reader = FakeReader()
        state.state = AppState.MINIGAME
        state.minigame = FakeGame()
        state._minigame_session = 7
        state._minigame_last_input_seq = None
        state._minigame_next_heartbeat = 10.0
        state._minigame_pending_done = None
        state.recent_events = []
        return state

    def test_snapshot_sequence_and_session_filtering(self):
        state = self.make_state()
        state.handle_event(GameEvent("MUNCHIES_INPUT", (8, 1, 7)))
        state.handle_event(GameEvent("MUNCHIES_INPUT", (7, 10, 3)))
        state.handle_event(GameEvent("MUNCHIES_INPUT", (7, 10, 5)))
        state.handle_event(GameEvent("MUNCHIES_INPUT", (7, 9, 6)))
        state.handle_event(GameEvent("MUNCHIES_INPUT", (7, 11, 4)))
        self.assertEqual(state.minigame.masks, [3, 4])

    def test_heartbeat_and_done_retry_stop_on_ack(self):
        state = self.make_state()
        state._minigame_next_heartbeat = 0.0
        state._minigame_pending_done = (7, 12345, 20.0, 0.0)
        state._service_minigame_protocol(12.0)
        self.assertEqual(
            state.serial_reader.lines,
            ["MG_ALIVE,7", "MG_DONE,7,12345"],
        )
        state.handle_event(GameEvent("MUNCHIES_ACK", (7,)))
        self.assertIsNone(state._minigame_pending_done)

    def test_start_arms_session_and_pickup_light_messages(self):
        state = self.make_state()
        game = FakeGame()
        state.state = AppState.SCORE
        state.mpv = FakeMpv()
        state._preloaded_minigame = game
        state.minigame = None
        state.minigame_settings = FakeSettings()
        state._in_attract_loop = True
        state._minigame_pending_done = None

        state.handle_event(GameEvent("MUNCHIES_START", (21,)))
        self.assertEqual(state.state, AppState.MINIGAME)
        self.assertEqual(state._minigame_session, 21)
        self.assertTrue(game.activated)
        self.assertEqual(game.difficulty, 2)
        self.assertEqual(
            state.serial_reader.lines,
            ["MG_READY,21", "MG_ALIVE,21"],
        )
        self.assertEqual(state.serial_reader.heartbeat_sessions, [21])

        state._handle_minigame_sound("collect")
        state._handle_minigame_sound("bad_pickup")
        state._handle_minigame_sound("collection_complete")
        self.assertEqual(
            state.serial_reader.lines[2:],
            [
                "MG_PICKUP,21,GOOD",
                "MG_PICKUP,21,BAD",
                "MG_COLLECTION,21",
            ],
        )


if __name__ == "__main__":
    unittest.main()
