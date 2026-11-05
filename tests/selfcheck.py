import os, sys, tempfile, threading, time, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "asw"))
from aswplugin.route import join_legs, length_nm, route_gpx, call_legs, LegError, safe_name, end_points, DEFAULT_NAME
import plugin
from aswplugin import install
from aswplugin.install import binary_asset, release_url, preflight, check_system, download_stopped, cleanup_stale, download_binary, mem_total

wps = [{"lat": 36.0, "lon": 28.0, "name": n} for n in "ABC"]
wps[1].update(lat=36.1, lon=28.1); wps[2].update(lat=36.2, lon=28.2)
ab = {"geometry": {"type": "LineString", "coordinates": [[28.0, 36.0], [28.05, 36.05], [28.1, 36.1]]},
      "distance_nm": 7.04, "shore_buffer_nm": 0.1, "land_legs": []}
bc = {"geometry": {"type": "LineString", "coordinates": [[28.1, 36.1], [28.12, 36.12], [28.15, 36.15], [28.2, 36.2]]},
      "distance_nm": 6.51, "shore_buffer_nm": 0.1, "land_legs": []}

def test_join_two_legs():
    pts, nm, land = join_legs(wps, [ab, bc])
    assert [p["name"] for p in pts] == ["A", "asw 1", "B", "asw 2", "asw 3", "C"]
    assert pts[1] == {"lat": 36.05, "lon": 28.05, "name": "asw 1"}
    assert nm == round(length_nm(pts), 1) == 15.4 and land == []  # the points, not the legs' distance_nm (13.55)

def test_length_like_avnav():  # AvNav: geodesy LatLonSpherical, R 6371 km, 1852 m per nm
    assert round(length_nm([{"lat": 0, "lon": 0}, {"lat": 1, "lon": 0}]), 4) == 60.0405
    assert length_nm([{"lat": 36, "lon": 28}]) == 0

def test_land_waypoints_named():
    a = dict(ab, land_legs=[0]); b = dict(bc, land_legs=[2])
    assert join_legs(wps, [a, b])[2] == ["A", "C"]
    # B on land seen from both legs: listed once
    a = dict(ab, land_legs=[1]); b = dict(bc, land_legs=[0])
    assert join_legs(wps, [a, b])[2] == ["B"]

ends = end_points({"lat": 36.0, "lon": 28.0}, {"lat": "36.2", "lon": 28.2})  # what Plugin.route passes
ad = {"geometry": {"type": "LineString", "coordinates": [[28.0, 36.0], [28.05, 36.05], [28.1, 36.1], [28.2, 36.2]]},
      "distance_nm": 13.55, "shore_buffer_nm": 0.1, "land_legs": []}

def test_one_leg():
    assert ends[1] == {"lat": 36.2, "lon": 28.2, "name": "Destination"}
    pts, nm, land = join_legs(ends, [ad])
    assert [p["name"] for p in pts] == ["Start", "asw 1", "asw 2", "Destination"]
    assert nm == 15.4 and land == []
    assert join_legs(ends, [dict(ad, land_legs=[0])])[2] == ["Start"]
    assert join_legs(ends, [dict(ad, land_legs=[2])])[2] == ["Destination"]
    assert join_legs(ends, [dict(ad, land_legs=[2, 0])])[2] == ["Start", "Destination"]
    assert join_legs(ends, [dict(ad, land_legs=[1])])[2] == []

def test_safe_name():
    for ok in ("asw 2026-10-30 1405", "Bodrum trip", "Ä" * 100, time.strftime(DEFAULT_NAME)):
        assert safe_name(ok), ok
    for bad in ("", "  ", ".hidden", "a/b", "a\\b", "a\0b", "a:b", "a?b", 'a"b', "a*b", "a<b", "a>b", "a|b", "a\nb", "a\tb", "x" * 201, "Ä" * 101, None, 5):
        assert not safe_name(bad), bad

def test_leg_without_route_reported():
    get = lambda a, b: (404, {"error": "no route"}) if a["name"] == "B" else (200, ab)
    try:
        call_legs(get, wps); assert False
    except LegError as e:
        assert str(e) == "B → C: no route"
    seen = []
    assert call_legs(lambda a, b: seen.append((a["name"], b["name"])) or (200, ab), wps) == [ab, ab]
    assert seen == [("A", "B"), ("B", "C")]

def test_gpx_roundtrip():
    p = [{"lat": 36.0, "lon": 28.0, "name": "A & <B>"}, {"lat": 36.5, "lon": 28.5, "name": "asw 1"}]
    root = ET.fromstring(route_gpx("Trip <1>", p))
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    rtes = root.findall("g:rte", ns)
    assert len(rtes) == 1 and rtes[0].find("g:name", ns).text == "Trip <1>"
    got = [(float(e.get("lat")), float(e.get("lon")), e.find("g:name", ns).text) for e in rtes[0].findall("g:rtept", ns)]
    assert got == [(36.0, 28.0, "A & <B>"), (36.5, 28.5, "asw 1")]

