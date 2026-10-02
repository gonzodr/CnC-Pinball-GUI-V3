"""Sensor telemetry protocol, background CSV logging and service toggle."""

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from protocol import GameEvent, parse_line
from serial_reader import SerialReader
from service_menu import ServiceMenuController


class FakeSerialPort:
    def __init__(self):
        self.writes = []

    def write(self, payload):
        self.writes.append(payload)

    def flush(self):
        pass


class FakeLoggingReader:
    def __init__(self, path):
        self.path = path
        self.sensor_logging_active = False
        self.sensor_log_last_error = ""
        self.last_sensor_log_path = None
        self.starts = 0
        self.stops = 0

    def start_sensor_logging(self):
        self.starts += 1
        self.sensor_logging_active = True
        return self.path

    def stop_sensor_logging(self):
        self.stops += 1
        self.sensor_logging_active = False
        self.last_sensor_log_path = self.path
        return self.path


class SensorProtocolTests(unittest.TestCase):
    def test_started_data_and_stopped_are_dedicated_events(self):
        header = (
            "SENSOR_LOG,STARTED,ms,a0,a1,a2,a3,a4,a5,stableMask,"
            "trustedBIS,rawBIS,stateFlags,bip,intmon,firstplay,sidelaneSave,"
            "arrivalArmed,faultCode,observedCount,feedSeq,lastFeedReason"
        )
        self.assertEqual(
            parse_line(header),
            GameEvent("SENSOR_LOG_STARTED", ((
                "ms", "a0", "a1", "a2", "a3", "a4", "a5",
                "stableMask", "trustedBIS", "rawBIS", "stateFlags",
                "bip", "intmon", "firstplay", "sidelaneSave",
                "arrivalArmed", "faultCode", "observedCount", "feedSeq",
                "lastFeedReason",
            ),)),
        )
        self.assertEqual(
            parse_line(
                "SENSOR_DATA,1234,0,1,997,998,2,996,3,4,5,165,"
                "5,6,1,0,1,2,4,33,3"
            ),
            GameEvent("SENSOR_DATA", (
                1234, (0, 1, 997, 998, 2, 996), 3, 4, 5, 165,
                (5, 6, 1, 0, 1, 2, 4, 33, 3),
            )),
        )
        self.assertEqual(
            parse_line("SENSOR_LOG,STOPPED"),
            GameEvent("SENSOR_LOG_STOPPED"),
        )

    def test_malformed_sensor_lines_never_become_video_events(self):
        self.assertIsNone(parse_line("SENSOR_DATA"))
        self.assertIsNone(parse_line("SENSOR_LOG"))
        self.assertIsNone(parse_line("SENSOR_DATA,broken"))


