# TODO

## 1. Simulator + game
- [x] `drone_sim.py`: one headless physics + race engine (Tello-like stick response, takeoff/land/flip, gates, crashes, lap timing). Shared by the game, practice mode and the AI.
- [x] Courses as JSON files in `courses/` (5 courses).
- [x] 3D racing game in Chrome (three.js): chase / FPV / high-follow cameras, gate highlights, minimap, timer, best times. Same controls as the real drone (keyboard, gamepad, touch).
- [x] Practice mode (`fake_tello.py`) uses the same physics and courses.
- [x] Endless mode: procedurally generated course (seeded), difficulty ramps with score, gate countdown, one-life.
- [ ] Later: more courses, wind setting, ghost of your best lap, in-game course editor, "daily seed" challenge.

## 2. AI pilot (reinforcement learning): CPU is enough  ← in progress
- [x] Separate AI Python environment (`setup_ai.bat`, outside Google Drive) so PyTorch doesn't clash with the main Python.
- [x] `tello_env.py`: Gymnasium environment around `drone_sim.py`. The AI sees 26 numbers (own speed/height + next two gates relative to itself + nearest pillar), not pixels, and outputs the four sticks at 10 Hz.
- [x] Domain randomisation: random delay (0-200 ms), wind, speed and response differences, sensor noise.
- [x] Trains mostly on Endless courses (new seed every flight) plus the fixed tracks.
- [x] AI mode in the 3D game (press I), with instant human override.
- [x] First training run (PPO, 3M steps, ~20 min on CPU), 2026-09-28. Exam on 30 Endless courses: **AI 89 gates avg vs scripted 48** (70 vs 48 with wind/delay). Finishes First Flight / Oval ~2x faster than scripted, but **crashes early on Slalom and Pillar Forest**.
- [ ] Round 2: make Endless also generate sharply angled gates (like Slalom's zigzag) so the AI learns them; add a bonus for passing near the gate centre (most crashes are clipping gate frames); retrain and re-run the exam.
- [ ] Takeoff and landing are still automatic commands, not learned (fine for now).

## 3. Sim-to-real
- [ ] Flight recorder in `tello_bridge.py`: log stick inputs + telemetry from real flights.
- [ ] Tune `drone_sim.py` parameters (speed, response time, delay) to match the recorded real flights.
- [ ] Marker detection on the real video (OpenCV ArUco tags or bright coloured gates) → gives the AI the same "where is the gate" numbers it had in the sim.
- [ ] Autopilot mode in the real controller: AI flies, capped speed and altitude, **any key/stick from the human takes over instantly**, emergency stays on Esc×2.
- [ ] First real test: hover and centre over a marker, then a single gate. Indoors, prop guards on.

## 4. Android app
- [ ] **Android app**: standalone phone controller that doesn't need the laptop bridge.
  - Talks UDP to the Tello directly (Android apps can, unlike Chrome): commands to 192.168.10.1:8889, telemetry on 8890, video on 11111. Same protocol as `tello_bridge.py`.
  - Decode the H.264 video with Android's `MediaCodec` (hardware, low latency).
  - Same controls as the web page: touch sticks, Bluetooth gamepad, take off / land / flip / emergency, photo and video recording, HUD.
  - Same safety rules: hover when sticks are released, keepalive so it doesn't auto-land, double-tap emergency.
  - Must work on new Android versions (the reason the official app was dropped).
