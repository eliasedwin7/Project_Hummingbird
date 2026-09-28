# TODO

## 0. Runs everywhere (requirement for every item below)
The game must work in a **desktop browser (Chrome, Edge, Firefox, Safari) and on Android (Chrome)**, with no install for players. Today it needs `game_server.py` running on a PC, because the physics (`drone_sim.py`) runs in Python and streams to the page. Options, pick one before building multiplayer:
- [ ] **A. Hosted server** (recommended first step): deploy `game_server.py` (Fly.io/Render/a VPS, HTTPS + `wss://`) so any phone or PC just opens a URL. Simple, keeps one physics engine, and is needed for internet multiplayer anyway. Needs: `PORT` env var, no `webbrowser.open`, optional password/room limits, no AI dependency (torch) on the server unless the AI is on.
- [ ] **B. Port the physics to JavaScript** (`drone_sim.js`, checked against `drone_sim.py` with shared test vectors): the game runs fully offline in the browser (single player, PWA/installable on Android), and gives multiplayer client-side prediction. Cost: two engines to keep in sync, so test them against each other.
- [ ] **C. Pyodide** (run `drone_sim.py` in the browser via WebAssembly): one engine, but big download and slower on phones.
- [ ] AI pilot in the browser: export `models/pilot.zip` to ONNX and run it with `onnxruntime-web` (no PyTorch or server needed).
- [ ] Responsive layout: phone portrait + landscape, touch sticks by default on touch devices, safe-area insets, `viewport` meta, no hover-only UI, fullscreen button.
- [ ] Performance budget: 60 fps on a mid-range Android phone (lower render scale/shadows/draw distance option, auto quality by frame time).
- [ ] Input parity: keyboard, gamepad, touch, plus tilt-steering as an option on phones.
- [ ] Cross-browser test matrix (desktop Chrome/Firefox/Safari + Android Chrome) with Playwright in CI (section 7); WebGL2 fallback message.
- [ ] Web app manifest + service worker so it can be installed to the Android home screen and works offline (with option B).
- [ ] The real-drone controller (`index.html`) stays PC/Android-with-bridge; the standalone Android app (section 4) replaces the bridge on phones.

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
- [ ] **Create the Android app** (Kotlin, Android Studio project in `android/`): standalone phone controller that doesn't need the laptop bridge.
  - Talks UDP to the Tello directly (Android apps can, unlike Chrome): commands to 192.168.10.1:8889, telemetry on 8890, video on 11111. Same protocol as `tello_bridge.py`.
  - Decode the H.264 video with Android's `MediaCodec` (hardware, low latency).
  - Same controls as the web page: touch sticks, Bluetooth gamepad, take off / land / flip / emergency, photo and video recording, HUD.
  - Same safety rules: hover when sticks are released, keepalive so it doesn't auto-land, double-tap emergency.
  - Must work on new Android versions (the reason the official app was dropped).
  - Milestones: (1) UDP command/telemetry layer + unit tests against `fake_tello.py`; (2) touch sticks + HUD; (3) `MediaCodec` video; (4) gamepad, photo/video recording; (5) signed release APK built by GitHub Actions (see section 7).

## 5. Boost collectibles
- [ ] Physics: `Drone` gets a `boost` state (temporary higher top speed / acceleration, then a short cooldown) with parameters in `Params`, in `drone_sim.py` so game, practice and AI all share it.
- [ ] Collectibles: `Boost` pickups (glowing orbs) placed by `Course` (JSON field `boosts`) and by `EndlessCourse` (seeded, on or near the racing line, some off-line as risk/reward).
- [ ] Race rules: pickup detection like gates, respawn or one-shot per lap (decide), boost count/timer in the race state sent to the client.
- [ ] Game UI (`static/game.html`): orb meshes + pickup effect, boost bar in the HUD, camera FOV/speed-lines while boosting, sound.
- [ ] AI: add the nearest boost to the observation and a small reward for collecting it. Changes the observation size, so it needs a retrain (do together with Round 2 in section 2). Cap boost in autopilot on the real drone (there are no pickups outside the sim).
- [ ] Add boosts to the course files and an option to turn them off.

