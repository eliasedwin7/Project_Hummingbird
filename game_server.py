#!/usr/bin/env python3
"""Tello Sim: a 3D drone racing game in the browser.

Runs the shared simulator (drone_sim.py) and streams its state to
static/game.html, which draws the world with three.js. The physics lives
here in Python so the AI pilot trains on exactly the same drone, and press I
in the game to let the trained pilot (models/pilot.zip) fly.

    python game_server.py      ->  http://localhost:8081
"""
import argparse
import asyncio
import contextlib
import time
import webbrowser
from pathlib import Path

from aiohttp import WSMsgType, web

from drone_sim import Course, Race

HERE = Path(__file__).resolve().parent
SIM_DT = 1 / 60       # fixed physics step
RC_TIMEOUT = 0.5      # no stick input for this long -> hover
AI_EVERY = 6          # physics steps per AI decision (10 Hz, as in training)
OVERRIDE = 10         # any human stick beyond this takes over from the AI


class AIPilot:
    """The trained pilot (models/pilot.zip). Loaded on first use: it needs the
    AI environment's PyTorch, so the game still runs without it."""

    def __init__(self, path=HERE / "models" / "pilot"):
        if not Path(f"{path}.zip").exists():
            raise FileNotFoundError(path)
        import pickle

        import numpy as np
        from stable_baselines3 import PPO

        from tello_env import observe
        self.np, self.observe = np, observe
        self.model = PPO.load(path, device="cpu")
        with open(f"{path}_vecnormalize.pkl", "rb") as f:
            norm = pickle.load(f)
        self.mean, self.var = norm.obs_rms.mean, norm.obs_rms.var
        self.clip, self.eps = norm.clip_obs, norm.epsilon

    def act(self, race, prev_action):
        np = self.np
        obs = self.observe(race, prev_action)
        obs = np.clip((obs - self.mean) / np.sqrt(self.var + self.eps), -self.clip, self.clip)
        action, _ = self.model.predict(obs, deterministic=True)
        return np.clip(action, -1, 1).astype(np.float32)


_ai = None


def load_ai():
    global _ai
    if _ai is None:
        _ai = AIPilot()
    return _ai


async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=5, compress=False)
    await ws.prepare(request)
    s = {"race": None, "rc": [0, 0, 0, 0], "rc_time": 0.0, "paused": False,
         "ai": False, "ai_action": [0.0] * 4, "ai_rc": [0, 0, 0, 0], "ai_tick": 0}

    def pick_rc(race):
        """Human sticks, or the AI's while AI mode is on and the human isn't touching anything."""
        human = s["rc"] if time.time() - s["rc_time"] < RC_TIMEOUT else (0, 0, 0, 0)
        if not s["ai"] or max(map(abs, human)) > OVERRIDE:
            return human, "you"
        if race.drone.mode == "landed" and not race.game_over and not race.finished and race.crash_timer is None:
            race.command("takeoff")
        if race.drone.mode == "flying":
            if s["ai_tick"] % AI_EVERY == 0:
                s["ai_action"] = load_ai().act(race, s["ai_action"])
                s["ai_rc"] = [int(round(v * 100)) for v in s["ai_action"]]
            s["ai_tick"] += 1
            return s["ai_rc"], "ai"
        return (0, 0, 0, 0), "ai"

    async def run():
        # Step in fixed increments of real elapsed time, so the sim keeps true
        # speed even though Windows timers are coarse (~15 ms).
        last, acc, clock = time.perf_counter(), 0.0, 0.0
        while True:
            await asyncio.sleep(SIM_DT)
            now = time.perf_counter()
            elapsed, last = min(now - last, 0.25), now
            race = s["race"]
            if not race or s["paused"]:
                continue
            acc += elapsed
            events, pilot = [], "you"
            while acc >= SIM_DT:
                rc, pilot = pick_rc(race)
                events += race.step(rc, SIM_DT)
                acc -= SIM_DT
                clock += SIM_DT
            await ws.send_json({"t": "s", "clock": round(clock, 4), **race.state(), "events": events,
                                "ai": s["ai"], "pilot": pilot})

    async def send(obj):
        with contextlib.suppress(ConnectionError):
            await ws.send_json(obj)

    await send({"t": "courses", "list": Course.list()})
    runner = asyncio.create_task(run())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = msg.json()
            kind = data.get("t")
            if kind == "rc":
                try:
                    s["rc"] = [max(-100, min(100, int(v))) for v in data.get("v", [])[:4]]
                    s["rc_time"] = time.time()
                except (TypeError, ValueError):
                    pass
            elif kind == "start":
                try:
                    course = Course.load(str(data.get("course", "")), seed=data.get("seed"))
                except (OSError, ValueError, KeyError, TypeError):
                    await send({"t": "error", "m": "Could not load that course"})
                    continue
                s["race"], s["paused"] = Race(course), False
                await send({"t": "course", "course": course.to_dict()})
            elif kind == "cmd" and s["race"]:
                c = str(data.get("c", ""))
                await send({"t": "reply", "c": c, "r": s["race"].command(c)})
            elif kind == "pause":
                s["paused"] = bool(data.get("on"))
            elif kind == "ai":
                want = bool(data.get("on"))
                if want:
                    try:
                        load_ai()
                    except ImportError:
                        await send({"t": "ai", "on": False, "error": "The AI pilot needs the AI environment: run setup_ai.bat, then start the game again."})
                        continue
                    except FileNotFoundError:
                        await send({"t": "ai", "on": False, "error": "No trained pilot yet: run train.bat first."})
                        continue
                s["ai"], s["ai_action"], s["ai_tick"] = want, [0.0] * 4, 0
                await send({"t": "ai", "on": want})
    except Exception:
        pass
    finally:
        runner.cancel()
    return ws


async def index(request):
    return web.FileResponse(HERE / "static" / "game.html", headers={"Cache-Control": "no-store"})


def main():
    ap = argparse.ArgumentParser(description="Tello Sim racing game")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static", HERE / "static")

    def quiet_resets(loop, context):
        if not isinstance(context.get("exception"), ConnectionResetError):
            loop.default_exception_handler(context)

    async def on_startup(app):
        asyncio.get_running_loop().set_exception_handler(quiet_resets)
        url = f"http://localhost:{args.port}"
        print(f"\n  Tello Sim is running\n  Open in Chrome:  {url}\n  Press Ctrl+C here to stop.\n", flush=True)
        if not args.no_browser:
            webbrowser.open(url)

    app.on_startup.append(on_startup)
    try:
        web.run_app(app, host="0.0.0.0", port=args.port, print=None)
    except OSError as e:
        print(f"\nCould not open port {args.port} ({e}). Is Tello Sim already running?")


if __name__ == "__main__":
    main()
