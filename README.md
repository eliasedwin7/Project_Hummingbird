# Project Hummingbird 🐦

A home-made flight deck for a DJI/Ryze Tello: a real-drone controller (**Tello Pilot**), a 3D racing game (**Tello Sim**) and, next, an AI pilot that learns in the sim before it flies the real thing. See [TODO.md](TODO.md) for the roadmap.

## Tello Pilot: fly the real drone

Fly a DJI/Ryze Tello from Chrome on your laptop, like a game. Use the keyboard, a gamepad (Xbox or PlayStation), or the touch screen. You get live video, a telemetry HUD, photos and video recording.

Chrome can't talk to the drone directly because the Tello uses raw UDP. So a small Python program (`tello_bridge.py`) runs in the background and relays between the drone and the web page.

## Quick start

1. Turn on the Tello and wait for the **blinking yellow** light.
2. Connect the laptop's Wi-Fi to **TELLO-XXXXXX**.
3. Double-click **`start.bat`**. Chrome opens at http://localhost:8080 and connects on its own.
4. Press **T** to take off.

**Practice first:** double-click **`practice.bat`** to fly a simulated drone in the real controller page. It needs no drone and no Wi-Fi change.

## Tello Sim: the racing game 🎮

Double-click **`game.bat`**. Chrome opens http://localhost:8081. Pick a course, press **T** to take off, and fly through the glowing gate. The clock starts on takeoff.

- **Endless ∞:** a new random course every run, like Flappy Bird or Temple Run. Gates keep coming, bends get sharper, height changes bigger, gates smaller and pillars more common as you go. Reach each gate before the bar runs out. One crash, a missed gate or running out of time ends the run. Score = gates passed.
- **5 fixed courses:** Free Flight (no timer), First Flight, Slalom, Oval Circuit (2 laps), Pillar Forest.
- **Same controls as the real drone** (keyboard, gamepad, touch), so practice carries straight over. Extra keys: **C** camera (chase / FPV / high follow), **R** restart, **Esc** menu.
- Hitting a gate or pillar is a crash. You respawn in front of the gate you were heading for, and the clock keeps running.
- Best times (and your best Endless score) are saved.
- It uses the same physics as practice mode and, later, the AI pilot (`drone_sim.py`).

