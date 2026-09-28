#!/usr/bin/env python3
"""Tello Pilot bridge.

Chrome can't speak raw UDP, so this small server sits between the browser and
the drone: it relays stick input and commands to the Tello over UDP, and turns
the drone's H.264 video into JPEG frames the browser can draw.

    python tello_bridge.py              # fly the real drone
    python tello_bridge.py --practice   # fly a simulated drone
"""
import argparse
import asyncio
import collections
import contextlib
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from io import BytesIO
from pathlib import Path

import av
from aiohttp import WSMsgType, web

HERE = Path(__file__).resolve().parent
CMD_PORT = 8889         # drone listens for commands here
STATE_PORT = 8890       # drone streams telemetry to us here
VIDEO_PORT = 11111      # drone streams H.264 video to us here
LOCAL_CMD_PORT = 9000   # our side of the command channel
RC_HZ = 20
RC_TIMEOUT = 0.5        # no stick input from the browser for this long -> hover
JPEG_QUALITY = 75

ALLOWED = re.compile(r"^(takeoff|land|emergency|flip [lrfb]|speed \d{2,3}|battery\?|wifi\?|streamon|streamoff)$")


def udp_socket(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if hasattr(socket, "SIO_UDP_CONNRESET"):
        sock.ioctl(socket.SIO_UDP_CONNRESET, False)
    sock.bind(("0.0.0.0", port))
    return sock


class Tello:
    def __init__(self, ip, on_log):
        self.addr = (ip, CMD_PORT)
        self.on_log = on_log
        self.loop = None
        self.cmd = None
        self.rc = [0, 0, 0, 0]
        self.rc_time = 0.0
        self.telemetry = {}
        self.last_reply = self.last_state = self.last_frame = 0.0
        self.last_stream_req = 0.0
        self.was_connected = False
        self.pending = collections.deque()  # (show_reply, sent_at) per command awaiting a reply
        self.frame_queues = set()

    @property
    def connected(self):
        return time.time() - max(self.last_reply, self.last_state) < 3.5

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self.cmd = udp_socket(LOCAL_CMD_PORT)
        # Plain threads rather than asyncio datagram endpoints: on Windows an ICMP
        # "port unreachable" (drone still booting) permanently stops asyncio's UDP reader.
        threading.Thread(target=self._listen, args=(self.cmd, self._on_reply), daemon=True).start()
        threading.Thread(target=self._listen, args=(udp_socket(STATE_PORT), self._on_state), daemon=True).start()
        threading.Thread(target=self._video_thread, daemon=True).start()
        self.tasks = [asyncio.create_task(self._link_task()), asyncio.create_task(self._rc_task())]

    # --- sending -----------------------------------------------------------
    def _send(self, text, show_reply):
        self.pending.append((show_reply, time.time()))
        self.cmd.sendto(text.encode(), self.addr)

    def user_command(self, text):
        if not ALLOWED.match(text):
            self.on_log(f"Blocked unknown command: {text!r}")
            return
        if text in ("land", "emergency"):
            self.rc = [0, 0, 0, 0]
        self._send(text, True)
        if text == "emergency":  # this one must not get lost
            self._send(text, False)

    def set_rc(self, values):
        try:
            self.rc = [max(-100, min(100, int(v))) for v in values[:4]]
            self.rc_time = time.time()
        except (TypeError, ValueError):
            pass

    # --- receiving ---------------------------------------------------------
    def _listen(self, sock, handler):
        while True:
            try:
                data = sock.recv(2048)
            except OSError:
                time.sleep(0.05)  # ICMP errors surface here; keep listening
                continue
            self.loop.call_soon_threadsafe(handler, data)

    def _on_reply(self, data):
        self.last_reply = time.time()
        text = data.decode(errors="ignore").strip()
        while self.pending and self.last_reply - self.pending[0][1] > 15:
            self.pending.popleft()
        show = self.pending.popleft()[0] if self.pending else text != "ok"
        if show:
            self.on_log(f"Drone: {text}")

    def _on_state(self, data):
        fields = {}
        for part in data.decode(errors="ignore").strip().split(";"):
            key, _, value = part.partition(":")
            try:
                fields[key] = float(value)
            except ValueError:
                pass
        if fields:
            self.telemetry = fields
            self.last_state = time.time()

    def _video_thread(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.bind(("0.0.0.0", VIDEO_PORT))
        sock.settimeout(1)
        codec = av.CodecContext.create("h264", "r")
        while True:
            try:
                data = sock.recv(4096)
            except (socket.timeout, OSError):
                continue
            try:
                for packet in codec.parse(data):
                    for frame in codec.decode(packet):
                        self._publish(frame)
            except Exception:
                continue  # partial frames before the first keyframe are expected

    def _publish(self, frame):
        self.last_frame = time.time()
        if not self.frame_queues:
            return
        buf = BytesIO()
        frame.to_image().save(buf, "JPEG", quality=JPEG_QUALITY)
        self.loop.call_soon_threadsafe(self._fanout, buf.getvalue())

    def _fanout(self, jpeg):
        for q in self.frame_queues:
            if q.full():
                q.get_nowait()  # viewer is behind: drop the stale frame
            q.put_nowait(jpeg)

    # --- background loops --------------------------------------------------
    async def _link_task(self):
        while True:
            now = time.time()
            if now - max(self.last_reply, self.last_state) > 2.5:
                self.pending.clear()  # nothing heard for a while: earlier sends went unanswered
                self._send("command", False)  # enter SDK mode / reconnect
            connected = self.connected
            if connected and not self.was_connected:
                self.on_log("Connected to Tello")
                self._send("streamon", False)
                self.last_stream_req = now
            elif not connected and self.was_connected:
                self.on_log("Lost connection to Tello")
            self.was_connected = connected
            if connected and now - self.last_frame > 4 and now - self.last_stream_req > 4:
                self._send("streamon", False)
                self.last_stream_req = now
            await asyncio.sleep(1)

    async def _rc_task(self):
        # Streaming rc continuously doubles as the keepalive (Tello lands after 15 s of silence).
        while True:
            await asyncio.sleep(1 / RC_HZ)
            if not self.connected:
                continue
            v = self.rc if time.time() - self.rc_time < RC_TIMEOUT else (0, 0, 0, 0)
            self.cmd.sendto(f"rc {v[0]} {v[1]} {v[2]} {v[3]}".encode(), self.addr)

    def status(self):
        now = time.time()
        return {
            "t": "status",
            "connected": self.connected,
            "state": now - self.last_state < 2,
            "video": now - self.last_frame < 2,
            "telemetry": self.telemetry,
        }


async def ws_handler(request):
    app = request.app
    tello = app["tello"]
    ws = web.WebSocketResponse(heartbeat=5, compress=False)  # JPEGs don't compress
    await ws.prepare(request)
    app["clients"].add(ws)
    frames = asyncio.Queue(maxsize=1)
    tello.frame_queues.add(frames)

    async def pump_video():
        with contextlib.suppress(ConnectionError):
            while True:
                await ws.send_bytes(await frames.get())

    async def pump_status():
        with contextlib.suppress(ConnectionError):
            while True:
                await ws.send_json({**tello.status(), "practice": app["practice"]})
                await asyncio.sleep(0.2)

    pumps = [asyncio.create_task(pump_video()), asyncio.create_task(pump_status())]
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = msg.json()
            if data.get("t") == "rc":
                tello.set_rc(data.get("v", []))
            elif data.get("t") == "cmd":
                tello.user_command(str(data.get("c", "")).strip())
    except Exception:
        pass
    finally:
        for p in pumps:
            p.cancel()
        tello.frame_queues.discard(frames)
        app["clients"].discard(ws)
        tello.set_rc([0, 0, 0, 0])  # controller went away: hover
    return ws


async def index(request):
    return web.FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-store"})


def local_ip(target):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((target, 1))
        return s.getsockname()[0]
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description="Browser controller for the DJI/Ryze Tello")
    ap.add_argument("--drone-ip", default="192.168.10.1")
    ap.add_argument("--port", type=int, default=8080, help="web page port")
    ap.add_argument("--practice", action="store_true", help="fly a simulated drone instead")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    sim = None
    if args.practice:
        args.drone_ip = "127.0.0.1"
        sim = subprocess.Popen([sys.executable, str(HERE / "fake_tello.py")])

    app = web.Application()
    app["clients"] = set()
    app["practice"] = args.practice

    async def send_log(ws, msg):
        with contextlib.suppress(ConnectionError):
            await ws.send_json({"t": "log", "m": msg})

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
        for ws in list(app["clients"]):
            asyncio.ensure_future(send_log(ws, msg))

    tello = Tello(args.drone_ip, log)
    app["tello"] = tello

    def quiet_resets(loop, context):
        # Windows logs a traceback whenever a browser tab closes its socket; that's normal.
        if not isinstance(context.get("exception"), ConnectionResetError):
            loop.default_exception_handler(context)

    async def on_startup(app):
        asyncio.get_running_loop().set_exception_handler(quiet_resets)
        await tello.start()
        url = f"http://localhost:{args.port}"
        print(f"\n  Tello Pilot is running{' (PRACTICE MODE)' if args.practice else ''}")
        print(f"  Open in Chrome:  {url}")
        lan = local_ip(args.drone_ip)
        if lan and not lan.startswith("127."):
            print(f"  From a phone on the same Wi-Fi:  http://{lan}:{args.port}")
        print("  Press Ctrl+C here to stop.\n", flush=True)
        if not args.no_browser:
            webbrowser.open(url)

    async def on_shutdown(app):
        if tello.connected:
            tello.user_command("land")  # never leave it hovering unattended
            await asyncio.sleep(0.2)
        if sim:
            sim.terminate()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static", HERE / "static")
    try:
        web.run_app(app, host="0.0.0.0", port=args.port, print=None)
    except OSError as e:
        print(f"\nCould not open a network port ({e}).\n"
              "Is Tello Pilot (or another Tello app) already running? Close it and try again.")
        if sim:
            sim.terminate()
        sys.exit(1)


if __name__ == "__main__":
    main()
