from typing import List, Dict, Tuple
from .utils import haversine_km
from .scorer import score_poi

def plan_itinerary(
    pois: List[Dict],
    days: int,
    budget: float,
    prefs: Dict[str, float],
    pace: str = "moderate",
    start_hour: int = 10,
) -> Dict:
    """
    Simple greedy planner:
    - ranks POIs by (score - travel_penalty - cost_penalty)
    - fills each day until time/budget constraints
    """
    pace_to_slots = {"relaxed": 3, "moderate": 4, "packed": 5}
    slots_per_day = pace_to_slots.get(pace, 4)

    # daily time budget in minutes (excluding meals/travel; keep it simple)
    daily_time_budget = 8 * 60

    remaining_budget = float(budget)
    remaining = [dict(p) for p in pois]  # copy

    itinerary = {"days": [], "total_cost": 0.0, "total_time_mins": 0}
    total_cost = 0.0
    total_time = 0

    for d in range(days):
        day_plan = []
        day_time = 0
        day_cost = 0.0

        # choose a starting anchor as the best scored POI
        remaining.sort(key=lambda x: score_poi(x, prefs), reverse=True)
        current = None

        for _ in range(slots_per_day):
            if not remaining:
                break

            best_idx = None
            best_value = -1e9

            for i, poi in enumerate(remaining):
                cost = float(poi.get("avg_cost", 0.0))
                dur = int(poi.get("visit_duration_mins", 60))

                if cost > remaining_budget:
                    continue
                if day_time + dur > daily_time_budget:
                    continue

                travel_penalty = 0.0
                if current is not None:
                    dist = haversine_km(current["lat"], current["lon"], poi["lat"], poi["lon"])
                    travel_penalty = dist * 0.25  # tune this

                cost_penalty = cost * 0.02  # tune this
                value = score_poi(poi, prefs) - travel_penalty - cost_penalty

                if value > best_value:
                    best_value = value
                    best_idx = i

            if best_idx is None:
                break

            chosen = remaining.pop(best_idx)
            day_plan.append(chosen)
            current = chosen

            c = float(chosen.get("avg_cost", 0.0))
            t = int(chosen.get("visit_duration_mins", 60))
            remaining_budget -= c
            day_cost += c
            day_time += t

        itinerary["days"].append({
            "day": d + 1,
            "start_hour": start_hour,
            "items": day_plan,
            "day_cost": round(day_cost, 2),
            "day_time_mins": day_time
        })

        total_cost += day_cost
        total_time += day_time

    itinerary["total_cost"] = round(total_cost, 2)
    itinerary["total_time_mins"] = total_time
    itinerary["remaining_budget"] = round(remaining_budget, 2)
    return itinerary
