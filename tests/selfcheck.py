import os, sys, tempfile, xml.etree.ElementTree as ET
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "asw"))
from aswplugin.route import join_legs, route_gpx, unchecked_name, checked_name, points_key
from aswplugin.unchecked import Registry

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

path = os.path.join(tempfile.mkdtemp(), "unchecked.json")
r = Registry(path)
U = "A (asw, unchecked)"

def test_registry_follows_rename():
    r.add(U, "k1"); r.sync({"Renamed": "k1"})
    assert r.is_unchecked("Renamed", "k1") and not Registry(path).is_unchecked(U, "zz")
    r.remove("Renamed")

def test_registry_follows_edit():
    r.add(U, "k1"); r.sync({U: "k2"})
    assert r.is_unchecked(U, "k2") and Registry(path).is_unchecked("x", "k2")

def test_registry_gone_entry_kept():
    r.sync({"Other": "k9"})
    assert r.is_unchecked(U, "k2")

def test_registry_remove_and_persist():
    r.remove(U); assert not Registry(path).is_unchecked(U, "k2")
    assert Registry(os.path.join(tempfile.mkdtemp(), "none.json")).routes == []

for k, f in list(globals().items()):
    if k.startswith("test_"): f()
print("ok")
