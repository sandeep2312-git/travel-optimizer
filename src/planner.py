from typing import List, Dict
from .utils import haversine_km, travel_minutes_km
from .scorer import score_poi

def plan_itinerary(
    pois: List[Dict],
    days: int,
    budget: float,
    prefs: Dict[str, float],
    pace: str = "moderate",
    start_hour: int = 10,
    travel_mode: str = "drive",
) -> Dict:
    pace_to_slots = {"relaxed": 3, "moderate": 4, "packed": 5}
    slots_per_day = pace_to_slots.get(pace, 4)

    daily_time_budget = 8 * 60  # minutes
    remaining_budget = float(budget)
    remaining = [dict(p) for p in pois]

    itinerary = {"days": [], "total_cost": 0.0, "total_time_mins": 0, "remaining_budget": remaining_budget}
    total_cost = 0.0
    total_time = 0

    for d in range(days):
        day_items = []
        day_time = 0
        day_cost = 0.0
        current = None

        # Sort by preference score first (helps anchor)
        remaining.sort(key=lambda x: score_poi(x, prefs), reverse=True)

        for _ in range(slots_per_day):
            best_idx, best_value = None, -1e9

            for i, poi in enumerate(remaining):
                cost = float(poi.get("avg_cost", 0.0))
                dur = int(poi.get("visit_duration_mins", 60))
                if cost > remaining_budget:
                    continue

                travel_mins = 0
                travel_km = 0.0
                if current is not None:
                    travel_km = haversine_km(current["lat"], current["lon"], poi["lat"], poi["lon"])
                    travel_mins = travel_minutes_km(travel_km, travel_mode)

                # time feasibility includes travel + activity
                if day_time + travel_mins + dur > daily_time_budget:
                    continue

                # objective: score - travel penalty - cost penalty
                travel_penalty = travel_km * 0.5
                cost_penalty = cost * 0.02
                value = score_poi(poi, prefs) - travel_penalty - cost_penalty

                if value > best_value:
                    best_value, best_idx = value, i

            if best_idx is None:
                break

            chosen = remaining.pop(best_idx)

            # compute travel from current -> chosen
            travel_mins = 0
            travel_km = 0.0
            if current is not None:
                travel_km = haversine_km(current["lat"], current["lon"], chosen["lat"], chosen["lon"])
                travel_mins = travel_minutes_km(travel_km, travel_mode)

            chosen["_travel_from_prev_mins"] = travel_mins
            chosen["_travel_from_prev_km"] = round(travel_km, 2)

            day_items.append(chosen)
            current = chosen

            c = float(chosen.get("avg_cost", 0.0))
            t = int(chosen.get("visit_duration_mins", 60))
            remaining_budget -= c
            day_cost += c
            day_time += travel_mins + t

        # Build a timeline with start/end times
        t_cursor = start_hour * 60
        timeline = []
        prev = None
        for item in day_items:
            if prev is not None:
                t_cursor += int(item.get("_travel_from_prev_mins", 0))

            start = t_cursor
            end = start + int(item.get("visit_duration_mins", 60))
            t_cursor = end

            timeline.append({
                "name": item.get("name", "Unknown"),
                "category": item.get("category", "other"),
                "start_min": start,
                "end_min": end,
                "travel_from_prev_mins": int(item.get("_travel_from_prev_mins", 0)),
                "travel_from_prev_km": float(item.get("_travel_from_prev_km", 0.0)),
                "lat": item.get("lat"),
                "lon": item.get("lon"),
                "avg_cost": float(item.get("avg_cost", 0.0))
            })
            prev = item

        itinerary["days"].append({
            "day": d + 1,
            "items": day_items,
            "timeline": timeline,
            "day_cost": round(day_cost, 2),
            "day_time_mins": int(day_time)
        })

        total_cost += day_cost
        total_time += int(day_time)

    itinerary["total_cost"] = round(total_cost, 2)
    itinerary["total_time_mins"] = int(total_time)
    itinerary["remaining_budget"] = round(remaining_budget, 2)
    return itinerary
