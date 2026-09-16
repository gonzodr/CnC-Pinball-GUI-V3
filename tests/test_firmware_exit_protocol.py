import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from state_machine import StateMachine


class _SerialCapture:
    def __init__(self):
        self.sent = []

    def send_raw(self, text):
        self.sent.append(text)
        return True


class FirmwareExitProtocolTests(unittest.TestCase):
    def test_hiscore_exit_commands_are_newline_terminated(self):
        machine = object.__new__(StateMachine)
        machine.serial_reader = _SerialCapture()

        machine._send_exit_to_firmware("Exit")
        machine._send_exit_to_firmware("Exit1")

        self.assertEqual(machine.serial_reader.sent, ["Exit\n", "Exit1\n"])


if __name__ == "__main__":
    unittest.main()
