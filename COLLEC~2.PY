"""Collect businesses from the Google Places API (New) using a grid of
Nearby Search calls across the target area, one per place type."""
import math
import time
import requests
import db as dbm

NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
FIELDS = ",".join([
    "places.id", "places.displayName", "places.formattedAddress",
    "places.location", "places.nationalPhoneNumber",
    "places.internationalPhoneNumber", "places.websiteUri",
    "places.rating", "places.userRatingCount", "places.primaryTypeDisplayName",
])


def _grid_points(center_lat, center_lon, half_extent_m, cell_radius_m):
    """Return circle centers that tile a square area. Cells overlap slightly so
    nothing falls between them (step = radius * sqrt(2))."""
    step = cell_radius_m * math.sqrt(2) * 0.9
    lat_step = step / 111_320.0
    lon_step = step / (111_320.0 * math.cos(math.radians(center_lat)))
    n = int(math.ceil(half_extent_m / step))
    pts = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            pts.append((center_lat + i * lat_step, center_lon + j * lon_step))
    return pts


def _search_cell(api_key, lat, lon, radius, types):
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELDS,
    }
    body = {
        "includedTypes": types,
        "maxResultCount": 20,
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lon},
                       "radius": float(radius)}
        },
    }
    r = requests.post(NEARBY_URL, headers=headers, json=body, timeout=30)
    if r.status_code != 200:
        print(f"[google] HTTP {r.status_code}: {r.text[:200]}")
        return []
    return r.json().get("places", [])


def run(cfg, conn):
    g = cfg["google"]
    api_key = (g.get("api_key") or "").strip()
    if not api_key:
        print("[google] no api_key set - skipping Google collection")
        return 0
    center = g["center"]
    grid = g["grid"]
    radius = grid["cell_radius_m"]
    pts = _grid_points(center[0], center[1], grid["half_extent_m"], radius)
    # One type per request keeps results focused (each call caps at 20 results).
    types = g["included_types"]
    total_calls = len(pts) * len(types)
    print(f"[google] grid={len(pts)} cells x {len(types)} types = {total_calls} API calls")
    print("[google] (each call is a billable Nearby Search request)")
    seen, new, call = 0, 0, 0
    for (lat, lon) in pts:
        for t in types:
            call += 1
            try:
                places = _search_cell(api_key, lat, lon, radius, [t])
            except Exception as e:
                print(f"[google] call error: {e}")
                places = []
            for p in places:
                loc = p.get("location", {})
                rec = {
                    "source": "google",
                    "source_id": p.get("id"),
                    "name": (p.get("displayName") or {}).get("text"),
                    "category": (p.get("primaryTypeDisplayName") or {}).get("text") or t,
                    "address": p.get("formattedAddress"),
                    "latitude": loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                    "phone": p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber"),
                    "website": p.get("websiteUri"),
                    "rating": p.get("rating"),
                    "rating_count": p.get("userRatingCount"),
                }
                if not rec["source_id"]:
                    continue
                _, inserted = dbm.upsert_business(conn, rec)
                seen += 1
                new += 1 if inserted else 0
            if call % 50 == 0:
                conn.commit()
                print(f"[google] {call}/{total_calls} calls done, {new} new so far")
            time.sleep(0.05)
    conn.commit()
    print(f"[google] stored {seen} place rows ({new} new)")
    return seen
