import os, sys, tempfile, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "asw"))
from aswplugin.route import join_legs, route_gpx, unchecked_name, checked_name, points_key, call_legs, LegError, \
    gpx_key, leg_route, route_off
from aswplugin.unchecked import Registry
from aswplugin.install import binary_asset, release_url, preflight, cleanup_stale, download_binary, mem_total

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
    assert nm == round(ab["distance_nm"] + bc["distance_nm"], 1) and land == []

def test_land_waypoints_named():
    a = dict(ab, land_legs=[0]); b = dict(bc, land_legs=[2])
    assert join_legs(wps, [a, b])[2] == ["A", "C"]
    # B on land seen from both legs: listed once
    a = dict(ab, land_legs=[1]); b = dict(bc, land_legs=[0])
    assert join_legs(wps, [a, b])[2] == ["B"]

def test_leg_without_route_reported():
    get = lambda a, b: (404, {"error": "no route"}) if a["name"] == "B" else (200, ab)
    try:
        call_legs(get, wps); assert False
    except LegError as e:
        assert str(e) == "B → C: no route"
    seen = []
    assert call_legs(lambda a, b: seen.append((a["name"], b["name"])) or (200, ab), wps) == [ab, ab]
    assert seen == [("A", "B"), ("B", "C")]

def test_names():
    assert unchecked_name("Bodrum") == "Bodrum (asw, unchecked)"
    assert checked_name("Bodrum (asw, unchecked)") == "Bodrum (asw)"

def test_points_key_ignores_names():
    p = join_legs(wps, [ab, bc])[0]
    assert points_key(p) == points_key([dict(x, name="other") for x in p])
    assert points_key(p) != points_key(p[:-1])

def test_gpx_roundtrip():
    p = [{"lat": 36.0, "lon": 28.0, "name": "A & <B>"}, {"lat": 36.5, "lon": 28.5, "name": "asw 1"}]
    root = ET.fromstring(route_gpx("Trip <1>", p))
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    rtes = root.findall("g:rte", ns)
    assert len(rtes) == 1 and rtes[0].find("g:name", ns).text == "Trip <1>"
    got = [(float(e.get("lat")), float(e.get("lon")), e.find("g:name", ns).text) for e in rtes[0].findall("g:rtept", ns)]
    assert got == [(36.0, 28.0, "A & <B>"), (36.5, 28.5, "asw 1")]

def test_gpx_key():
    p = join_legs(wps, [ab, bc])[0]
    assert gpx_key(route_gpx("R & 1", p)) == points_key(p)
    plain = '<gpx><rte><name>M</name><rtept lon="28.2" lat="36.8"><name>A</name></rtept></rte></gpx>'  # as AvNav writes
    assert gpx_key(plain) == points_key([{"lat": 36.8, "lon": 28.2}])
    for bad in ("", "<gpx><rte>", '<gpx><rte><rtept lat="x" lon="1"/></rte></gpx>', "<gpx/>"):
        assert gpx_key(bad) is None

def test_leg_route():
    pts = [{"lat": 36.0, "lon": 28.0, "name": "A"}, {"lat": 36.1, "lon": 28.1, "name": "B"}]
    leg = {"from": {"lat": 36.0, "lon": 28.0}, "to": dict(pts[1], routeName="R"), "name": "R", "active": True,
           "currentTarget": 1, "currentRoute": {"name": "R", "time": 0, "points": pts}}
    assert leg_route(leg) == ("R", points_key(pts))
    assert leg_route({}) is None and leg_route(dict(leg, active=False)) is None
    assert leg_route(dict(leg, anchorDistance=50)) is None and leg_route(dict(leg, currentRoute=None)) is None
    assert leg_route(dict(leg, currentRoute={"name": "R", "points": [{"lat": "x"}]})) == ("R", None)
    assert leg_route(dict(leg, **{"from": {}})) == ("R", points_key(pts))  # AvNav tests "is not None"
    off = route_off(leg)
    assert off["active"] is False and "currentRoute" not in off and "name" not in off
    assert off["to"] == pts[1] and off["from"] == leg["from"] and leg_route(off) is None
    assert leg["to"]["routeName"] == "R" and leg["currentRoute"]  # input left alone

path = os.path.join(tempfile.mkdtemp(), "unchecked.json")
r = Registry(path)
U = "A (asw, unchecked)"

def test_registry_follows_rename():
    r.add(U, "k1"); r.sync({"Renamed": "k1"})
    assert Registry(path).routes == [{"name": "Renamed", "key": "k1"}]
    r.remove("Renamed")

def test_registry_follows_edit():
    r.add(U, "k1"); r.sync({U: "k2"})
    assert Registry(path).routes == [{"name": U, "key": "k2"}]

def test_registry_gone_entry_kept():
    r.sync({"Other": "k9"})
    assert Registry(path).routes == [{"name": U, "key": "k2"}] and r.routes == Registry(path).routes

def test_registry_remove_and_persist():
    r.remove(U); assert Registry(path).routes == []
    assert Registry(os.path.join(tempfile.mkdtemp(), "none.json")).routes == []

def test_registry_damaged_file():
    for bad in ("", "{", "[]", '{"routes": [1]}'):
        p = os.path.join(tempfile.mkdtemp(), "u.json")
        open(p, "w").write(bad)
        d = Registry(p)
        assert d.routes == [] and d.damaged and os.path.exists(p + ".bad") and not os.path.exists(p)
    assert not Registry(p + ".none").damaged

def test_hint_name_always_unchecked():
    assert Registry(os.path.join(tempfile.mkdtemp(), "n.json")).is_unchecked("X (asw, unchecked)", "unknown")

def test_unsupported_machine():
    assert binary_asset("armv7l") is None and binary_asset("aarch64") == "asw-linux-arm64"
    assert binary_asset("arm64") == "asw-linux-arm64" and binary_asset("x86_64") == binary_asset("amd64") == "asw-linux-amd64"
    assert preflight("/x", "armv7l", 4e9, 5e9)[0]
    assert release_url("0.11.0", "asw-linux-arm64") == "https://github.com/auto-sea-way/asw/releases/download/v0.11.0/asw-linux-arm64"

def test_preflight_space_and_ram():
    assert preflight("/x", "aarch64", 4e9, 1e9)[0]
    errs, warns = preflight("/x", "aarch64", 1e9, 5e9)
    assert not errs and warns
    assert preflight("/x", "x86_64", 4e9, 5e9) == ([], [])
    assert preflight("/x", "aarch64", 0, 5e9) == ([], [])
    assert isinstance(mem_total(), int)

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

for k, f in list(globals().items()):
    if k.startswith("test_"): f()
print("ok")