## 6. Multiplayer
- [ ] Decide the model: same-course race with **ghosts** (each client runs its own physics, server relays poses; simplest, no lag problems) for racing; server-authoritative shared world for dogfights (see below), since kills must be decided in one place.
- [ ] Server (`game_server.py`): rooms/lobby over the existing WebSocket, room code to join, player names and colours, ready/start countdown so everyone starts on the same seed and course.
- [ ] Sync: clients send pose at ~10-20 Hz, server broadcasts; client interpolates other drones. Server validates lap/gate times (sanity check, not anti-cheat).
- [ ] Client: see other drones in the 3D scene, live position/lap standings, results screen, rematch.
- [ ] Modes: **Race** (first to finish N laps), **Endless duel** (same seed, highest score wins), **Time trial with ghosts** (async, compare with others' best laps), **Boost battle** (shared collectibles, first to grab gets it).
- [ ] Network play: LAN first (server already binds 0.0.0.0), then a hosted server or tunnel for friends over the internet.
- [ ] AI opponents as players in a room (the trained pilot flies as a bot).

### Dogfights (Mini Militia style, sim only)
Arena deathmatch between drones: fast, respawn instantly, pick up weapons and boosts, most kills wins. Needs the server-authoritative model above, because hits and kills must be decided in one place.
- [ ] Arena course type: open box or cave-like map with pillars/walls for cover, spawn points, pickup spots (new `arena` JSON format next to the race courses).
- [ ] Combat in `drone_sim.py`: health/shield, hit detection, damage, death and respawn (short invulnerability), kill/death counters. Real Tello has no weapon, so this stays out of `tello_bridge.py`.
- [ ] Weapons: starter blaster (infinite, weak) + pickups: rapid-fire, missile (homing, slow), shotgun, mines/EMP. Ammo and pickup respawn timers.
- [ ] Reuse the boost collectible for dodging/chasing; add health packs and shields as pickups.
- [ ] Controls: fire on Space / gamepad trigger / touch button, weapon swap; aim is drone heading (no separate aiming, keeps the 4-stick controls).
- [ ] Server: fixed-rate authoritative tick (~30 Hz) running all drones, clients send sticks (not poses), server sends snapshots; client-side prediction for your own drone, interpolation for the rest.
- [ ] Modes: Free-for-all deathmatch (kill limit / timer), Team deathmatch, Last drone standing, Race + weapons ("Mario Kart" style, combines with section 5).
- [ ] Client: crosshair/hit markers, damage flash, kill feed, scoreboard, respawn countdown, minimap showing enemies.
- [ ] AI bots: train a dogfight pilot (needs enemy-relative observations + shooting output, self-play against copies of itself); simple scripted bot first so rooms are never empty.
- [ ] Tests: hit detection, damage/respawn rules, tick determinism, server snapshot integration test.

## 7. Tests + GitHub Actions
- [ ] Tests with `pytest` in `tests/` (add `requirements-dev.txt`):
  - `drone_sim`: determinism for a given seed, takeoff/land, gate passing and lap timing, crash detection, EndlessCourse is reproducible and solvable, course JSON files all load and validate.
  - `tello_env`: Gymnasium `check_env`, observation shape/bounds, episode terminates.
  - `game_server`: WebSocket integration test with `aiohttp` test client (start race, receive state, stop).
  - `tello_bridge` / `fake_tello`: command parsing and keepalive/safety rules against the fake drone.
  - Multiplayer and boost tests as those features land.
  - Pilot smoke test: `models/pilot.zip` loads and flies a short course without crashing the harness (skipped if torch is not installed).
- [ ] GitHub Actions `.github/workflows/ci.yml`: on push and pull request, Windows + Ubuntu, Python 3.11/3.12, install `requirements.txt` + dev requirements, run `pytest`, and a lint step (ruff).
- [ ] Separate optional job for the AI tests (installs CPU torch + stable-baselines3, only on demand or nightly).
- [ ] Android workflow: build and unit-test the app, upload the APK as an artifact; release job on tags.
- [ ] Browser tests with Playwright (desktop Chromium/Firefox/WebKit + an Android Chrome emulation profile): page loads, connects, a race can be started, no console errors.
- [ ] Badge in `README.md`.

## 8. License and credits
- [x] MIT `LICENSE` (attribution required: the copyright notice has to stay in copies), `NOTICE.md` with credits and third-party notices, README section, credit line in the game menu.
- [ ] Put your real/legal name (or a studio name) in `LICENSE` if you want it there instead of `eliasedwin7`.
- [ ] Credits screen in every build: game menu (done), Android app "About" screen, multiplayer lobby footer, results screen ("made by ...").
- [ ] Add `LICENSE`/`NOTICE.md` to the Android APK and any hosted build (same notice rules).
- [ ] Optional: a GitHub "Sponsor" button (`.github/FUNDING.yml`) and a "Made with Project Hummingbird" link to make it easy for people to credit you.
- [ ] If you ever want to *require* visible credit or forbid commercial use, MIT can't do that. Alternatives: CC BY-NC (non-commercial) or a custom license. Decide before the project gets contributors, since relicensing later needs everyone's agreement.
