import os, sys, tempfile, time, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "asw"))
from aswplugin.route import join_legs, length_nm, route_gpx, call_legs, LegError, safe_name, end_points, DEFAULT_NAME
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

for k, f in list(globals().items()):
    if k.startswith("test_"): f()
print("ok")
