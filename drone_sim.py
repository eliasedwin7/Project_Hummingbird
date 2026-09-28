"""Tello flight simulator core: physics and race logic, no graphics.

Shared by the 3D game (game_server.py), practice mode (fake_tello.py) and,
later, the reinforcement-learning environment. Pure Python and headless, so it
can also run much faster than real time for training.

Coordinates: x = east, y = north, z = up (metres). Yaw in degrees, clockwise
from north, like a compass. Sticks use the Tello's rc convention:
[left/right, forward/back, up/down, yaw], each -100..100.
"""
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path

COURSES_DIR = Path(__file__).resolve().parent / "courses"
BAR_RADIUS = 0.05  # gate frame tube radius


@dataclass
class Params:
    """How the drone flies. Tweak these to match a real Tello (sim-to-real) or randomise them for training."""
    max_speed: float = 3.0       # m/s horizontal at full stick
    max_climb: float = 1.0       # m/s vertical at full stick
    max_yaw: float = 100.0       # deg/s at full stick
    tau: float = 0.35            # s, how quickly speed follows the sticks
    yaw_tau: float = 0.12        # s, same for turning
    min_alt: float = 0.3         # m, the Tello won't fly lower than this
    max_alt: float = 10.0
    takeoff_alt: float = 0.8
    radius: float = 0.12         # m, drone size incl. prop guards (for collisions)
    drift: float = 0.0           # m/s, random wind strength
    battery_drain: float = 0.12  # % per second of flight


def _wrap(deg):
    return (deg + 180) % 360 - 180


