#!/usr/bin/env python3
"""Practice drone: pretends to be a Tello on 127.0.0.1.

Answers SDK commands, streams telemetry, and renders the drone's camera view
as a real H.264 video feed, so the real controller page can be used without
the drone. The physics and course come from drone_sim.py, the same engine as
the 3D game. Started by `tello_bridge.py --practice`.
"""
import math
import socket
import threading
import time
from fractions import Fraction

import av
from PIL import Image, ImageDraw

from drone_sim import Course, Race

W, H, FPS = 960, 720, 30
FOCAL = W * 0.55
COURSE = "free_flight"
REPLY_DELAY = {"takeoff": 2.0, "land": 1.8, "flip": 1.0}


class Sim:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        self.sock.bind(("127.0.0.1", 8889))
        self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.client = None
        self.sdk = self.streaming = False
        self.race = Race(Course.load(COURSE))
        self.rc, self.rc_time = [0, 0, 0, 0], 0.0
        self.lock = threading.Lock()

    # --- command channel ---------------------------------------------------
    def reply(self, addr, text, delay=0.0):
        threading.Timer(delay, lambda: self.sock.sendto(text.encode(), addr)).start()

    def command_loop(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
            except OSError:
                continue
            cmd = data.decode(errors="ignore").strip()
            self.client = addr[0]
            with self.lock:
                self.handle(cmd, addr)

    def handle(self, cmd, addr):
        if cmd == "command":
            self.sdk = True
            return self.reply(addr, "ok")
        if not self.sdk:
            return
        if cmd.startswith("rc "):
            try:
                self.rc = [max(-100, min(100, int(v))) for v in cmd.split()[1:5]]
                self.rc_time = time.time()
            except ValueError:
                pass
            return
        if cmd in ("streamon", "streamoff"):
            self.streaming = cmd == "streamon"
            return self.reply(addr, "ok")
        if cmd == "battery?":
            return self.reply(addr, str(int(self.race.drone.battery)))
        if cmd == "wifi?":
            return self.reply(addr, "90")
        if cmd.startswith("speed "):
            return self.reply(addr, "ok")
        if cmd in ("takeoff", "land", "emergency") or cmd.startswith("flip "):
            result = self.race.command(cmd)
            return self.reply(addr, result, REPLY_DELAY.get(cmd.split()[0], 0) if result == "ok" else 0)
        self.reply(addr, f"unknown command: {cmd}")

    def step(self, dt):
        rc = self.rc if time.time() - self.rc_time < 1 else [0, 0, 0, 0]
        self.race.step(rc, dt)

    def state_string(self):
        fields = {**self.race.drone.telemetry(), "agx": "0.00", "agy": "0.00", "agz": "-1000.00"}
        return "".join(f"{k}:{v};" for k, v in fields.items()) + "\r\n"

    # --- rendering ---------------------------------------------------------
    def render(self):
        d = self.race.drone
        img = Image.new("RGB", (W, H), (122, 170, 222))
        draw = ImageDraw.Draw(img)
        a, th, ph = math.radians(d.yaw), math.radians(-d.pitch), math.radians(d.roll)
        sa, ca, st, ct, sp, cp = math.sin(a), math.cos(a), math.sin(th), math.cos(th), math.sin(ph), math.cos(ph)
        cx, cy, cz = d.x, d.y, d.z + 0.05

        def to_cam(p):
            dx, dy, dz = p[0] - cx, p[1] - cy, p[2] - cz
            xc, zc = dx * ca - dy * sa, dx * sa + dy * ca
            return xc, dz * ct + zc * st, zc * ct - dz * st

        def rot(sx, sy):
            return W / 2 + sx * cp + sy * sp, H / 2 - sx * sp + sy * cp

        def to_screen(xc, yc, zc):
            return rot(FOCAL * xc / zc, -FOCAL * yc / zc)

        def segment(p1, p2, fill, width=0.0):  # width in metres; 0 = hairline
            c1, c2 = to_cam(p1), to_cam(p2)
            near = 0.05
            if c1[2] < near and c2[2] < near:
                return
            if c1[2] < near or c2[2] < near:
                t = (near - c1[2]) / (c2[2] - c1[2])
                clip = tuple(c1[i] + (c2[i] - c1[i]) * t for i in range(3))
                c1, c2 = (clip, c2) if c1[2] < near else (c1, clip)
            w = max(1, int(FOCAL * width / max((c1[2] + c2[2]) / 2, 0.5)))
            draw.line([to_screen(*c1), to_screen(*c2)], fill=fill, width=min(w, 24))

        hy = -FOCAL * st / ct  # horizon height relative to centre, before roll
        big = W * 3
        draw.polygon([rot(-big, hy), rot(big, hy), rot(big, big), rot(-big, big)], fill=(64, 110, 62))

        gx, gy, n = round(cx / 2) * 2, round(cy / 2) * 2, 30
        for i in range(-n, n + 1, 2):
            segment((gx + i, gy - n, 0), (gx + i, gy + n, 0), (92, 140, 88))
            segment((gx - n, gy + i, 0), (gx + n, gy + i, 0), (92, 140, 88))

        sx, sy, _ = self.race.course.start
        pad = (255, 150, 40)
        for p1, p2 in [((-.5, -.5), (.5, -.5)), ((.5, -.5), (.5, .5)), ((.5, .5), (-.5, .5)), ((-.5, .5), (-.5, -.5)),
                       ((-.2, -.3), (-.2, .3)), ((.2, -.3), (.2, .3)), ((-.2, 0), (.2, 0))]:
            segment((sx + p1[0], sy + p1[1], 0.01), (sx + p2[0], sy + p2[1], 0.01), pad, 0.04)

        things = [(g.x, g.y, "gate", g) for g in self.race.course.gates] + \
                 [(p.x, p.y, "pillar", p) for p in self.race.course.pillars]
        colors = [(255, 90, 90), (255, 200, 60), (90, 200, 255), (190, 120, 255), (90, 255, 160)]
        for x, y, kind, obj in sorted(things, key=lambda t: -math.hypot(t[0] - cx, t[1] - cy)):
            if kind == "gate":
                color = colors[self.race.course.gates.index(obj) % len(colors)]
                for a3, b3 in obj.bars():
                    segment(a3, b3, color, 0.08)
            else:
                segment((x, y, 0), (x, y, obj.height), (150, 158, 172), obj.radius * 2)

        label = "PRACTICE MODE - simulated drone" + ("   CRASH!" if self.race.crash_timer is not None else "")
        draw.text((14, H - 26), label, fill=(255, 255, 255))
        return img

    # --- main loop ---------------------------------------------------------
    def run(self):
        threading.Thread(target=self.command_loop, daemon=True).start()
        enc = av.CodecContext.create("libx264", "w")
        enc.width, enc.height, enc.pix_fmt = W, H, "yuv420p"
        enc.time_base, enc.framerate, enc.gop_size = Fraction(1, FPS), FPS, FPS
        enc.options = {"preset": "ultrafast", "tune": "zerolatency"}
        n, next_t = 0, time.perf_counter()
        while True:
            with self.lock:
                self.step(1 / FPS)
                state = self.state_string()
            if self.client and self.sdk:
                if n % 3 == 0:
                    self.out.sendto(state.encode(), (self.client, 8890))
                if self.streaming:
                    with self.lock:
                        img = self.render()
                    frame = av.VideoFrame.from_image(img).reformat(format="yuv420p")
                    frame.pts = n
                    for packet in enc.encode(frame):
                        data = bytes(packet)
                        for i in range(0, len(data), 1460):
                            self.out.sendto(data[i:i + 1460], (self.client, 11111))
            n += 1
            next_t += 1 / FPS
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()


if __name__ == "__main__":
    Sim().run()
