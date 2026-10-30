"""AvNav glue: runs asw as a child process and serves the plugin HTTP API."""
import ctypes
import json
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape as xml_escape

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # AvNav loads this file by path, so aswplugin is not importable otherwise

from aswplugin import install  # noqa: E402
from aswplugin.route import SUFFIX, LegError, call_legs, checked_name, gpx_key, join_legs, leg_route, points_key, \
    route_gpx, route_off, unchecked_name  # noqa: E402
from aswplugin.unchecked import Registry  # noqa: E402

GRAPH_MB = 1430
MISSING = "Map data is not installed. Press Download to get it: about 540 MB, use Wi-Fi."
DAMAGED = "The list of unchecked asw routes was damaged and reset. Check every asw route before you use it."
AVNAV_STATUS = {"missing": "INACTIVE", "downloading": "STARTED", "starting": "STARTED", "ready": "NMEA", "error": "ERROR"}
DEFAULT_OPTIONS = {"shore_buffer": 0.0, "canals": True, "arctic": False}
WATCH_FAILED = "Cannot watch the active route on this AvNav version."
ALARM = "asw-unchecked"
ALARM_TEXT = "This asw route is not checked yet. Check it and press Mark as checked in the Auto route dialog."
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def die_with_parent():
    # Linux PR_SET_PDEATHSIG: asw gets SIGTERM when the run() thread that started it ends, even if AvNav is killed.
    ctypes.CDLL(None).prctl(1, signal.SIGTERM)


def safe_name(name):
    return isinstance(name, str) and name.strip() != "" and not name.startswith(".") \
        and not any(c in name for c in "/\\\0")


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


