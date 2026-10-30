"""Join asw /route legs into one point list and write it as GPX."""
from xml.sax.saxutils import escape, quoteattr


def safe_name(name):
    """A route name usable as a file name in AvNav's routes dir."""
    return isinstance(name, str) and name.strip() != "" and name.isprintable() and len(name.encode()) <= 200 \
        and not name.startswith(".") and not any(c in name for c in "/\\")


class LegError(Exception):
    pass


def call_legs(get, waypoints):
    """get(a, b) -> (http status, json body) for one asw /route call."""
    legs = []
    for a, b in zip(waypoints, waypoints[1:]):
        status, body = get(a, b)
        if status != 200:
            raise LegError("%s → %s: %s" % (a["name"], b["name"], body.get("error") or "HTTP %d" % status))
        legs.append(body)
    return legs


def join_legs(waypoints, legs):
    """legs[i] is the asw /route JSON for waypoints[i] -> waypoints[i+1]."""
    points = [dict(waypoints[0])]
    nm, land, added = 0.0, [], 0
    for i, leg in enumerate(legs):
        coords = leg["geometry"]["coordinates"]
        nm += leg["distance_nm"]
        for j in leg.get("land_legs", []):
            for hit, wp in ((j == 0, waypoints[i]), (j == len(coords) - 2, waypoints[i + 1])):
                if hit and wp["name"] not in land:
                    land.append(wp["name"])
        for lon, lat in coords[1:-1]:
            added += 1
            points.append({"lat": lat, "lon": lon, "name": "asw %d" % added})
        points.append(dict(waypoints[i + 1]))
    land.sort(key=lambda n: [w["name"] for w in waypoints].index(n))
    return points, round(nm, 1), land


def route_gpx(name, points):
    pts = "".join(
        "<rtept lat=%s lon=%s><name>%s</name></rtept>\n"
        % (quoteattr(repr(p["lat"])), quoteattr(repr(p["lon"])), escape(p["name"]))
        for p in points
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="avnav-asw" xmlns="http://www.topografix.com/GPX/1/1">\n'
        "<rte>\n<name>%s</name>\n%s</rte>\n</gpx>\n" % (escape(name), pts)
    )
