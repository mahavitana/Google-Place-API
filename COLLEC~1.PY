"""Collect businesses from OpenStreetMap via the Overpass API (free, no key)."""
import time
import requests
import db as dbm

# OSM tag keys that usually denote a real business/point of interest.
POI_KEYS = ["shop", "office", "craft", "amenity", "tourism", "healthcare", "leisure"]

# amenity values that are NOT businesses we want (filter out noise).
AMENITY_BLOCKLIST = {
    "bench", "waste_basket", "recycling", "bicycle_parking", "parking",
    "parking_space", "drinking_water", "fountain", "toilets", "shelter",
    "fire_hydrant", "grave_yard", "place_of_worship", "fire_station",
    "police", "post_box", "telephone", "clock", "bus_station",
}


def _build_query(cfg):
    o = cfg["osm"]
    timeout = o.get("timeout", 180)
    if o.get("use_bbox"):
        b = o["bbox"]  # [min_lat, min_lon, max_lat, max_lon]
        area_filter = f"({b[0]},{b[1]},{b[2]},{b[3]})"
        parts = []
        for k in POI_KEYS:
            parts.append(f'  node["{k}"]["name"]{area_filter};')
            parts.append(f'  way["{k}"]["name"]{area_filter};')
        body = "\n".join(parts)
        return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags;"
    else:
        name = o["area_name"]
        parts = []
        for k in POI_KEYS:
            parts.append(f'  node["{k}"]["name"](area.a);')
            parts.append(f'  way["{k}"]["name"](area.a);')
        body = "\n".join(parts)
        return (f'[out:json][timeout:{timeout}];\n'
                f'area["name"="{name}"]["boundary"="administrative"]->.a;\n'
                f'(\n{body}\n);\nout center tags;')


def _category(tags):
    for k in POI_KEYS:
        if k in tags:
            return f"{k}:{tags[k]}"
    return None


def _address(tags):
    parts = []
    for k in ["addr:housenumber", "addr:street", "addr:suburb", "addr:city",
              "addr:postcode"]:
        if tags.get(k):
            parts.append(tags[k])
    return ", ".join(parts) or None


def _wanted(tags):
    if "amenity" in tags and tags["amenity"] in AMENITY_BLOCKLIST:
        # keep only if it also carries another business key
        if not any(k in tags for k in POI_KEYS if k != "amenity"):
            return False
    return True


def run(cfg, conn):
    q = _build_query(cfg)
    endpoint = cfg["osm"].get("endpoint", "https://overpass-api.de/api/interpreter")
    print("[osm] querying Overpass ...")
    resp = requests.post(endpoint, data={"data": q}, timeout=cfg["osm"].get("timeout", 180) + 30)
    resp.raise_for_status()
    elements = resp.json().get("elements", [])
    print(f"[osm] received {len(elements)} raw elements")
    new = 0
    seen = 0
    for el in elements:
        tags = el.get("tags", {})
        if not tags.get("name") or not _wanted(tags):
            continue
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center", {})
            lat, lon = c.get("lat"), c.get("lon")
        phone = tags.get("phone") or tags.get("contact:phone") or tags.get("mobile")
        website = (tags.get("website") or tags.get("contact:website")
                   or tags.get("url") or tags.get("contact:facebook"))
        rec = {
            "source": "osm",
            "source_id": f"{el['type']}/{el['id']}",
            "name": tags.get("name"),
            "category": _category(tags),
            "address": _address(tags),
            "latitude": lat,
            "longitude": lon,
            "phone": phone,
            "website": website,
            "rating": None,
            "rating_count": None,
        }
        _, inserted = dbm.upsert_business(conn, rec)
        seen += 1
        new += 1 if inserted else 0
    conn.commit()
    print(f"[osm] stored {seen} businesses ({new} new)")
    return seen