def test_unsupported_machine():
    assert binary_asset("armv7l") is None and binary_asset("aarch64") == "asw-linux-arm64"
    assert binary_asset("arm64") == "asw-linux-arm64" and binary_asset("x86_64") == binary_asset("amd64") == "asw-linux-amd64"
    assert preflight("/x", "linux", "armv7l", 4e9, 5e9)[0] == \
        ["This system is not supported (linux armv7l). asw needs 64-bit Linux on arm64 or x86_64."]
    for system in ("darwin", "win32"):
        assert preflight("/x", system, "arm64", 4e9, 5e9)[0] == \
            ["This system is not supported (%s arm64). asw needs 64-bit Linux on arm64 or x86_64." % system]
    assert release_url("0.11.0", "asw-linux-arm64") == "https://github.com/auto-sea-way/asw/releases/download/v0.11.0/asw-linux-arm64"

def test_preflight_space_and_ram():
    assert preflight("/x", "linux", "aarch64", 4e9, 1e9)[0]
    assert preflight("/x", "linux", "aarch64", 4e9, 1e9, graph_present=True) == ([], [])  # only the binary to get
    errs, warns = preflight("/x", "linux", "aarch64", 1e9, 5e9)
    assert not errs and warns
    assert preflight("/x", "linux", "x86_64", 4e9, 5e9) == ([], [])
    assert preflight("/x", "linux", "aarch64", 0, 5e9) == ([], [])
    assert isinstance(mem_total(), int)

def test_check_system():  # Plugin.run checks this at start; free space waits for the download
    assert check_system("linux", "aarch64", 4e9) == ([], [])
    assert check_system("linux", "aarch64", 1e9) == ([], ["Less than 2 GB of RAM: routing will be slow."])
    assert check_system("darwin", "arm64", 1e9) == (
        ["This system is not supported (darwin arm64). asw needs 64-bit Linux on arm64 or x86_64."],
        ["Less than 2 GB of RAM: routing will be slow."])

def test_download_stopped():
    tail = ". Check the internet connection and press Try again."
    assert download_stopped("3: Connection refused (os error 111)", "exit code 1") == \
        "The map data download stopped (Connection refused" + ")" + tail
    assert download_stopped("HTTP status client error (404 Not Found) for url (https://x/asw.graph.zst)", "exit code 1") == \
        "The map data download stopped (HTTP status client error (404 Not Found))" + tail
    assert download_stopped("", "killed by signal 9") == "The map data download stopped (killed by signal 9)" + tail
    assert len(download_stopped("x" * 500, "exit code 1")) < 200

def test_stale_tmp_removed():
    d = tempfile.mkdtemp()
    for n in ("asw.graph", "asw.graph.tmp"):
        open(os.path.join(d, n), "w").write("x")
    cleanup_stale(os.path.join(d, "asw.graph"))
    assert os.listdir(d) == ["asw.graph"]
    cleanup_stale(os.path.join(d, "asw.graph"))

def test_download_binary():
    d = tempfile.mkdtemp()
    src, dest = os.path.join(d, "src"), os.path.join(d, "asw")
    open(src, "w").write("not a program")
    try:
        download_binary("file://" + src, dest); assert False
    except RuntimeError as e:
        assert str(e) == "this system cannot run asw"
    assert not os.path.exists(dest) and not os.path.exists(dest + ".part")
    try:
        download_binary("file://" + d + "/missing", dest); assert False
    except RuntimeError as e:
        assert str(e) == "Download failed. Check the internet connection and try again."
    assert not os.path.exists(dest + ".part")
    open(src, "w").write("#!/bin/sh\necho asw 0.0.0\n")
    download_binary("file://" + src, dest)
    assert os.access(dest, os.X_OK) and not os.path.exists(dest + ".part")

class FakeApi:  # stores values as strings, like AvNav
    def __init__(self, d):
        self.d, self.values = d, {}
    def getDataDir(self): return self.d
    def registerRestart(self, stop): pass
    def getConfigValue(self, key, default=None): return self.values.get(key, default)
    def saveConfigValues(self, values): self.values.update({k: str(v) for k, v in values.items()})
    def setStatus(self, *a): pass

def test_options_in_plugin_parameters():
    api = FakeApi(tempfile.mkdtemp())
    p = plugin.Plugin(api)
    assert p.options(None) == {"shore_buffer": 0.0, "canals": True}
    assert p.options('{"shore_buffer": 0.5, "canals": false}') == {"shore_buffer": 0.5, "canals": False}
    assert api.values == {"shore_buffer": "0.5", "canals": "False"}
    assert p.options(None) == {"shore_buffer": 0.5, "canals": False}
    api.values.update(shore_buffer="many", canals="true")  # a bad number gives the default, and the case does not matter
    assert p.options(None) == {"shore_buffer": 0.0, "canals": True}
    assert p.options('{"shore_buffer": 6}')["ok"] is False and api.values["shore_buffer"] == "many"

