def score_poi(poi: dict, prefs: dict) -> float:
    """
    prefs example:
      {"nature": 0.8, "food": 0.4, "museums": 0.7, "nightlife": 0.2}
    """
    cat = poi.get("category", "").lower()
    base = float(poi.get("rating", 4.0))
    pref_boost = prefs.get(cat, 0.0) * 2.0  # weight preferences
    return base + pref_boost
