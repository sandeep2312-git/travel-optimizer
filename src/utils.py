from math import radians, sin, cos, sqrt, atan2

def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat/2)**2 + cos(radians(lat1))*cos(radians(lat2))*sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

def travel_minutes_km(dist_km: float, mode: str = "drive") -> int:
    # Simple heuristic speeds (tune later)
    speed_kmh = {"walk": 4.5, "transit": 18.0, "drive": 28.0}.get(mode, 28.0)
    mins = (dist_km / speed_kmh) * 60.0
    # add fixed overhead (parking/waiting)
    overhead = {"walk": 3, "transit": 10, "drive": 8}.get(mode, 8)
    return int(round(mins + overhead))
