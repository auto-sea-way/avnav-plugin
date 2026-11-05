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

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:  # AvNav loads this file by path, so aswplugin is not importable otherwise
    sys.path.insert(0, HERE)
# a plugin update in AvNav runs this file again without a restart: drop the old aswplugin modules
for name in [n for n in sys.modules if n == "aswplugin" or n.startswith("aswplugin.")]:
    del sys.modules[name]

from aswplugin import install  # noqa: E402
from aswplugin.route import DEFAULT_NAME, FORBIDDEN, LegError, call_legs, end_points, join_legs, route_gpx, safe_name  # noqa: E402

GRAPH_MB = 1430
MISSING = "Map data is not installed. On the chart page press To here, then Download map data: about 540 MB, use Wi-Fi."
AVNAV_STATUS = {"missing": "INACTIVE", "downloading": "STARTED", "starting": "STARTED", "ready": "NMEA", "error": "ERROR"}
DEFAULT_OPTIONS = {"shore_buffer": 0.0, "canals": True}
NO_ROUTE = "No route to save. Make a route first."
REPLACED = "A newer route was made, maybe on another device. Make this route again."
ANSI = re.compile(r"\x1b\[[0-9;]*m")
try:  # Linux; looked up here because the forked child must not load anything
    PRCTL = ctypes.CDLL(None).prctl
except (AttributeError, OSError, TypeError):  # other systems: the plugin still loads and preflight says why not
    PRCTL = None


