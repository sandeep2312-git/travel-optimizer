import time
import random
import requests

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
]

# Tags to fetch (Strict vs Relaxed)
TAGS_STRICT = [
    # Core
    "tourism=attraction",
    "tourism=museum",
    "leisure=park",
    "amenity=restaurant",
    "amenity=bar",

    # New categories
    "amenity=cafe",                # coffee
    "shop=mall",                   # shopping
    "shop=supermarket",            # shopping
    "tourism=viewpoint",           # viewpoints
    "tourism=gallery",             # museums-ish
]

TAGS_RELAXED = TAGS_STRICT + [
    # More nightlife/food variants
    "amenity=pub",
    "amenity=fast_food",
    "amenity=ice_cream",

    # Shopping expansion
    "shop=clothes",
    "shop=department_store",
    "shop=gift",
    "shop=convenience",

    # Nature expansion
    "leisure=garden",
    "leisure=nature_reserve",

    # Events-ish (OSM doesn’t always tag events consistently; these help)
    "amenity=theatre",
    "amenity=cinema",
    "amenity=arts_centre",

    # Culture / sights
    "historic=monument",
    "historic=memorial",
    "tourism=information",
]

# Category mapping rules (first match wins)
def categorize(tags: dict) -> str:
    amenity = tags.get("amenity")
    tourism = tags.get("tourism")
    leisure = tags.get("leisure")
    shop = tags.get("shop")
    historic = tags.get("historic")

    # Food
    if amenity in ("restaurant", "fast_food", "ice_cream"):
        return "food"

    # Coffee
    if amenity == "cafe":
        return "coffee"

    # Nightlife
    if amenity in ("bar", "pub"):
        return "nightlife"

    # Museums / culture
    if tourism in ("museum", "gallery"):
        return "museums"
    if amenity in ("theatre", "cinema", "arts_centre"):
        return "events"
    if historic in ("monument", "memorial"):
        return "museums"

    # Nature
    if leisure in ("park", "garden", "nature_reserve"):
        return "nature"
    if tourism == "attraction":
        return "nature"

    # Viewpoints
    if tourism == "viewpoint":
        return "viewpoints"

    # Shopping
    if shop in (
        "mall",
        "supermarket",
        "clothes",
        "department_store",
        "gift",
        "convenience",
    ):
        return "shopping"

    # Events-ish
    if tourism == "information":
        return "events"

    return "other"


def _build_query(lat: float, lon: float, radius_m: int, tags: list[str], include_ways: bool) -> str:
    parts = []
    for t in tags:
        k, v = t.split("=", 1)
        parts.append(f'node(around:{radius_m},{lat},{lon})["{k}"="{v}"];')
        if include_ways:
            parts.append(f'way(around:{radius_m},{lat},{lon})["{k}"="{v}"];')
            parts.append(f'relation(around:{radius_m},{lat},{lon})["{k}"="{v}"];')

    out_stmt = "out tags;" if not include_ways else "out tags center;"
    timeout = 20 if not include_ways else 25

    return f"""
[out:json][timeout:{timeout}];
(
  {''.join(parts)}
);
{out_stmt}
"""


def _request_with_retries(url: str, query: str, max_tries: int = 2) -> dict:
    last_err = None
    for attempt in range(1, max_tries + 1):
        try:
            r = requests.post(url, data={"data": query}, timeout=35)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(min(6, (2 ** attempt) + random.random()))
    raise last_err


def _try_endpoints(query: str) -> dict:
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            return _request_with_retries(endpoint, query, max_tries=2)
        except Exception as e:
            last_err = e
    raise last_err if last_err else RuntimeError("Overpass request failed")


def _elements_to_pois(data: dict) -> list[dict]:
    pois = []
    for el in data.get("elements", []):
        t = el.get("tags", {}) or {}
        name = t.get("name")
        if not name:
            continue

        # node has lat/lon; ways/relations have center
        if "lat" in el and "lon" in el:
            plat, plon = el["lat"], el["lon"]
        else:
            c = el.get("center") or {}
            plat, plon = c.get("lat"), c.get("lon")

        if plat is None or plon is None:
            continue

        category = categorize(t)

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

    # de-dupe
    seen = set()
    uniq = []
    for p in pois:
        key = (p["name"].strip().lower(), round(p["lat"], 4), round(p["lon"], 4))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)

    return uniq


def fetch_pois(
    lat: float,
    lon: float,
    radius_km: float = 8.0,
    limit: int = 120,
    relaxed: bool = True,
) -> list[dict]:
    """
    Strategy:
      1) Fast: nodes only
      2) If empty/too small: include ways/relations (more coverage)
      3) Try strict set as fallback
    """
    tags_primary = TAGS_RELAXED if relaxed else TAGS_STRICT

    # Overpass stability: keep radius sane
    radius_m = int(max(1000, min(20000, radius_km * 1000)))  # 1..20 km

    # 1) nodes-only (fast)
    q1 = _build_query(lat, lon, radius_m, tags_primary, include_ways=False)
    data1 = _try_endpoints(q1)
    pois1 = _elements_to_pois(data1)
    if len(pois1) >= 10:
        return pois1[:limit]

    # 2) include ways/relations (better coverage)
    q2 = _build_query(lat, lon, radius_m, tags_primary, include_ways=True)
    data2 = _try_endpoints(q2)
    pois2 = _elements_to_pois(data2)
    if len(pois2) >= 10:
        return pois2[:limit]

    # 3) last resort: strict tags
    q3 = _build_query(lat, lon, radius_m, TAGS_STRICT, include_ways=True)
    data3 = _try_endpoints(q3)
    pois3 = _elements_to_pois(data3)
    return pois3[:limit]
