"""Real cabinet assets/rendering and session smoke; no serial/coil access.

Run with the GUI stopped: venv/bin/python tools/puff_cabinet_smoke.py
Uses dummy audio/video and temporary score files. Does not prove real wiring.
"""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_BLEND_ALPHA_SDL2"] = "1"
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pygame
from game_modes import GAME_ARCADE, ARCADE_PUFF_N_RIFF
from protocol import GameEvent, parse_line
from score_gui import ScoreGUI
from score_manager import ScoreManager
from state_machine import StateMachine, AppState


class Reader:
    def __init__(self):
        self.lines = []
        self.heartbeat = None
    def send_line(self, text):
        self.lines.append(text)
        return True
    def start_minigame_heartbeat(self, sid):
        self.heartbeat = sid
    def stop_minigame_heartbeat(self):
        self.heartbeat = None


with tempfile.TemporaryDirectory(prefix="puff-smoke-") as temp:
    for attr in ("FILE_PATH", "TEAM_FILE_PATH", "QUICK_FILE_PATH",
                 "MAYHEM_FILE_PATH", "MUNCHIES_FILE_PATH", "PUFF_FILE_PATH"):
        setattr(ScoreManager, attr, str(Path(temp) / (attr + ".json")))
    reader = Reader()
    state = StateMachine(serial_reader=reader)
    gui = ScoreGUI()
    gui.acquire_display()
    state.preload_minigame()  # same dormant Munchies cache as real main.py
    state.selected_arcade_game = ARCADE_PUFF_N_RIFF
    state.handle_event(GameEvent("GAME_START", (GAME_ARCADE, 2)))
    totals = []
    for player, sid in ((1, 17), (2, 18)):
        state.handle_event(parse_line(f"MUNCHIES_PLAYER,{player}"))
        state.handle_event(parse_line(f"GUITAR_SOLO_START,{sid}"))
        gui.render_puff_loading(state)
        gui.flip_display()
        state.mark_puff_loading_presented()
        state._puff_loading_started_at -= 2
        begin = time.monotonic()
        state.tick()
        assert state.state == AppState.MINIGAME, state.party_message
        assert f"MG_READY,{sid}" in reader.lines
        game = state.minigame
        game.draw(gui.screen)  # real countdown art/audio/font caches
        game.phase = "playing"
        first = game.notes[0]
        game.song_time = first.at
        state.handle_event(parse_line(f"MG_INPUT,{sid},0,0"))
        state.handle_event(parse_line(f"MG_INPUT,{sid},1,{(1,4,2)[first.lane]}"))
        assert game.hits == 1 and game.score > 0
        state.handle_event(parse_line(f"MG_INPUT,{sid},2,0"))
        assert not any(game._held_lanes)
        render_begin = time.monotonic()
        for _ in range(30):
            game.draw(gui.screen)
            gui.flip_display()
        render_ms = (time.monotonic() - render_begin) * 1000 / 30
        print(f"PLAYER {player}: ready in {render_begin-begin:.2f}s, "
              f"render {render_ms:.1f}ms/frame, hits={game.hits}", flush=True)
        total = game.score
        totals.append(total)
        game.finished = True
        state.tick()
        assert f"MG_DONE,{sid},{total}" in reader.lines
        state.handle_event(parse_line(f"MG_ACK,{sid}"))
        state.handle_event(parse_line(f"MUNCHIES_RESULT,{player},{total}"))
    state.handle_event(parse_line("MUNCHIES_FINISH,1"))
    assert state.state == AppState.FINAL_SCORES
    assert [state.final_scores[p] for p in (1,2)] == totals
    assert state.highscore_manager is state.puff_score_manager
    assert reader.heartbeat is None
    try:
        import resource
        print(f"Peak RSS: {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f} MiB")
    except ImportError:
        pass
    gui.release_display()
    pygame.quit()
    print("Puff real-assets cabinet smoke: PASS", flush=True)