def die_with_parent():
    # Linux PR_SET_PDEATHSIG: asw gets SIGTERM when the run() thread that started it ends, even if AvNav is killed.
    PRCTL(1, signal.SIGTERM)


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
        self.routes_dir = os.path.join(api.getDataDir(), "routes")
        with open(os.path.join(HERE, "plugin.json")) as f:
            self.asw_version = json.load(f)["aswVersion"]
        self.warnings = []
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.child = self.port = self.key = self.dl = None
        self.started_at = self.restart_at = 0
        self.want_start = False
        self.pending = None  # (id, points) of the last computed route; a newer route call replaces it
        self.route_id = 0
        self.update_info = None  # {"from", "to", "error"} while another asw version is on offer
        self.status = {"state": "starting", "detail": "Starting", "warnings": list(self.warnings), "downloadedMB": 0,
                       "update": None}
        api.registerRestart(self.stop)

    # --- status and child process -------------------------------------------------

    def set(self, state, detail, mb=0):
        new = {"state": state, "detail": detail, "warnings": list(self.warnings), "downloadedMB": mb,
               "update": self.update_info}
        if new != self.status:
            self.status = new
            self.api.setStatus(AVNAV_STATUS[state], " ".join([detail] + self.warnings))

    def run(self):
        # own event per run: AvNav's shouldStopMainThread() is True until it has stored the new thread,
        # and an old loop must not see the event of a newer run
        self.stopping = stopping = threading.Event()
        self.api.registerRequestHandler(self.handle)  # AvNav drops the handler when the plugin is disabled
        install.cleanup_stale(self.graph)
        errors, self.warnings = install.check_system(sys.platform, platform.machine(), install.mem_total())
        if errors:
            self.set("error", " ".join(errors))
            stopping.wait()  # returning would make AvNav replace the status with "plugin run finished"
            return
        self.check_program()
        if os.path.exists(self.bin) and os.path.exists(self.graph):
            self.want_start = True
            self.set("starting", "Starting asw")
        else:
            self.set("missing", MISSING)
        failed = None
        while not stopping.wait(1):
            try:  # the loop must keep running whatever tick does
                self.tick()
                failed = None
            except Exception as e:
                if str(e) != failed:
                    self.api.error("asw control failed: %s", e)
                failed = str(e)

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
        if self.stopping.is_set() or (self.dl and self.dl.is_alive()):
            return
        if c is None:
            if self.want_start or (self.restart_at and time.monotonic() >= self.restart_at):
                self.restart_at = 0
                self.start()
            return
        code = c.poll()
        if code is not None:
            self.child = None
            why = "killed by signal %d" % -code if code < 0 else "exit code %d" % code
            line = self.last_log_line()
            if os.path.exists(self.graph):
                self.set("error", "asw stopped (%s)%s" % (why, ": " + line if line else ""))
                self.restart_at = max(time.monotonic(), self.started_at + 30)
            else:  # never retry a graph download the sailor did not ask for
                self.set("error", install.download_stopped(line, why))
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
        self.want_start = False
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        key = secrets.token_hex(16)
        cmd = [self.bin, "serve", "--host", "127.0.0.1", "--port", str(port), "--graph", self.graph,
               "--graph-url", install.release_url(self.asw_version, "asw.graph.zst")]
        try:
            with open(self.log_path, "wb") as log:
                io = dict(env=dict(os.environ, ASW_API_KEY=key),  # env keeps the key out of ps
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
                try:
                    child = self.api.safeSpawn(cmd, kill_signal=signal.SIGTERM, **io)
                except (AttributeError, TypeError):  # before AvNav 20261102: not there, or a stub that raises TypeError
                    child = subprocess.Popen(cmd, preexec_fn=die_with_parent if PRCTL else None, **io)
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
            install.cleanup_stale(self.graph)
            machine = platform.machine()
            errors, warnings = install.preflight(self.dir, sys.platform, machine, install.mem_total(),
                                                 shutil.disk_usage(self.dir).free, os.path.exists(self.graph))
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

    def check_program(self):
        """A new plugin zip keeps the asw program that is installed, so routes work at sea right after an update.
        If this plugin version wants another program, offer it; only the sailor starts the download."""
        have = install.program_version(self.bin)
        self.update_info = {"from": have, "to": self.asw_version, "error": None} \
            if have and have != self.asw_version else None

    def update(self):
        with self.lock:
            if self.update_info and self.status["state"] == "ready" and not (self.dl and self.dl.is_alive()):
                self.set("downloading", "Downloading asw %s" % self.update_info["to"])
                self.dl = threading.Thread(target=self.update_thread, daemon=True, name="asw update")
                self.dl.start()
        return self.status

    def update_thread(self):
        # ponytail: assumes the new program reads the installed map data. A release with a new
        # graph format also needs a map download; asw would refuse the old file and report it.
        url = install.release_url(self.asw_version, install.binary_asset(platform.machine()))
        try:
            install.download_binary(url, self.bin)  # asw keeps running, and the file changes only if all went well
        except Exception as e:
            self.update_info = dict(self.update_info, error=str(e))
            self.set("ready", "Ready")
            return
        self.update_info = None
        self.kill()
        self.want_start = True  # the run() thread starts asw, so die_with_parent ties it to that thread
        self.set("starting", "Starting asw")

    # --- HTTP API -------------------------------------------------------------------

    def handle(self, url, handler, args):
        try:
            arg = lambda k: (args.get(k) or [None])[0] if args else None  # noqa: E731
            if url == "status":
                return self.status
            if url == "download":
                return self.download()
            if url == "update":
                return self.update()
            if url == "route":
                return self.route(arg("data"))
            if url == "save":
                return self.save(arg("id"), arg("name"), arg("overwrite") == "1")
            if url == "options":
                return self.options(arg("data"))
            return {"ok": False, "error": "Unknown request: %s" % url}
        except Exception as e:
            self.api.error("request %s failed: %s", url, e)
            return {"ok": False, "error": str(e)}

    def route(self, data):
        """Computes the route from d["from"] to d["to"] and keeps it as the pending route; save() writes it."""
        if self.status["state"] != "ready":
            return {"ok": False, "error": "asw is not ready: %s" % self.status["detail"]}
        try:
            d = json.loads(data)
            sb, canals = d.get("shore_buffer", 0), d.get("canals", True)
            points = end_points(d["from"], d["to"])
            valid = isinstance(canals, bool) \
                and not isinstance(sb, bool) and 0 <= float(sb) <= 5 \
                and all(-90 <= p["lat"] <= 90 and -180 <= p["lon"] <= 180 for p in points)
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            return {"ok": False, "error": "Invalid route request."}
        query = {"shore_buffer": float(sb), "canals": str(canals).lower()}
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
        with self.lock:
            self.route_id += 1
            self.pending = (str(self.route_id), pts)
            rid = self.route_id
        return {"ok": True, "id": rid, "distance_nm": nm, "points": len(pts), "land": land,
                "name": time.strftime(DEFAULT_NAME)}  # local time of the AvNav server

    def save(self, rid, name, overwrite):
        """Writes the pending route rid; two devices can use AvNav at once, so it must be the route this one made."""
        if not safe_name(name):
            return {"ok": False, "error": "Invalid route name. It must not be empty, start with \".\" or contain "
                                          "any of these characters: %s" % " ".join(FORBIDDEN)}
        path = os.path.join(self.routes_dir, name + ".gpx")
        with self.lock:
            if self.pending is None:
                return {"ok": False, "error": NO_ROUTE}
            if rid != self.pending[0]:
                return {"ok": False, "error": REPLACED}
            if os.path.exists(path) and not overwrite:
                return {"ok": False, "exists": True}
            os.makedirs(self.routes_dir, exist_ok=True)
            write_atomic(path, route_gpx(name, self.pending[1]))
        return {"ok": True, "name": name}

    def options(self, data):
        if not data:  # AvNav keeps parameters as strings, for example "True"
            try:
                shore_buffer = float(self.api.getConfigValue("shore_buffer", DEFAULT_OPTIONS["shore_buffer"]))
            except (TypeError, ValueError):
                shore_buffer = DEFAULT_OPTIONS["shore_buffer"]
            return {"shore_buffer": shore_buffer,
                    "canals": str(self.api.getConfigValue("canals", DEFAULT_OPTIONS["canals"])).lower() == "true"}
        try:
            d = json.loads(data)
            o = {k: d.get(k, v) for k, v in DEFAULT_OPTIONS.items()}
        except (ValueError, AttributeError):
            return {"ok": False, "error": "Invalid options."}
        if not (isinstance(o["canals"], bool)
                and isinstance(o["shore_buffer"], (int, float)) and not isinstance(o["shore_buffer"], bool)
                and 0 <= o["shore_buffer"] <= 5):
            return {"ok": False, "error": "Invalid options."}
        self.api.saveConfigValues(o)
        return o