class Drone:
    """A Tello from the outside: sticks in, motion out. The real one has its own stabiliser, so we model its response, not its motors."""

    def __init__(self, params=None, rng=None):
        self.p = params or Params()
        self.rng = rng or random.Random()
        self.reset()

    def reset(self, x=0.0, y=0.0, yaw=0.0, battery=100.0):
        self.x, self.y, self.z = x, y, 0.0
        self.vx = self.vy = self.vz = 0.0
        self.yaw, self.yaw_rate = yaw, 0.0
        self.pitch = self.roll = 0.0
        self.mode = "landed"  # landed | takeoff | flying | landing | crashed
        self.battery, self.flight_time = battery, 0.0
        self.flip_dir, self.flip_t = None, 0.0
        self.wind_x = self.wind_y = 0.0

    @property
    def airborne(self):
        return self.mode in ("takeoff", "flying", "landing")

    def command(self, cmd):
        """Tello SDK text command -> reply text, like the real drone."""
        if cmd == "takeoff":
            if self.mode != "landed" or self.battery < 10:
                return "error"
            self.mode = "takeoff"
            return "ok"
        if cmd == "land":
            if self.mode not in ("takeoff", "flying"):
                return "error"
            self.mode = "landing"
            return "ok"
        if cmd == "emergency":
            if self.airborne:
                self.mode = "crashed"
            return "ok"
        if cmd.startswith("flip ") and cmd[5:] in ("l", "r", "f", "b"):
            if self.mode != "flying" or self.flip_dir or self.battery < 50:
                return "error"
            self.flip_dir, self.flip_t = cmd[5:], 0.0
            return "ok"
        return "error"

    def step(self, rc, dt):
        p = self.p
        if not self.airborne:
            self.vx = self.vy = self.vz = self.yaw_rate = self.pitch = self.roll = 0.0
            if self.mode == "crashed":
                self.z = max(0.0, self.z - 3 * dt)
            return
        self.flight_time += dt
        self.battery = max(0.0, self.battery - p.battery_drain * dt)
        if self.battery <= 0 and self.mode == "flying":
            self.mode = "landing"

        a = math.radians(self.yaw)
        fx, fy, rx, ry = math.sin(a), math.cos(a), math.cos(a), -math.sin(a)
        want_f = want_r = want_yaw = 0.0
        if self.mode == "flying" and not self.flip_dir:
            want_r, want_f = rc[0] / 100 * p.max_speed, rc[1] / 100 * p.max_speed
            want_vz = rc[2] / 100 * p.max_climb
            want_yaw = rc[3] / 100 * p.max_yaw
        elif self.mode == "takeoff":
            want_vz = max(-0.6, min(0.6, (p.takeoff_alt - self.z) * 2))
        elif self.mode == "landing":
            want_vz = -0.6
        else:  # flipping
            want_vz = 0.0

        k = 1 - math.exp(-dt / p.tau)
        want_vx, want_vy = want_f * fx + want_r * rx, want_f * fy + want_r * ry
        ax, ay = (want_vx - self.vx) * k / dt, (want_vy - self.vy) * k / dt
        self.vx += (want_vx - self.vx) * k
        self.vy += (want_vy - self.vy) * k
        self.vz += (want_vz - self.vz) * k
        self.yaw_rate += (want_yaw - self.yaw_rate) * (1 - math.exp(-dt / p.yaw_tau))
        self.yaw = _wrap(self.yaw + self.yaw_rate * dt)

        if p.drift:  # slowly wandering wind
            self.wind_x += (self.rng.gauss(0, p.drift) - self.wind_x) * dt * 0.5
            self.wind_y += (self.rng.gauss(0, p.drift) - self.wind_y) * dt * 0.5
        self.x += (self.vx + self.wind_x) * dt
        self.y += (self.vy + self.wind_y) * dt
        self.z += self.vz * dt

        if self.mode == "takeoff" and abs(p.takeoff_alt - self.z) < 0.03:
            self.mode = "flying"
        elif self.mode == "landing" and self.z <= 0:
            self.z, self.mode = 0.0, "landed"
        elif self.mode == "flying":
            clamped = max(p.min_alt, min(p.max_alt, self.z))
            if clamped != self.z:
                self.z, self.vz = clamped, 0.0

        # body tilt, for visuals and telemetry (nose down = negative pitch)
        vf, vr = self.vx * fx + self.vy * fy, self.vx * rx + self.vy * ry
        af, ar = ax * fx + ay * fy, ax * rx + ay * ry
        self.pitch = max(-30.0, min(30.0, -(vf / p.max_speed * 12 + af * 2.5)))
        self.roll = max(-30.0, min(30.0, vr / p.max_speed * 12 + ar * 2.5))
        if self.flip_dir:
            self.flip_t += dt
            spin = 360 * min(1.0, self.flip_t / 0.8)
            if self.flip_dir in ("l", "r"):
                self.roll += spin if self.flip_dir == "r" else -spin
            else:
                self.pitch += -spin if self.flip_dir == "f" else spin
            if self.flip_t >= 0.8:
                self.flip_dir = None

    def telemetry(self):
        """Tello-style state fields (same keys and units the real drone sends)."""
        a = math.radians(self.yaw)
        vf = self.vx * math.sin(a) + self.vy * math.cos(a)
        vr = self.vx * math.cos(a) - self.vy * math.sin(a)
        return {
            "pitch": int(self.pitch), "roll": int(self.roll), "yaw": int(self.yaw),
            "vgx": int(vf * 10), "vgy": int(vr * 10), "vgz": int(-self.vz * 10),
            "templ": 58, "temph": 61, "tof": int(self.z * 100) + 10, "h": int(self.z * 100),
            "bat": int(self.battery), "baro": round(self.z, 2), "time": int(self.flight_time),
        }


@dataclass
class Gate:
    x: float
    y: float
    heading: float = 0.0   # direction you fly through it (deg, compass)
    z: float = 0.4         # height of the bottom of the opening
    width: float = 1.4
    height: float = 1.2

    @property
    def normal(self):
        a = math.radians(self.heading)
        return math.sin(a), math.cos(a)

    @property
    def side(self):
        a = math.radians(self.heading)
        return math.cos(a), -math.sin(a)

    @property
    def center(self):
        return self.x, self.y, self.z + self.height / 2

    def bars(self):
        """Frame tubes as 3D segments: two posts from the ground, top bar, bottom bar."""
        sx, sy = self.side
        hw, top = self.width / 2 + BAR_RADIUS, self.z + self.height + BAR_RADIUS
        left, right = (self.x - sx * hw, self.y - sy * hw), (self.x + sx * hw, self.y + sy * hw)
        bars = [((*left, 0.0), (*left, top)), ((*right, 0.0), (*right, top)), ((*left, top), (*right, top))]
        if self.z > 0.1:
            bottom = self.z - BAR_RADIUS
            bars.append(((*left, bottom), (*right, bottom)))
        return bars


