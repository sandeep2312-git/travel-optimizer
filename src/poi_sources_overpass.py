import time, random, json, os
import requests

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]

DEFAULT_TAGS = [
    "tourism=attraction",
    "tourism=museum",
    "leisure=park",
    "amenity=restaurant",
    "amenity=bar",
]

def _build_query_nodes_only(lat: float, lon: float, radius_m: int, tags: list[str]) -> str:
    # nodes-only is MUCH faster than node+way+relation
    parts = []
    for t in tags:
        k, v = t.split("=", 1)
        parts.append(f'node(around:{radius_m},{lat},{lon})["{k}"="{v}"];')
    return f"""
[out:json][timeout:20];
(
  {''.join(parts)}
);
out tags;
"""

def _request_with_retries(url: str, query: str, max_tries: int = 2) -> dict:
    last_err = None
    for attempt in range(1, max_tries + 1):
        try:
            r = requests.post(url, data={"data": query}, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(min(6, (2 ** attempt) + random.random()))
    raise last_err

def fetch_pois(lat: float, lon: float, radius_km: float = 4.0, limit: int = 120, tags: list[str] | None = None) -> list[dict]:
    tags = tags or DEFAULT_TAGS
    radius_m = int(max(1000, min(15000, radius_km * 1000)))  # clamp 1..15km
    query = _build_query_nodes_only(lat, lon, radius_m, tags)

    data = None
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            data = _request_with_retries(endpoint, query, max_tries=2)
            if data:
                break
        except Exception as e:
            last_err = e

    if data is None:
        raise last_err if last_err else RuntimeError("Overpass request failed")

    pois = []
    for el in data.get("elements", []):
        t = el.get("tags", {}) or {}
        name = t.get("name")
        if not name:
            continue
        plat, plon = el.get("lat"), el.get("lon")
        if plat is None or plon is None:
            continue

        category = "other"
        if t.get("amenity") == "restaurant":
            category = "food"
        elif t.get("amenity") == "bar":
            category = "nightlife"
        elif t.get("tourism") == "museum":
            category = "museums"
        elif t.get("leisure") == "park":
            category = "nature"
        elif t.get("tourism") == "attraction":
            category = "nature"

        pois.append({
            "name": name,
            "category": category,
            "lat": float(plat),
            "lon": float(plon),
            "avg_cost": 15,
            "visit_duration_mins": 90,
            "rating": 4.3,
        })

    # de-dupe
    seen = set()
    uniq = []
    for p in pois:
        key = (p["name"].strip().lower(), round(p["lat"], 4), round(p["lon"], 4))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)

    return uniq[:limit]