def test_start_uses_safespawn_when_avnav_has_it():
    class Child: pass
    calls = []
    api = FakeApi(tempfile.mkdtemp())
    p = plugin.Plugin(api)
    popen = plugin.subprocess.Popen
    plugin.subprocess.Popen = lambda cmd, **kw: calls.append(("popen", kw)) or Child()
    def stub(cmd, **kw): raise NotImplemented()  # like the stub in the AvNav 20261029 API: a TypeError
    try:
        p.start()  # older AvNav: no safeSpawn
        api.safeSpawn = stub
        p.start()
        api.safeSpawn = lambda cmd, **kw: calls.append(("safe", kw)) or Child()
        p.start()
    finally:
        plugin.subprocess.Popen = popen
    assert [c[0] for c in calls] == ["popen", "popen", "safe"]
    assert "preexec_fn" in calls[0][1] and "kill_signal" not in calls[0][1]
    assert calls[2][1]["kill_signal"] == plugin.signal.SIGTERM and "preexec_fn" not in calls[2][1]
    assert all(c[1]["env"]["ASW_API_KEY"] for c in calls) and calls[2][1]["env"]["ASW_API_KEY"] == p.key


def fake_program(path, version):
    with open(path, "w") as f:
        f.write("#!/bin/sh\necho 'asw %s'\n" % version)
    os.chmod(path, 0o755)

def test_program_version():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "asw")
    assert install.program_version(p) is None  # no file
    fake_program(p, "0.11.0")
    assert install.program_version(p) == "0.11.0"
    with open(p, "w") as f:
        f.write("#!/bin/sh\necho something else\n")
    assert install.program_version(p) is None

class FakeChild:
    def __init__(self): self.stopped = False
    def poll(self): return 0 if self.stopped else None
    def terminate(self): self.stopped = True
    def wait(self, timeout=None): return 0

def updatable_plugin(installed="0.11.0", wanted="0.11.1"):
    p = plugin.Plugin(FakeApi(tempfile.mkdtemp()))
    p.asw_version = wanted
    fake_program(p.bin, installed)
    open(p.graph, "w").close()
    p.check_program()
    p.child = FakeChild()
    p.set("ready", "Ready")
    return p

def test_update_is_offered_only_for_another_version():
    assert updatable_plugin().status["update"] == {"from": "0.11.0", "to": "0.11.1", "error": None}
    assert updatable_plugin("0.11.1", "0.11.1").status["update"] is None
    p = plugin.Plugin(FakeApi(tempfile.mkdtemp()))  # no program yet: this is the normal first download
    p.check_program()
    p.set("missing", "x")
    assert p.status["update"] is None

def with_download(fake, f):
    real = plugin.install.download_binary
    plugin.install.download_binary = fake
    try:
        f()
    finally:
        plugin.install.download_binary = real

def test_update_replaces_the_program_and_restarts_asw():
    p = updatable_plugin()
    old = p.child
    urls = []
    def fake(url, dest):
        urls.append(url)
        assert not old.stopped  # routes keep working during the download
        fake_program(dest, "0.11.1")
    def go():
        assert p.handle("update", None, None)["state"] == "downloading"
        p.dl.join()
    with_download(fake, go)
    assert urls == [install.release_url("0.11.1", install.binary_asset(plugin.platform.machine()))]
    assert old.stopped and p.child is None and p.want_start
    assert p.status["state"] == "starting" and p.status["update"] is None
    assert install.program_version(p.bin) == "0.11.1"

def test_failed_update_keeps_the_program_running():
    p = updatable_plugin()
    old = p.child
    def fake(url, dest): raise RuntimeError("Download failed. Check the internet connection and try again.")
    def go():
        p.update()
        p.dl.join()
    with_download(fake, go)
    assert not old.stopped and p.child is old and not p.want_start
    assert p.status["state"] == "ready"
    assert p.status["update"] == {"from": "0.11.0", "to": "0.11.1",
                                  "error": "Download failed. Check the internet connection and try again."}
    assert install.program_version(p.bin) == "0.11.0"
    done = []
    with_download(lambda url, dest: done.append(1), lambda: (p.update(), p.dl.join()))  # press it again: it tries again
    assert done == [1] and p.status["update"] is None

def test_update_without_a_new_version_does_nothing():
    p = updatable_plugin("0.11.1", "0.11.1")
    with_download(lambda url, dest: 1 / 0, p.update)
    assert p.dl is None and p.status["state"] == "ready"

def test_status_stays_downloading_during_an_update():
    p = updatable_plugin()
    release = threading.Event()
    def go():
        p.update()
        p.tick()  # the run() thread: asw is alive and the map is there, which would mean "ready"
        assert p.status["state"] == "downloading"
        release.set()
        p.dl.join()
    with_download(lambda url, dest: release.wait(5), go)

for k, f in list(globals().items()):
    if k.startswith("test_"): f()
print("ok")