class SensorCsvTests(unittest.TestCase):
    def test_data_is_written_on_reader_thread_path_and_not_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = SerialReader("unused", diagnostic_log_dir=tmp)
            port = FakeSerialPort()
            reader._ser = port

            path = reader.start_sensor_logging()
            self.assertIsNotNone(path)
            self.assertEqual(port.writes, [b"SENSOR_LOG,START\n"])

            reader._process_line(
                "SENSOR_LOG,STARTED,ms,a0,a1,a2,a3,a4,a5,stableMask,"
                "trustedBIS,rawBIS,stateFlags,bip,intmon,firstplay,"
                "sidelaneSave,arrivalArmed,faultCode,observedCount,feedSeq,"
                "lastFeedReason"
            )
            reader._process_line(
                "SENSOR_DATA,1234,0,1,997,998,2,996,3,4,5,165,"
                "5,6,1,0,1,2,4,33,3"
            )

            queued = reader.poll_events()
            self.assertEqual(
                queued,
                [GameEvent("SENSOR_LOG_STARTED", ((
                    "ms", "a0", "a1", "a2", "a3", "a4", "a5",
                    "stableMask", "trustedBIS", "rawBIS", "stateFlags",
                    "bip", "intmon", "firstplay", "sidelaneSave",
                    "arrivalArmed", "faultCode", "observedCount", "feedSeq",
                    "lastFeedReason",
                ),))],
            )
            saved = reader.stop_sensor_logging()
            self.assertEqual(saved, path)
            self.assertEqual(
                port.writes,
                [b"SENSOR_LOG,START\n", b"SENSOR_LOG,STOP\n"],
            )

            with open(path, newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 1)
            row = rows[0]
            self.assertEqual(row["firmware_millis"], "1234")
            self.assertEqual(row["adc_median7_a0"], "0")
            self.assertEqual(row["adc_median7_a5"], "996")
            self.assertEqual(row["stable_mask"], "3")
            self.assertEqual(row["stable_a0"], "1")
            self.assertEqual(row["stable_a1"], "1")
            self.assertEqual(row["stable_a2"], "0")
            self.assertNotIn("stable_a5", row)
            self.assertEqual(row["trusted_bis"], "4")
            self.assertEqual(row["raw_bis"], "5")
            self.assertEqual(row["state_flags"], "165")
            self.assertEqual(row["measurement_inhibited"], "1")
            self.assertEqual(row["shoot"], "0")
            self.assertEqual(row["kick"], "1")
            self.assertEqual(row["ballsave"], "1")
            self.assertEqual(row["drain_armed"], "1")
            self.assertEqual(row["bip"], "5")
            self.assertEqual(row["intmon"], "6")
            self.assertEqual(row["arrivalArmed"], "1")
            self.assertEqual(row["faultCode"], "2")
            self.assertEqual(row["observedCount"], "4")
            self.assertEqual(row["feedSeq"], "33")
            self.assertEqual(row["lastFeedReason"], "3")

    def test_fallback_header_is_valid_if_data_arrives_before_started_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = SerialReader("unused", diagnostic_log_dir=tmp)
            reader._ser = FakeSerialPort()
            path = reader.start_sensor_logging()
            reader._process_line(
                "SENSOR_DATA,7,1,2,3,4,5,6,0,5,5,0,"
                "0,0,0,0,0,0,5,1,0"
            )
            reader.stop_sensor_logging()

            with open(path, newline="", encoding="utf-8") as handle:
                header = next(csv.reader(handle))
            self.assertIn("adc_median7_sensor_1", header)
            self.assertIn("adc_median7_sensor_6", header)
            self.assertNotIn("stable_sensor_6", header)

    def test_temporary_port_release_keeps_same_log_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = SerialReader("unused", diagnostic_log_dir=tmp)
            port = FakeSerialPort()
            reader._ser = port
            path = reader.start_sensor_logging()

            reader.stop(preserve_sensor_log=True)
            self.assertTrue(reader.sensor_logging_active)
            self.assertEqual(reader.sensor_log_path, path)

            # A pause miatt kert STOPPED nyugtazas belso esemeny: nem zarja
            # le a fajlt es nem szennyezi a GUI queue-jat.
            reader._process_line("SENSOR_LOG,STOPPED")
            self.assertTrue(reader.sensor_logging_active)
            self.assertEqual(reader.poll_events(), [])

            reader.stop_sensor_logging()
            self.assertFalse(reader.sensor_logging_active)

    def test_start_retries_until_ack_and_can_rearm_after_reconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = SerialReader("unused", diagnostic_log_dir=tmp)
            port = FakeSerialPort()
            reader._ser = port
            reader.start_sensor_logging()
            self.assertEqual(port.writes, [b"SENSOR_LOG,START\n"])

            reader._sensor_log_start_next_retry = 10.0
            reader._service_sensor_log_start(9.99)
            self.assertEqual(len(port.writes), 1)
            reader._service_sensor_log_start(10.0)
            self.assertEqual(len(port.writes), 2)

            reader._process_line(
                "SENSOR_LOG,STARTED,ms,a0,a1,a2,a3,a4,a5,stableMask,"
                "trustedBIS,rawBIS,stateFlags,bip,intmon,firstplay,"
                "sidelaneSave,arrivalArmed,faultCode,observedCount,feedSeq,"
                "lastFeedReason"
            )
            reader._service_sensor_log_start(999.0)
            self.assertEqual(len(port.writes), 2)

            # Ujracsatlakozaskor a reader uj ACK-ciklust kezd.
            with reader._sensor_log_lock:
                reader._sensor_log_acknowledged = False
                reader._sensor_log_start_next_retry = 0.0
            reader._service_sensor_log_start(1000.0)
            self.assertEqual(len(port.writes), 3)
            reader.stop_sensor_logging()

    def test_start_request_survives_initially_missing_serial_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = SerialReader("unused", diagnostic_log_dir=tmp)
            path = reader.start_sensor_logging()
            self.assertIsNotNone(path)
            self.assertTrue(reader.sensor_logging_active)
            self.assertIn("varakozas", reader.sensor_log_last_error)

            port = FakeSerialPort()
            reader._ser = port
            reader._sensor_log_start_next_retry = 0.0
            reader._service_sensor_log_start(100.0)
            self.assertEqual(port.writes, [b"SENSOR_LOG,START\n"])
            reader.stop_sensor_logging()

    def test_stop_retries_until_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = SerialReader("unused", diagnostic_log_dir=tmp)
            port = FakeSerialPort()
            reader._ser = port
            reader.start_sensor_logging()
            reader.stop_sensor_logging()
            self.assertEqual(
                port.writes,
                [b"SENSOR_LOG,START\n", b"SENSOR_LOG,STOP\n"],
            )

            reader._sensor_log_stop_next_retry = 10.0
            reader._service_sensor_log_stop(9.99)
            self.assertEqual(len(port.writes), 2)
            reader._service_sensor_log_stop(10.0)
            self.assertEqual(len(port.writes), 3)

            reader._process_line("SENSOR_LOG,STOPPED")
            self.assertEqual(
                reader.poll_events(), [GameEvent("SENSOR_LOG_STOPPED")]
            )
            reader._service_sensor_log_stop(999.0)
            self.assertEqual(len(port.writes), 3)

    def test_late_stop_ack_cannot_override_new_on_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = SerialReader("unused", diagnostic_log_dir=tmp)
            port = FakeSerialPort()
            reader._ser = port
            first_path = reader.start_sensor_logging()
            reader.stop_sensor_logging()
            second_path = reader.start_sensor_logging()
            self.assertNotEqual(first_path, second_path)

            reader._process_line("SENSOR_LOG,STOPPED")
            self.assertTrue(reader.sensor_logging_active)
            self.assertEqual(reader.poll_events(), [])
            # A kesoi STOPPED utan azonnal uj START indul, majd ACK-ig retry.
            self.assertEqual(port.writes[-1], b"SENSOR_LOG,START\n")
            reader.stop_sensor_logging()

    def test_late_start_ack_after_off_reissues_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            reader = SerialReader("unused", diagnostic_log_dir=tmp)
            port = FakeSerialPort()
            reader._ser = port
            reader.start_sensor_logging()
            reader.stop_sensor_logging()

            reader._process_line(
                "SENSOR_LOG,STARTED,ms,a0,a1,a2,a3,a4,a5,stableMask,"
                "trustedBIS,rawBIS,stateFlags,bip,intmon,firstplay,"
                "sidelaneSave,arrivalArmed,faultCode,observedCount,feedSeq,"
                "lastFeedReason"
            )
            self.assertFalse(reader.sensor_logging_active)
            self.assertEqual(reader.poll_events(), [])
            self.assertEqual(port.writes[-1], b"SENSOR_LOG,STOP\n")


class SensorServiceMenuTests(unittest.TestCase):
    def test_diagnostics_action_toggles_logging_without_leaving_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sensor_log.csv")
            reader = FakeLoggingReader(path)
            menu = object.__new__(ServiceMenuController)
            menu.serial_reader = reader
            menu.screen = "diagnostics"
            menu.status_message = ""
            menu.cursor = next(
                index for index, (target, _label)
                in enumerate(menu.DIAGNOSTIC_ITEMS)
                if target == "sensor_logging"
            )

            menu._activate_diagnostic_item()
            self.assertTrue(reader.sensor_logging_active)
            self.assertEqual(reader.starts, 1)
            self.assertEqual(menu.screen, "diagnostics")
            self.assertIn("ON", dict(menu.get_diagnostic_items())["sensor_logging"])

            menu._activate_diagnostic_item()
            self.assertFalse(reader.sensor_logging_active)
            self.assertEqual(reader.stops, 1)
            self.assertIn("Mentve:", menu.status_message)
            self.assertIn("OFF", dict(menu.get_diagnostic_items())["sensor_logging"])


if __name__ == "__main__":
    unittest.main()
