import sys
import unittest
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from protocol import GameEvent, parse_line
from state_machine import StateMachine
from state_machine import AppState


class FakeSerial:
    def __init__(self):
        self.lines = []

    def send_line(self, line):
        self.lines.append(line)
        return True


class FakePngPlayer:
    def __init__(self):
        self.clip_names = (
            "UfoWheel_ExtraBall",
            "UfoWheel_HurryUp",
            "UfoWheel_Munchies",
            "Extraball",
            "HURRY_UP_MASTER",
        )
        self.started = []
        self.finished = False

    def start(self, name):
        self.started.append(name)
        self.finished = False
        return name in self.clip_names

    def update(self):
        pass


class PartyProtocolTests(unittest.TestCase):
    def test_party_state_protocol(self):
        self.assertEqual(
            parse_line("Party,2,3,2,3,1"),
            GameEvent("PARTY_STATE", (2, 3, 2, 3, True)),
        )

    def test_party_state_rejects_out_of_range_values(self):
        for line in (
            "Party,0,3,2,3,1",
            "Party,1,4,2,3,1",
            "Party,1,3,2,5,1",
            "Party,1,3,2,3,2",
        ):
            with self.subTest(line=line):
                self.assertIsNone(parse_line(line))

    def test_party_event_protocol(self):
        self.assertEqual(
            parse_line("PartyEvent,1,LOVE_PACK"),
            GameEvent("PARTY_EVENT", (1, "LOVE_PACK")),
        )

    def test_ufo_wheel_protocol(self):
        self.assertEqual(
            parse_line("WheelStart,42,HurryUp"),
            GameEvent("UFO_WHEEL_START", (42, "HURRYUP")),
        )
        self.assertIsNone(parse_line("WheelStart,0,Munchies"))
        self.assertIsNone(parse_line("WheelStart,42,Unknown"))

    def test_ufo_wheel_finishes_before_firmware_reward(self):
        serial = FakeSerial()
        player = FakePngPlayer()
        state = StateMachine(serial_reader=serial)
        state.state = AppState.SCORE
        state.png_video_player = player

        event = GameEvent("UFO_WHEEL_START", (7, "MUNCHIES"))
        state.handle_event(event)
        state.handle_event(event)  # firmware retry: must not restart the clip
        self.assertEqual(player.started, ["UfoWheel_Munchies"])
        self.assertEqual(serial.lines, [])

        player.finished = True
        state.tick()
        self.assertEqual(serial.lines, ["WHEEL_DONE,7"])
        self.assertEqual(state.state, AppState.SCORE)

        # If the DONE was lost, the next repeated START only repeats DONE.
        state.handle_event(event)
        self.assertEqual(player.started, ["UfoWheel_Munchies"])
        self.assertEqual(serial.lines, ["WHEEL_DONE,7", "WHEEL_DONE,7"])

    def test_ufo_wheel_chains_result_video_before_done(self):
        for result, wheel, outcome in (
            ("EXTRABALL", "UfoWheel_ExtraBall", "Extraball"),
            ("HURRYUP", "UfoWheel_HurryUp", "HURRY_UP_MASTER"),
        ):
            with self.subTest(result=result):
                serial = FakeSerial()
                player = FakePngPlayer()
                state = StateMachine(serial_reader=serial)
                state.state = AppState.SCORE
                state.png_video_player = player

                state.handle_event(GameEvent("UFO_WHEEL_START", (8, result)))
                self.assertEqual(player.started, [wheel])

                player.finished = True
                state.tick()
                self.assertEqual(player.started, [wheel, outcome])
                self.assertEqual(serial.lines, ["WHEEL_DONE,8"])

                player.finished = True
                state.tick()
                self.assertEqual(serial.lines, ["WHEEL_DONE,8"])

    def test_super_cashout_steal_protocol_prevents_double_subtraction(self):
        self.assertEqual(
            parse_line("Steal,2,20000"),
            GameEvent("SCORE_STEAL", (2, 20000)),
        )
        state = StateMachine()
        state.players[2] = 50000
        state.handle_event(GameEvent("SCORE_STEAL", (2, 20000)))
        state.handle_event(GameEvent("VIDEO", ("Ufo11",)))
        self.assertEqual(state.players[2], 30000)

    def test_legacy_steal_video_still_subtracts_ten_thousand(self):
        state = StateMachine()
        state.players[2] = 50000
        state.handle_event(GameEvent("VIDEO", ("Ufo11",)))
        self.assertEqual(state.players[2], 40000)

    def test_state_machine_keeps_progress_per_player(self):
        state = StateMachine()
        state.handle_event(GameEvent("PARTY_STATE", (1, 2, 1, 2, False)))
        state.handle_event(GameEvent("PARTY_STATE", (2, 1, 2, 3, True)))

        self.assertEqual(state.party_progress[1], {
            "beers": 2, "joints": 1, "ufo_tier": 2, "weed_ready": False
        })
        self.assertEqual(state.party_progress[2], {
            "beers": 1, "joints": 2, "ufo_tier": 3, "weed_ready": True
        })

    def test_love_pack_message_is_explicit(self):
        state = StateMachine()
        state.handle_event(GameEvent("PARTY_EVENT", (1, "LOVE_PACK")))
        self.assertEqual(
            state.party_message,
            "LOVE PACK! SHOOT THE UFO FOR COKE!",
        )
        self.assertGreater(state.party_message_until, 0)


if __name__ == "__main__":
    unittest.main()
