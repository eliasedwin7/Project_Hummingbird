"""Gymnasium environment: teach an AI to fly the simulated Tello through gates.

The AI senses only what the real drone could know: its own velocity, turn rate
and height (Tello telemetry), and where the next gates are relative to itself
(on the real drone that will come from spotting markers on the gates with the
camera). It answers with the four rc sticks, exactly like a human pilot. No
pixels, so a laptop CPU can train it.

Training uses domain randomisation (random drone speed/response, wind, control
delay and sensor noise) so the pilot doesn't over-fit to one perfect drone,
and mostly Endless courses (a new random track every episode) so it learns to
fly in general rather than memorising a track.
"""
import math
from collections import deque

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from drone_sim import Course, Params, Race

DECISION_HZ = 10                # the AI picks new sticks 10 times a second
PHYSICS_DT = 1 / 60
SUBSTEPS = round(1 / (DECISION_HZ * PHYSICS_DT))
RANGE = 12.0                    # metres: gate offsets are divided by this
PILLAR_RANGE = 6.0              # pillars further than this are ignored
OBS_SIZE = 26
FIXED_COURSES = ("first_flight", "slalom", "oval", "pillar_forest")


def _body(dx, dy, yaw_deg):
    """World offset -> (forward, right) in the drone's frame."""
    a = math.radians(yaw_deg)
    return dx * math.sin(a) + dy * math.cos(a), dx * math.cos(a) - dy * math.sin(a)


def _gate_after(race, k):
    """The k-th upcoming gate (0 = next), or None if the race ends before it."""
    gates, i = race.course.gates, race.next_gate + k
    if race.endless:
        return gates[i] if i < len(gates) else None
    laps_left = race.course.laps - race.lap - 1
    if i < len(gates):
        return gates[i]
    return gates[i % len(gates)] if laps_left > 0 else None


def observe(race, prev_action, sense_pillars=True):
    """The pilot's view of the world as 26 numbers. Shared by training, the game and (later) the real drone."""
    d = race.drone
    obs = np.zeros(OBS_SIZE, dtype=np.float32)
    vf, vr = _body(d.vx, d.vy, d.yaw)
    obs[0:5] = vf / 3, vr / 3, d.vz, d.yaw_rate / 100, d.z / 3
    for k, base in ((0, 5), (1, 12)):
        g = _gate_after(race, k)
        if g is None:
            continue
        cx, cy, cz = g.center
        f, r = _body(cx - d.x, cy - d.y, d.yaw)
        rel = math.radians(g.heading - d.yaw)
        if k == 0:
            obs[base:base + 7] = f / RANGE, r / RANGE, (cz - d.z) / RANGE, math.sin(rel), math.cos(rel), g.width / 2, g.height / 2
        else:
            obs[base:base + 5] = f / RANGE, r / RANGE, (cz - d.z) / RANGE, math.sin(rel), math.cos(rel)
    if sense_pillars:
        near, best = None, PILLAR_RANGE
        for p in race.course.pillars[-40:] if race.endless else race.course.pillars:
            gap = math.hypot(p.x - d.x, p.y - d.y) - p.radius
            if gap < best and d.z < p.height + 0.3:
                near, best = p, gap
        if near:
            f, r = _body(near.x - d.x, near.y - d.y, d.yaw)
            obs[17:21] = f / PILLAR_RANGE, r / PILLAR_RANGE, near.radius, 1.0
    obs[21] = race.time_left / race.time_limit if race.endless and race.time_limit else 1.0
    obs[22:26] = prev_action
    return np.clip(obs, -3, 3)


