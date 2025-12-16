import time
import random
import requests

# Multiple public Overpass endpoints (fallback if one times out)
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

def _build_query(lat: float, lon: float, radius_m: int, tags: list[str]) -> str:
    parts = []
    for t in tags:
        k, v = t.split("=", 1)
        parts.append(f'node(around:{radius_m},{lat},{lon})["{k}"="{v}"];')
        parts.append(f'way(around:{radius_m},{lat},{lon})["{k}"="{v}"];')
        parts.append(f'relation(around:{radius_m},{lat},{lon})["{k}"="{v}"];')

    # 'out center' gives coords for ways/relations
    return f"""
[out:json][timeout:25];
(
  {''.join(parts)}
);
out tags center;
"""

def _request_with_retries(url: str, query: str, max_tries: int = 3) -> dict:
    last_err = None
    for attempt in range(1, max_tries + 1):
        try:
            r = requests.post(url, data={"data": query}, timeout=45)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            # exponential backoff + jitter
            sleep_s = min(10, (2 ** attempt) + random.random())
            time.sleep(sleep_s)
    raise last_err

def fetch_pois(lat: float, lon: float, radius_km: float = 6.0, limit: int = 120, tags: list[str] | None = None) -> list[dict]:
    """
    Robust Overpass POI fetcher with:
    - fallback endpoints
    - retries with backoff
    - configurable tags + radius
    """
    tags = tags or DEFAULT_TAGS

    # Overpass is sensitive: smaller radius helps a LOT
    radius_m = int(max(1000, min(30000, radius_km * 1000)))  # clamp 1km..30km
    query = _build_query(lat, lon, radius_m, tags)

    data = None
    last_err = None

    # Try each endpoint
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            data = _request_with_retries(endpoint, query, max_tries=3)
            if data:
                break
        except Exception as e:
            last_err = e

    if data is None:
        raise last_err if last_err else RuntimeError("Overpass request failed with unknown error")

    elements = data.get("elements", [])
    pois = []

    for el in elements:
        t = el.get("tags", {}) or {}
        name = t.get("name")
        if not name:
            continue

        # node has lat/lon, ways/relations have 'center'
        if "lat" in el and "lon" in el:
            plat, plon = el["lat"], el["lon"]
        else:
            c = el.get("center") or {}
            plat, plon = c.get("lat"), c.get("lon")

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
            # heuristic defaults (user can edit in UI)
            "avg_cost": 15,
            "visit_duration_mins": 90,
            "rating": 4.3,
        })

    # de-dupe by (name, rounded coords)
    seen = set()
    uniq = []
    for p in pois:
        key = (p["name"].strip().lower(), round(p["lat"], 4), round(p["lon"], 4))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)

    return uniq[:limit]