@dataclass
class Pillar:
    x: float
    y: float
    radius: float = 0.3
    height: float = 3.0
    gate: int = -1         # endless mode: the gate this obstacle was spawned with


class Course:
    endless = False

    def __init__(self, data):
        self.id = data.get("id", "")
        self.name = data["name"]
        self.description = data.get("description", "")
        self.laps = data.get("laps", 1)
        self.timed = data.get("timed", True)
        start = data.get("start", {})
        self.start = (start.get("x", 0.0), start.get("y", 0.0), start.get("yaw", 0.0))
        self.gates = [Gate(**g) for g in data.get("gates", [])]
        self.pillars = [Pillar(**p) for p in data.get("pillars", [])]

    @classmethod
    def load(cls, course_id, seed=None):
        if course_id == "endless":
            return EndlessCourse(seed)
        path = COURSES_DIR / f"{Path(course_id).name}.json"
        return cls({"id": path.stem, **json.loads(path.read_text(encoding="utf-8"))})

    @staticmethod
    def list():
        out = [{"id": "endless", "name": EndlessCourse.NAME, "description": EndlessCourse.DESCRIPTION,
                "gates": 0, "laps": 1, "timed": True, "endless": True, "order": 0.5}]
        for path in sorted(COURSES_DIR.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append({"id": path.stem, "name": data["name"], "description": data.get("description", ""),
                        "gates": len(data.get("gates", [])), "laps": data.get("laps", 1),
                        "timed": data.get("timed", True), "endless": False, "order": data.get("order", 99)})
        return sorted(out, key=lambda c: c["order"])

    def to_dict(self):
        return {"id": self.id, "name": self.name, "description": self.description, "laps": self.laps,
                "timed": self.timed, "endless": self.endless, "seed": getattr(self, "seed", None),
                "start": dict(zip(("x", "y", "yaw"), self.start)),
                "gates": [{"index": i, **asdict(g)} for i, g in enumerate(self.gates)],
                "pillars": [asdict(p) for p in self.pillars]}


class EndlessCourse(Course):
    """A course that builds itself ahead of the drone, forever, and gets harder with every gate.

    The path wanders generally north: bends get sharper, height changes bigger,
    gates smaller and pillars more common as the gate count climbs. The same
    seed always gives the same course.
    """
    endless = True
    AHEAD = 8  # gates kept generated beyond the next one
    NAME = "Endless"
    DESCRIPTION = "A new random course every run that gets harder as you go. One crash, one miss or running out of time ends it."

    def __init__(self, seed=None):
        self.seed = random.randrange(1_000_000) if seed is None else int(seed)
        super().__init__({"id": "endless", "name": self.NAME, "description": self.DESCRIPTION})
        self.restart()

    def restart(self):
        self.rng = random.Random(self.seed)
        self.gates, self.pillars = [], []
        self._x, self._y, self._heading, self._z = 0.0, 0.0, 0.0, 0.4
        self.extend(self.AHEAD + 1)

    def extend(self, count):
        """Generate `count` more gates. Returns (new gates as dicts, new pillars as dicts)."""
        rng, new_gates, new_pillars = self.rng, [], []
        for _ in range(count):
            i = len(self.gates)
            level = min(1.0, max(0.0, (i - 2) / 50))  # 0 = easy start, 1 = full difficulty (~gate 52)
            if i == 0:
                dist, turn = 4.5, 0.0
            else:
                dist = rng.uniform(4.5, 7.0)
                turn = rng.gauss(0, 10 + 30 * level) - self._x * 1.5  # drift back toward the centre line
                self._z = max(0.2, min(2.4, self._z + rng.gauss(0, 0.25 + 0.55 * level)))
            heading = max(-70.0, min(70.0, self._heading + turn))  # always heading roughly north: no loops
            mid = math.radians((self._heading + heading) / 2)
            x, y = self._x + dist * math.sin(mid), self._y + dist * math.cos(mid)
            gate = Gate(round(x, 3), round(y, 3), round(heading, 1), round(self._z, 2),
                        round(1.8 - 0.7 * level, 2), round(1.5 - 0.5 * level, 2))
            self.gates.append(gate)
            new_gates.append({"index": i, **asdict(gate)})

            if i > 0 and rng.random() < 0.15 + 0.45 * level:
                # an obstacle beside the straight line between gates: cut the corner and you hit it
                mx, my = (self._x + x) / 2, (self._y + y) / 2
                side = rng.choice((-1, 1)) * rng.uniform(1.0, 2.2 - 0.8 * level)
                pillar = Pillar(round(mx + math.cos(mid) * side, 3), round(my - math.sin(mid) * side, 3),
                                0.3, round(rng.uniform(2.5, 4.5), 2), i)
                self.pillars.append(pillar)
                new_pillars.append(asdict(pillar))
            self._x, self._y, self._heading = x, y, heading
        return new_gates, new_pillars


def _segment_distance(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    denom = sum(v * v for v in ab) or 1e-9
    t = max(0.0, min(1.0, sum(ap[i] * ab[i] for i in range(3)) / denom))
    return math.dist(p, [a[i] + ab[i] * t for i in range(3)])


class Race:
    """A drone on a course: gates in order, lap timing, crashes and respawns.

    On an endless course the rules change: one crash, a missed gate or running
    out of time on the gate countdown ends the run. The score is gates passed.
    """

    RESPAWN_DELAY = 1.5

    def __init__(self, course, params=None, seed=None):
        self.course = course
        self.endless = course.endless
        self.drone = Drone(params, random.Random(seed))
        self.reset()

    def reset(self):
        if self.endless:
            self.course.restart()
        self.drone.reset(*self.course.start)
        self.next_gate, self.lap = 0, 0
        self.time, self.started, self.finished = 0.0, False, False
        self.crash_timer = None
        self.crashes = 0
        self.game_over = None
        self.time_limit = self.time_left = self._allowance(self.course.start, 0) if self.endless else 0.0

    def command(self, cmd):
        if cmd == "reset":
            self.reset()
            return "ok"
        reply = self.drone.command(cmd)
        if cmd == "takeoff" and reply == "ok" and not self.started:
            self.started = True
        return reply

    def step(self, rc, dt):
        """Advance the world by dt seconds. Returns a list of event dicts
        (gate, lap, finish, crash, respawn, gameover, spawn)."""
        d, events = self.drone, []
        prev = (d.x, d.y, d.z)
        d.step(rc, dt)
        if self.game_over:
            return events
        if self.started and not self.finished and self.course.timed:
            self.time += dt

        if self.crash_timer is not None:
            self.crash_timer -= dt
            if self.crash_timer <= 0:
                self._respawn()
                events.append({"type": "respawn"})
            return events

        if d.mode == "crashed":
            return self._crash(events, "emergency")
        if d.airborne:
            pos = (d.x, d.y, d.z)
            hit = self._collision(pos)
            if hit:
                return self._crash(events, hit)
            if not self.finished and self.course.gates:
                self._check_gate(prev, pos, events)
            if self.endless and d.mode == "flying" and not self.game_over:
                self.time_left -= dt
                if self.time_left <= 0:
                    self.time_left = 0.0
                    self._end(events, "time")
        return events

    def _nearby(self):
        """Gates and pillars close enough to matter (endless courses keep growing)."""
        if not self.endless:
            return self.course.gates, self.course.pillars
        lo, hi = self.next_gate - 2, self.next_gate + 3
        gates = self.course.gates[max(0, lo):hi + 1]
        return gates, [p for p in self.course.pillars[-30:] if lo <= p.gate <= hi]

    def _collision(self, pos):
        reach = self.drone.p.radius + BAR_RADIUS + 0.02
        gates, pillars = self._nearby()
        for g in gates:
            if math.hypot(g.x - pos[0], g.y - pos[1]) > g.width / 2 + 0.5:
                continue
            if any(_segment_distance(pos, a, b) < reach for a, b in g.bars()):
                return "gate"
        for pl in pillars:
            if math.hypot(pos[0] - pl.x, pos[1] - pl.y) < pl.radius + self.drone.p.radius and pos[2] < pl.height:
                return "pillar"
        return None

    def _allowance(self, pos, i):
        """Seconds allowed to reach gate i in endless mode; you need to keep moving faster as the score grows."""
        g = self.course.gates[i]
        speed = min(2.2, 1.0 + 0.025 * i)  # average m/s required
        return round(math.hypot(g.x - pos[0], g.y - pos[1]) / speed + 2.0, 2)

    def _check_gate(self, prev, pos, events):
        g = self.course.gates[self.next_gate]
        (nx, ny), (sx, sy), (cx, cy, cz) = g.normal, g.side, g.center
        d0 = (prev[0] - cx) * nx + (prev[1] - cy) * ny
        d1 = (pos[0] - cx) * nx + (pos[1] - cy) * ny
        if not (d0 < 0 <= d1):
            return
        t = d0 / (d0 - d1)
        px, py, pz = (prev[i] + (pos[i] - prev[i]) * t for i in range(3))
        lateral, vertical = (px - cx) * sx + (py - cy) * sy, pz - cz
        if abs(lateral) > g.width / 2 or abs(vertical) > g.height / 2:
            if self.endless:
                self._end(events, "missed")
            return
        events.append({"type": "gate", "index": self.next_gate})
        self.next_gate += 1
        if self.endless:
            missing = self.next_gate + self.course.AHEAD + 1 - len(self.course.gates)
            if missing > 0:
                new_gates, new_pillars = self.course.extend(missing)
                events.append({"type": "spawn", "gates": new_gates, "pillars": new_pillars})
            self.time_limit = self.time_left = self._allowance(pos, self.next_gate)
            return
        if self.next_gate == len(self.course.gates):
            self.next_gate = 0
            self.lap += 1
            if self.lap >= self.course.laps:
                self.finished = True
                events.append({"type": "finish", "time": round(self.time, 3), "crashes": self.crashes})
            else:
                events.append({"type": "lap", "lap": self.lap})

    def _crash(self, events, cause):
        self.drone.mode = "crashed"
        self.crashes += 1
        events.append({"type": "crash", "cause": cause})
        if self.endless:
            self._end(events, cause)
        else:
            self.crash_timer = self.RESPAWN_DELAY
        return events

    def _end(self, events, reason):
        self.game_over = reason
        if self.drone.mode in ("takeoff", "flying"):
            self.drone.mode = "landing"  # the run is over: bring it down gently
        events.append({"type": "gameover", "reason": reason, "score": self.next_gate, "time": round(self.time, 3)})

    def _respawn(self):
        """Back in the air just before the gate you were heading for (or on the pad if none passed)."""
        self.crash_timer = None
        d = self.drone
        battery, flight_time = d.battery, d.flight_time
        if self.next_gate == 0 and self.lap == 0 or not self.course.gates:
            d.reset(*self.course.start, battery=battery)
        else:
            g = self.course.gates[self.next_gate]
            (nx, ny), (cx, cy, cz) = g.normal, g.center
            d.reset(cx - nx * 2.5, cy - ny * 2.5, g.heading, battery=battery)
            d.z, d.mode = max(d.p.min_alt, cz), "flying"
        d.flight_time = flight_time

    def state(self):
        d = self.drone
        race = {"time": round(self.time, 3), "started": self.started, "finished": self.finished,
                "next_gate": self.next_gate, "lap": self.lap, "laps": self.course.laps,
                "gates": len(self.course.gates), "crashes": self.crashes}
        if self.endless:
            race.update(endless=True, score=self.next_gate, game_over=self.game_over,
                        time_left=round(self.time_left, 2), time_limit=self.time_limit, seed=self.course.seed)
        return {
            "x": round(d.x, 3), "y": round(d.y, 3), "z": round(d.z, 3),
            "yaw": round(d.yaw, 2), "pitch": round(d.pitch, 2), "roll": round(d.roll, 2),
            "vx": round(d.vx, 2), "vy": round(d.vy, 2), "vz": round(d.vz, 2),
            "mode": d.mode, "battery": round(d.battery, 1), "flight_time": round(d.flight_time, 1),
            "race": race,
        }
