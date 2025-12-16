import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

def _build_query(lat: float, lon: float, radius_m: int) -> str:
    tags = [
        "tourism=attraction",
        "tourism=museum",
        "leisure=park",
        "amenity=restaurant",
        "amenity=bar",
    ]

    parts = []
    for t in tags:
        k, v = t.split("=", 1)
        parts.append(f'node(around:{radius_m},{lat},{lon})["{k}"="{v}"];')
        parts.append(f'way(around:{radius_m},{lat},{lon})["{k}"="{v}"];')
        parts.append(f'relation(around:{radius_m},{lat},{lon})["{k}"="{v}"];')

    return f"""
    [out:json][timeout:25];
    (
      {''.join(parts)}
    );
    out center tags;
    """

def fetch_pois(lat: float, lon: float, radius_km: float = 8.0, limit: int = 120) -> list[dict]:
    q = _build_query(lat, lon, int(radius_km * 1000))
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=40)
    r.raise_for_status()
    data = r.json()

    pois = []
    for el in data.get("elements", []):
        tags = el.get("tags", {}) or {}
        name = tags.get("name")
        if not name:
            continue

        if "lat" in el and "lon" in el:
            plat, plon = el["lat"], el["lon"]
        else:
            center = el.get("center") or {}
            plat, plon = center.get("lat"), center.get("lon")
        if plat is None or plon is None:
            continue

        category = "other"
        if tags.get("amenity") == "restaurant":
            category = "food"
        elif tags.get("amenity") == "bar":
            category = "nightlife"
        elif tags.get("tourism") == "museum":
            category = "museums"
        elif tags.get("leisure") == "park":
            category = "nature"
        elif tags.get("tourism") == "attraction":
            category = "nature"

        pois.append({
            "name": name,
            "category": category,
            "lat": float(plat),
            "lon": float(plon),
            "avg_cost": 15,
            "visit_duration_mins": 90,
            "rating": 4.3
        })

    # de-dupe by name
    seen = set()
    uniq = []
    for p in pois:
        key = p["name"].strip().lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)

    return uniq[:limit]