Courses are plain JSON files in `courses/`. Copy one and edit it to make your own:
- Gates: `x`, `y` in metres (y = north), `heading` (direction you fly through, degrees), `z` (height of the opening's bottom), `width`, `height`.
- Pillars: `x`, `y`, `radius`, `height`.

## AI pilot (reinforcement learning) 🤖

An AI learns to fly the simulated Tello through gates by trial and error (PPO, from stable-baselines3). It trains on the laptop CPU; no graphics card needed.

1. **`setup_ai.bat`** (once): creates a separate Python environment for the AI in `%USERPROFILE%\.venvs\hummingbird`, outside Google Drive, so PyTorch never interferes with your main Python.
2. **`train.bat`**: trains the pilot. It prints progress (gates per flight, how flights end) and saves the best pilot to `models/pilot.zip`. Continue training later with `train.bat --resume --steps 2000000`.
3. **`game.bat`**, then press **I** (or the robot button / LB): the AI flies. Touch any control and you take over instantly; let go and the AI continues.

How it works:
- **What the AI senses**, 26 numbers: its own speed, turn rate and height (the real Tello reports these) and where the next two gates are relative to itself. On the real drone the gate positions will come from markers on the gates (see TODO). No camera pixels, which keeps training fast and makes the jump to the real drone easier.
- **What it controls:** the same four sticks you do, 10 times a second.
- **How it learns:** it gets points for moving toward the next gate and for passing gates, and loses points for crashing, missing a gate, running out of time or jerky sticks. It trains mostly on Endless courses, so it learns to fly rather than memorising a track.
- **Built for reality:** during training every flight gets a slightly different drone (speed, responsiveness), random wind, 0–200 ms of radio delay and sensor noise, so the pilot doesn't depend on a perfect simulator.
- **`evaluate_pilot.py`** gives the AI an exam on 30 fixed Endless courses and the fixed tracks, compared with a hand-written scripted pilot. Add `--noisy` to test it with wind and delay.

### First run: Windows Firewall ⚠️

Windows treats the Tello's Wi-Fi as a **Public** network. The first time the bridge runs, Windows asks whether Python may use the network. **Tick "Public networks" and allow it.** Otherwise video and telemetry are blocked, and the page stays on "Looking for your Tello…".

If you already clicked it away: open *Windows Security → Firewall & network protection → Allow an app through firewall*. Find **Python** (`anaconda3\python.exe`) and tick **Public**.

## Controls

| Action | Keyboard | Gamepad | Touch |
|---|---|---|---|
| Forward / back | W / S | Right stick ↕ | Right stick ↕ |
| Slide left / right | A / D | Right stick ↔ | Right stick ↔ |
| Up / down | ↑ / ↓ | Left stick ↕ | Left stick ↕ |
| Turn | ← / → (or Q / E) | Left stick ↔ | Left stick ↔ |
| Full speed | hold Shift | hold RT | – |
| Take off / land | T / L | A / B | buttons |
| Flip fwd / back / left / right | 1 / 2 / 3 / 4 | RB + D-pad | flip buttons |
| Photo / record | P / R | X / Y | top-right icons |
| **Emergency motor stop** | **Esc twice** | **Back + Start** | tap Emergency twice |
| Help & settings | H | | ⚙ icon |

The gamepad layout above is "Mode 2", the usual drone layout. You can switch to Mode 1 in settings. Photos and videos go to your Chrome **Downloads** folder.

Settings (⚙) let you change normal speed, stick smoothing, gamepad mode, and whether touch sticks show. On a touch-screen laptop the touch sticks appear automatically. Put a thumb anywhere in the lower-left or lower-right of the screen.

## Safety

- Let go of everything and the drone **hovers**. It also hovers if the page closes, the tab is hidden, or input stops for half a second.
- If the Tello hears nothing from the laptop for 15 seconds, it **lands by itself**. Stopping the bridge with Ctrl+C sends a land command.
- **Emergency cuts the motors and the drone falls.** Use it only if it hits something or someone.
- Flips need ≥ 50% battery. The drone won't take off below about 10%.
- The Tello holds position with a downward camera. Fly in good light over a textured floor. It drifts over plain or shiny floors and in wind.

## Using a phone as the controller

With the bridge running on the laptop, a phone on the same Wi-Fi can open the address printed in the bridge window (e.g. `http://192.168.10.2:8080`). The touch sticks show up automatically. Whether a second device can join the Tello's own Wi-Fi depends on the drone. Only one controller should fly at a time.

## Troubleshooting

| Problem | Fix |
|---|---|
| Stuck on "Looking for your Tello…" | Check the Wi-Fi is TELLO-xxxx. Check the firewall (see above). Restart the drone. |
| "Connected, waiting for data…" | Firewall is blocking incoming data. Allow Python on **Public** networks. |
| "Could not open a network port" | Another copy of Tello Pilot, or another Tello app, is running. Close it. |
| Video freezes or lags | Move closer. Other 2.4 GHz Wi-Fi nearby hurts the Tello's link. |
| Gamepad does nothing | Press any button once while the page is open. Chrome only exposes gamepads after input. |

## Files

- `tello_bridge.py`: the bridge (UDP ↔ WebSocket, H.264 → JPEG). Options: `--practice`, `--drone-ip`, `--port`, `--no-browser`.
- `static/index.html`: the controller page (no internet needed).
- `fake_tello.py`: the simulated drone used by practice mode.
- `drone_sim.py`: the shared simulator core (physics, gates, crashes, timing). No graphics, runs ~15,000 steps/s.
- `game_server.py` + `static/game.html`: the racing game (three.js, bundled in `static/vendor/`, so it works offline).
- `static/controls.js`, `static/common.css`: input handling and styles shared by the controller and the game.
- `courses/*.json`: race courses.
- `tello_env.py`: the AI training environment (Gymnasium). `train_pilot.py`, `evaluate_pilot.py`: training and exam. `models/`: trained pilots and the training log (`pilot_log.csv`).

## License and credits

Released under the [MIT License](LICENSE), © 2026 eliasedwin7. You're free to use, change and share it, but the copyright notice must stay with every copy. If you build on Project Hummingbird, please credit it, e.g. "Based on Project Hummingbird by eliasedwin7". Third-party components are listed in [NOTICE.md](NOTICE.md).