class TelloGatesEnv(gym.Env):
    """Fly through as many gates as possible without crashing.

    Reward: +1 per metre of progress toward the next gate, +10 per gate,
    +20 for finishing a fixed course, -10 for a crash / missed gate / timeout,
    and a small penalty for jerky stick movements.
    """
    metadata = {"render_modes": []}

    def __init__(self, courses=None, endless_share=0.7, randomize=True, sense_pillars=True, max_seconds=180):
        self.courses = tuple(courses or FIXED_COURSES)
        self.endless_share = endless_share
        self.randomize = randomize
        self.sense_pillars = sense_pillars
        self.max_steps = int(max_seconds * DECISION_HZ)
        self.observation_space = spaces.Box(-3, 3, (OBS_SIZE,), np.float32)
        self.action_space = spaces.Box(-1, 1, (4,), np.float32)
        self._fixed = {c: Course.load(c) for c in self.courses if c != "endless"}

    # --- episode setup -----------------------------------------------------
    def _params(self):
        if not self.randomize:
            return Params()
        u = self.np_random.uniform
        base = Params()
        return Params(max_speed=base.max_speed * u(0.85, 1.15), max_climb=base.max_climb * u(0.8, 1.2),
                      max_yaw=base.max_yaw * u(0.85, 1.15), tau=u(0.25, 0.5), yaw_tau=u(0.08, 0.2),
                      drift=u(0.0, 0.15))

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        options = options or {}
        course_id = options.get("course")
        if course_id is None:
            fixed = [c for c in self.courses if c != "endless"]
            use_endless = not fixed or self.np_random.random() < self.endless_share
            course_id = "endless" if use_endless else str(self.np_random.choice(fixed))
        if course_id == "endless":
            course = Course.load("endless", seed=options.get("course_seed", int(self.np_random.integers(1 << 30))))
        else:
            course = self._fixed.get(course_id) or Course.load(course_id)
        self.race = Race(course, self._params(), seed=int(self.np_random.integers(1 << 30)))
        self.race.command("takeoff")
        while self.race.drone.mode == "takeoff":
            self.race.step((0, 0, 0, 0), PHYSICS_DT)

        delay = int(self.np_random.integers(0, 3)) if self.randomize else 0  # 0-200 ms of radio lag
        self.pending = deque([np.zeros(4, np.float32)] * delay)
        self.prev_action = np.zeros(4, np.float32)
        self.steps, self.gates = 0, 0
        self.prev_dist = self._dist()
        return self._obs(), {}

    # --- stepping ----------------------------------------------------------
    def _dist(self):
        g, d = _gate_after(self.race, 0), self.race.drone
        return math.dist((d.x, d.y, d.z), g.center) if g else 0.0

    def _obs(self):
        obs = observe(self.race, self.prev_action, self.sense_pillars)
        if self.randomize:
            obs = obs + self.np_random.normal(0, 0.01, OBS_SIZE).astype(np.float32)
        return obs

    def step(self, action):
        action = np.clip(np.asarray(action, np.float32), -1, 1)
        self.pending.append(action)
        applied = self.pending.popleft()
        rc = [int(round(v * 100)) for v in applied]

        events = []
        for _ in range(SUBSTEPS):
            events += self.race.step(rc, PHYSICS_DT)
            if self.race.game_over or self.race.drone.mode == "crashed" or self.race.finished:
                break
        self.steps += 1

        reward = -0.01 - 0.05 * float(np.mean((action - self.prev_action) ** 2))
        terminated, end = False, None
        passed = sum(1 for e in events if e["type"] == "gate")
        dist = self._dist()
        if passed:
            self.gates += passed
            reward += 10.0 * passed
        else:
            reward += self.prev_dist - dist
        self.prev_dist = dist
        for e in events:
            if e["type"] == "crash":
                terminated, end = True, e["cause"]
            elif e["type"] == "gameover":
                terminated, end = True, end or e["reason"]
            elif e["type"] == "finish":
                terminated, end = True, "finished"
                reward += 20.0
        if terminated and end != "finished":
            reward -= 10.0
        truncated = not terminated and self.steps >= self.max_steps
        self.prev_action = action
        info = {"gates": self.gates}
        if terminated or truncated:
            info["end"] = end or "time_limit"
        return self._obs(), reward, terminated, truncated, info