class Plugin:
    @classmethod
    def pluginInfo(cls):
        return {"description": "Auto route around land with auto-sea-way"}

    def __init__(self, api):
        self.api = api
        self.dir = os.path.join(api.getDataDir(), "asw")
        os.makedirs(self.dir, exist_ok=True)
        self.bin = os.path.join(self.dir, "asw")
        self.graph = os.path.join(self.dir, "asw.graph")
        self.log_path = os.path.join(self.dir, "asw.log")
        self.options_path = os.path.join(self.dir, "options.json")
        self.routes_dir = os.path.join(api.getDataDir(), "routes")
        with open(os.path.join(HERE, "plugin.json")) as f:
            self.asw_version = json.load(f)["aswVersion"]
        self.registry = Registry(os.path.join(self.dir, "unchecked.json"))
        self.warnings = [DAMAGED] if self.registry.damaged else []
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.child = self.port = self.key = self.dl = None
        self.started_at = self.restart_at = 0
        self.want_start = False
        self.keys = {}  # route file path -> ((mtime, size), points key)
        self.alarm = None  # name of the unchecked route the watcher last stopped while its alarm runs
        self.status = {"state": "starting", "detail": "Starting", "warnings": list(self.warnings), "downloadedMB": 0}
        api.registerRestart(self.stop)

    # --- status and child process -------------------------------------------------

    def set(self, state, detail, mb=0):
        new = {"state": state, "detail": detail, "warnings": list(self.warnings), "downloadedMB": mb}
        if new != self.status:
            self.status = new
            self.api.setStatus(AVNAV_STATUS[state], detail)

    def run(self):
        # own event per run: AvNav's shouldStopMainThread() is True until it has stored the new thread,
        # and an old loop must not see the event of a newer run
        self.stopping = stopping = threading.Event()
        self.api.registerRequestHandler(self.handle)  # AvNav drops the handler when the plugin is disabled
        install.cleanup_stale(self.graph)
        if os.path.exists(self.bin) and os.path.exists(self.graph):
            self.want_start = True
            self.set("starting", "Starting asw")
        else:
            self.set("missing", MISSING)
        n = 0
        while not stopping.wait(1):
            self.tick()
            n += 1
            if n % 2:
                self.watch()

    def stop(self):
        self.stopping.set()
        self.kill()

    def kill(self):
        c, self.child = self.child, None
        if c:
            c.terminate()
            try:
                c.wait(5)
            except subprocess.TimeoutExpired:
                c.kill()
                c.wait()

    def tick(self):
        c = self.child
        if self.stopping.is_set():
            return
        if c is None:
            if self.want_start or (self.restart_at and time.monotonic() >= self.restart_at):
                self.want_start, self.restart_at = False, 0
                self.start()
            return
        code = c.poll()
        if code is not None:
            self.child = None
            why = "killed by signal %d" % -code if code < 0 else "exit code %d" % code
            line = self.last_log_line()
            self.set("error", "asw stopped (%s)%s" % (why, ": " + line if line else ""))
            if os.path.exists(self.graph):  # never retry a graph download the sailor did not ask for
                self.restart_at = max(time.monotonic(), self.started_at + 30)
            return
        if not os.path.exists(self.graph):
            try:
                mb = os.path.getsize(self.graph + ".tmp") // 1_000_000
            except OSError:
                mb = 0
            self.set("downloading", "Downloading map data: %d MB of about %d MB unpacked" % (mb, GRAPH_MB), mb)
        elif self.status["state"] == "ready" or self.ready():
            self.set("ready", "Ready")
        else:
            self.set("starting", "Loading map data")

    def start(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        key = secrets.token_hex(16)
        cmd = [self.bin, "serve", "--host", "127.0.0.1", "--port", str(port), "--graph", self.graph,
               "--graph-url", install.release_url(self.asw_version, "asw.graph.zst")]
        try:
            with open(self.log_path, "wb") as log:
                child = subprocess.Popen(cmd, env=dict(os.environ, ASW_API_KEY=key),  # env keeps the key out of ps
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                         preexec_fn=die_with_parent)
        except (OSError, subprocess.SubprocessError) as e:
            self.set("error", "Cannot start asw: %s" % e)
            return
        self.port, self.key, self.started_at, self.child = port, key, time.monotonic(), child
        self.set("starting", "Starting asw")

    def ready(self):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/ready" % self.port, timeout=1) as r:
                return r.status == 200
        except OSError:
            return False

    def last_log_line(self):
        try:
            with open(self.log_path, "rb") as f:
                f.seek(max(0, os.path.getsize(self.log_path) - 4096))
                lines = [ANSI.sub("", l).strip() for l in f.read().decode("utf-8", "replace").splitlines()]
        except OSError:
            return ""
        return next((l for l in reversed(lines) if l and " INFO " not in l), "")

    # --- unchecked route watcher ------------------------------------------------------
    # Uses AvNav's router object in this process (avnrouter.py AVNRouter.getCurrentLeg/setCurrentLeg): an internal
    # interface, so every failure only adds a status warning and routing keeps working.

    def watch(self):
        try:
            from avnav_worker import AVNWorker
            router = AVNWorker.findHandlerByName("AVNRouter")
            leg = router.getCurrentLeg()
            with self.lock:
                try:
                    self.registry.sync(self.route_keys())
                except OSError as e:  # sync updates the list in memory before it saves, so still stop below
                    self.api.error("cannot save the unchecked route list: %s", e)
                cur = leg_route(leg.getJson()) if leg is not None else None
                stop = cur is not None and self.registry.is_unchecked(*cur)
            if stop:
                def off(l, _):  # runs under the router's lock: keep a route the sailor switched to meanwhile
                    return type(l)(route_off(l.getJson())) if l is not None and leg_route(l.getJson()) == cur else l
                router.setCurrentLeg(changeFunction=off)
                router.wakeUp()
                self.api.log("stopped unchecked asw route %s", cur[0])
                self.api.startAlarm(ALARM, defaultCategory="critical", message=ALARM_TEXT)
                self.alarm = cur[0]
            elif cur is not None and self.alarm:
                self.clear_alarm()
            self.warn(WATCH_FAILED, False)
        except Exception as e:
            if WATCH_FAILED not in self.warnings:
                self.api.error("cannot watch the active route: %s", e)
            self.warn(WATCH_FAILED, True)

    def route_keys(self):
        """{route name: points key} for every readable .gpx in the routes dir; a damaged file is skipped."""
        keys, cache = {}, {}
        try:
            files = [f for f in os.listdir(self.routes_dir) if f.endswith(".gpx")]
        except OSError:
            files = []
        for f in files:
            path = os.path.join(self.routes_dir, f)
            try:
                st = os.stat(path)
                sig = (st.st_mtime_ns, st.st_size)
                old = self.keys.get(path)
                if old is None or old[0] != sig:
                    with open(path, "rb") as fh:
                        old = (sig, gpx_key(fh.read()))
            except OSError:
                continue
            cache[path] = old
            if old[1] is not None:
                keys[f[:-4]] = old[1]
        self.keys = cache
        return keys

    def clear_alarm(self):
        self.alarm = None
        try:
            self.api.clearAlarm(ALARM)
        except Exception as e:
            self.api.error("cannot clear alarm: %s", e)

    def warn(self, text, on):
        if on != (text in self.warnings):
            self.warnings = [w for w in self.warnings if w != text] + ([text] if on else [])
            st = self.status
            self.set(st["state"], st["detail"], st["downloadedMB"])

    # --- download -----------------------------------------------------------------

    def download(self):
        with self.lock:
            if self.status["state"] in ("missing", "error") and self.child is None \
                    and not (self.dl and self.dl.is_alive()):
                self.set("downloading", "Checking this system")
                self.dl = threading.Thread(target=self.download_thread, daemon=True, name="asw download")
                self.dl.start()
        return self.status

    def download_thread(self):
        try:
            machine = platform.machine()
            errors, warnings = install.preflight(self.dir, machine, install.mem_total(),
                                                 shutil.disk_usage(self.dir).free)
            self.warnings += [w for w in warnings if w not in self.warnings]
            if errors:
                self.set("error", " ".join(errors))
                return
            if not os.path.exists(self.bin):
                self.set("downloading", "Downloading the asw program")
                install.download_binary(install.release_url(self.asw_version, install.binary_asset(machine)), self.bin)
            self.want_start = True  # the run() thread starts asw, so die_with_parent ties it to that thread
        except RuntimeError as e:
            self.set("error", str(e))
        except Exception as e:
            self.set("error", "Download failed: %s" % e)

    # --- HTTP API -------------------------------------------------------------------

    def handle(self, url, handler, args):
        try:
            arg = lambda k: (args.get(k) or [None])[0] if args else None  # noqa: E731
            data = json.loads(arg("data")) if arg("data") else None
            if url == "status":
                return self.status
            if url == "download":
                return self.download()
            if url == "route":
                return self.route(data)
            if url == "check":
                return self.check(arg("name"))
            if url == "unchecked":
                return {"names": [e["name"] for e in self.registry.routes]}
            if url == "options":
                return self.options(data)
            return {"ok": False, "error": "Unknown request: %s" % url}
        except Exception as e:
            self.api.error("request %s failed: %s", url, e)
            return {"ok": False, "error": str(e)}

    def route(self, d):
        if self.status["state"] != "ready":
            return {"ok": False, "error": "asw is not ready: %s" % self.status["detail"]}
        try:
            name, sb, canals, arctic = d["name"], d.get("shore_buffer", 0), d.get("canals", True), d.get("arctic", False)
            points = [{"lat": float(p["lat"]), "lon": float(p["lon"]), "name": str(p.get("name") or "WP %d" % (i + 1))}
                      for i, p in enumerate(d["points"])]
            valid = safe_name(name) and len(points) >= 2 and isinstance(canals, bool) and isinstance(arctic, bool) \
                and not isinstance(sb, bool) and 0 <= float(sb) <= 5 \
                and all(-90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180 for p in points)
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            return {"ok": False, "error": "Invalid route request."}
        query = {"shore_buffer": float(sb), "canals": str(canals).lower(), "arctic": str(arctic).lower()}
        port, key = self.port, self.key

        def get(a, b):
            q = dict(query, **{"from": "%r,%r" % (a["lat"], a["lon"]), "to": "%r,%r" % (b["lat"], b["lon"])})
            req = urllib.request.Request("http://127.0.0.1:%d/route?%s" % (port, urllib.parse.urlencode(q)),
                                         headers={"X-Api-Key": key})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return r.status, json.load(r)
            except urllib.error.HTTPError as e:
                try:
                    return e.code, json.load(e)
                except ValueError:
                    return e.code, {}

        try:
            legs = call_legs(get, points)
        except LegError as e:
            return {"ok": False, "error": str(e)}
        except (OSError, ValueError):
            return {"ok": False, "error": "asw did not answer. Try again."}
        pts, nm, land = join_legs(points, legs)
        out = unchecked_name(name)
        with self.lock:
            os.makedirs(self.routes_dir, exist_ok=True)
            write_atomic(os.path.join(self.routes_dir, out + ".gpx"), route_gpx(out, pts))
            self.registry.add(out, points_key(pts))
        return {"ok": True, "name": out, "distance_nm": nm, "land": land, "points": len(pts)}

    def check(self, name):
        if not safe_name(name) or not (name.endswith(SUFFIX) or any(e["name"] == name for e in self.registry.routes)):
            return {"ok": False, "error": "This is not an unchecked asw route."}
        new = checked_name(name)
        src, dst = (os.path.join(self.routes_dir, n + ".gpx") for n in (name, new))
        with self.lock:
            if not os.path.exists(src):
                return {"ok": False, "error": "Route %s not found." % name}
            if os.path.exists(dst):
                return {"ok": False, "error": "A route named %s already exists. Rename or delete it first." % new}
            with open(src, encoding="utf-8") as f:
                text = f.read()
            # keep the name inside the file equal to the file name
            write_atomic(dst, text.replace("<name>%s</name>" % xml_escape(name), "<name>%s</name>" % xml_escape(new), 1))
            os.unlink(src)
            self.registry.remove(name)
        if self.alarm == name:
            self.clear_alarm()
        return {"ok": True, "name": new}

    def options(self, d):
        if d is None:
            try:
                with open(self.options_path) as f:
                    saved = json.load(f)
                return {k: saved.get(k, v) for k, v in DEFAULT_OPTIONS.items()}
            except (OSError, ValueError, AttributeError):
                return dict(DEFAULT_OPTIONS)
        o = {k: d.get(k, v) for k, v in DEFAULT_OPTIONS.items()}
        if not (isinstance(o["canals"], bool) and isinstance(o["arctic"], bool)
                and isinstance(o["shore_buffer"], (int, float)) and not isinstance(o["shore_buffer"], bool)
                and 0 <= o["shore_buffer"] <= 5):
            return {"ok": False, "error": "Invalid options."}
        write_atomic(self.options_path, json.dumps(o))
        return o

