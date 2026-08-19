"""Regression tests for the cabinet-side Munchies serial handshake."""

import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from protocol import GameEvent, parse_line
from serial_reader import SerialReader
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

    def send_line(self, text):
        self.lines.append(text)
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


class SerialWriterTests(unittest.TestCase):
    def test_send_line_adds_exactly_one_newline(self):
        reader = SerialReader("unused")
        port = FakeSerialPort()
        reader._ser = port
        self.assertTrue(reader.send_line("MG_READY,9\r\n"))
        self.assertEqual(port.writes, [b"MG_READY,9\n"])


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
        self.assertEqual(state.serial_reader.lines, ["MG_READY,21"])

        state._handle_minigame_sound("collect")
        state._handle_minigame_sound("bad_pickup")
        state._handle_minigame_sound("collection_complete")
        self.assertEqual(
            state.serial_reader.lines[1:],
            [
                "MG_PICKUP,21,GOOD",
                "MG_PICKUP,21,BAD",
                "MG_COLLECTION,21",
            ],
        )


if __name__ == "__main__":
    unittest.main()
