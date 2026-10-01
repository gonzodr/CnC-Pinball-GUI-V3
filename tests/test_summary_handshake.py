import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from state_machine import AppState, StateMachine
from protocol import parse_line


class _SerialCapture:
    def __init__(self):
        self.sent = []

    def send_line(self, text):
        self.sent.append(text)
        return True


class SummaryHandshakeTests(unittest.TestCase):
    def test_next_summary_switches_to_score_before_releasing_firmware(self):
        machine = object.__new__(StateMachine)
        machine.state = AppState.SUMMARY
        machine.serial_reader = _SerialCapture()
        machine._pending_highscore_check = None
        machine._pending_game_over = False
        machine._summary_done_pending = False
        machine._summary_session = 42

        machine._resolve_after_summary()

        self.assertIs(machine.state, AppState.SCORE)
        self.assertEqual(machine.serial_reader.sent, ["SUMMARY_DONE,42"])
        self.assertTrue(machine._summary_done_pending)

        machine.handle_event(parse_line("SUMMARY_ACK,42"))
        self.assertFalse(machine._summary_done_pending)

    def test_firmware_summary_status_is_not_misread_as_video(self):
        self.assertEqual(parse_line("SUMMARY_ACK,42").kind, "SUMMARY_ACK")
        self.assertEqual(parse_line("SUMMARY_ACK,42").args, (42,))
        self.assertEqual(parse_line("SUMMARY_TIMEOUT,42").kind, "SUMMARY_TIMEOUT")

    def test_next_carries_summary_session(self):
        event = parse_line("Next,42")
        self.assertEqual(event.kind, "NEXT")
        self.assertEqual(event.args, (42,))


if __name__ == "__main__":
    unittest.main()
