import pandas as pd
import streamlit as st
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

from src.planner import plan_itinerary
from src.poi_sources_overpass import fetch_pois
from src.export_pdf import itinerary_to_pdf
from src.export_ics import itinerary_to_ics


CITY_PRESETS = {
    "— Select a preset (recommended) —": None,
    "Denver, CO": (39.7392, -104.9903),
    "Dallas, TX": (32.7767, -96.7970),
    "Austin, TX": (30.2672, -97.7431),
    "New York, NY": (40.7128, -74.0060),
    "San Francisco, CA": (37.7749, -122.4194),
    "Los Angeles, CA": (34.0522, -118.2437),
    "Chicago, IL": (41.8781, -87.6298),
    "Seattle, WA": (47.6062, -122.3321),
}

# Expand categories (UI-level). Your Overpass fetcher must supply these categories to fully use them.
ALL_CATEGORIES = [
    "nature",
    "food",
    "museums",
    "nightlife",
    "coffee",
    "shopping",
    "viewpoints",
    "events",
]

# For special modes
MODE_CONFIG = {
    "Full day": {"start_hour": None, "pace": None, "force_categories": None},
    "Evening outing only": {"start_hour": 17, "pace": "relaxed", "force_categories": ["nature", "museums", "events", "nightlife"]},
    "Night dinner only": {"start_hour": 19, "pace": "relaxed", "force_categories": ["food", "nightlife", "coffee"]},
}


# ----------------------------
# Page setup
# ----------------------------
st.set_page_config(page_title="AI Travel Optimizer", layout="wide")
st.title("🧭 AI Travel Optimizer")
st.caption("A layman-friendly itinerary builder that balances time, budget, distance, and your interests.")


# ----------------------------
# Helpers
# ----------------------------
@st.cache_data(show_spinner=False)
def geocode_city(city: str):
    geolocator = Nominatim(user_agent="travel-optimizer-app (github-codespace)")
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.2)

    queries = [
        city.strip(),
        f"{city.strip()}, USA",
        f"{city.strip()}, United States",
    ]

    for q in queries:
        for _ in range(3):
            try:
                loc = geocode(q)
                if loc:
                    return float(loc.latitude), float(loc.longitude)
            except Exception:
                pass
    return None


@st.cache_data(show_spinner=False)
def load_pois_cached(lat: float, lon: float, radius_km: float, limit: int, relaxed: bool):
    return fetch_pois(lat=lat, lon=lon, radius_km=radius_km, limit=limit, relaxed=relaxed)


def normalize_poi_defaults(df: pd.DataFrame) -> pd.DataFrame:
    for col, default in [
        ("avg_cost", 15),
        ("visit_duration_mins", 90),
        ("rating", 4.3),
        ("category", "other"),
    ]:
        if col not in df.columns:
            df[col] = default

    df["name"] = df["name"].astype(str)
    df["avg_cost"] = pd.to_numeric(df["avg_cost"], errors="coerce").fillna(15).astype(float)
    df["visit_duration_mins"] = pd.to_numeric(df["visit_duration_mins"], errors="coerce").fillna(90).astype(int)
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce").fillna(4.3).astype(float)
    df["category"] = df["category"].fillna("other").astype(str)

    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["name", "lat", "lon"]).copy()
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)
    return df


def fmt_time(mins: int) -> str:
    h = (mins // 60) % 24
    m = mins % 60
    ampm = "AM" if h < 12 else "PM"
    hh = h if 1 <= h <= 12 else (12 if h == 0 else h - 12)
    return f"{hh}:{m:02d} {ampm}"


def explain_plan(itinerary: dict, selected_categories: list[str], pace: str, travel_mode: str, mode: str) -> str:
    if not itinerary.get("days"):
        return "I couldn’t build an itinerary with the current constraints. Try selecting more places, increasing budget, or switching to Full day."

    cats = ", ".join(selected_categories) if selected_categories else "all categories"
    total_cost = itinerary.get("total_cost", 0)
    remaining = itinerary.get("remaining_budget", 0)

    return (
        f"Mode: {mode}. Travel: {travel_mode}. Pace: {pace}. "
        f"Included categories: {cats}. "
        f"Estimated spend is ${total_cost}, leaving about ${remaining} as buffer."
    )


def parse_must_visits(text: str) -> list[str]:
    if not text.strip():
        return []
    # split by comma or newline
    raw = text.replace("\n", ",")
    items = [x.strip() for x in raw.split(",")]
    return [x for x in items if x]


def apply_editor_edits(master: pd.DataFrame, edited: pd.DataFrame) -> pd.DataFrame:
    """
    Update master using edited rows by name (and keep coordinates).
    Edited should have at least: include, name, avg_cost, visit_duration_mins, rating
    """
    m = master.copy()
    idx_map = {n: i for i, n in enumerate(m["name"].tolist())}

    for _, row in edited.iterrows():
        n = str(row["name"])
        if n in idx_map:
            i = idx_map[n]
            if "include" in row:
                m.at[i, "include"] = bool(row["include"])
            if "avg_cost" in row:
                m.at[i, "avg_cost"] = float(row["avg_cost"])
            if "visit_duration_mins" in row:
                m.at[i, "visit_duration_mins"] = int(row["visit_duration_mins"])
            if "rating" in row:
                m.at[i, "rating"] = float(row["rating"])
    return m


def prefs_from_categories(selected_categories: list[str]) -> dict:
    """
    Convert checkbox categories into a prefs dict the planner already expects.
    Selected => 1.0, not selected => 0.2
    """
    base = 0.2
    prefs = {}
    for c in ["nature", "food", "museums", "nightlife"]:
        prefs[c] = 1.0 if c in selected_categories else base
    return prefs


def reorder_with_must_visits(pois: list[dict], must_visits: list[str]) -> list[dict]:
    """
    Heuristic: if POI name contains a must-visit phrase, move those POIs to the front.
    """
    if not must_visits:
        return pois

    def match_score(name: str) -> int:
        name_l = name.lower()
        for mv in must_visits:
            if mv.lower() in name_l:
                return 1
        return 0

    return sorted(pois, key=lambda p: match_score(p.get("name", "")), reverse=True)


# ----------------------------
# Sidebar: Inputs
# ----------------------------
with st.sidebar:
    st.header("1) Trip Inputs")
    preset = st.selectbox("City preset", list(CITY_PRESETS.keys()), index=1)
    city = st.text_input("City (optional if preset selected)", value="Denver, CO")
    trip_start_date = st.date_input("Trip start date").isoformat()

    mode = st.selectbox("Trip style", list(MODE_CONFIG.keys()), index=0)

    days = st.slider("Number of days", 1, 7, 3)
    budget = st.number_input("Total budget ($)", min_value=0, value=400, step=50)

    travel_mode = st.selectbox("Travel mode", ["drive", "transit", "walk"], index=0)

    st.divider()
    st.header("2) Time preferences")
    # Default start time depends on mode, but allow override for Full day
    if MODE_CONFIG[mode]["start_hour"] is None:
        start_hour = st.slider("Day starts at", 6, 12, 10)
    else:
        start_hour = MODE_CONFIG[mode]["start_hour"]
        st.caption(f"Start time is set to {start_hour}:00 for this mode.")

    # Pace: default based on mode, but allow changing for Full day
    if MODE_CONFIG[mode]["pace"] is None:
        pace = st.selectbox("Pace", ["relaxed", "moderate", "packed"], index=1)
    else:
        pace = MODE_CONFIG[mode]["pace"]
        st.caption(f"Pace is set to {pace} for this mode.")

    st.divider()
    st.header("3) Interests (no weights)")
    selected_categories = st.multiselect(
        "What do you want to include?",
        options=ALL_CATEGORIES,
        default=["nature", "food", "museums", "nightlife"],
    )

    st.divider()
    st.header("4) Must-visit places")
    must_visit_text = st.text_area(
        "Type specific places (comma or new line). Example: Red Rocks, Denver Art Museum",
        height=90
    )
    must_visits = parse_must_visits(must_visit_text)

    st.divider()
    st.header("5) Place Search")
    radius_km = st.slider("Search radius (km)", 2, 30, 10)
    max_pois = st.slider("Max places to load", 30, 250, 120, step=10)
    relaxed_tags = st.checkbox("Relax place matching (recommended)", value=True)
    force_refresh = st.checkbox("Force refresh places (ignore cache)", value=False)


# Apply mode category forcing
forced = MODE_CONFIG[mode]["force_categories"]
if forced:
    # Keep only those categories (intersection), but if user picked none, use forced default
    if selected_categories:
        selected_categories = [c for c in selected_categories if c in forced]
    if not selected_categories:
        selected_categories = forced


# ----------------------------
# Location: preset -> geocode -> manual
# ----------------------------
coords = None
city_display = city.strip()

if CITY_PRESETS.get(preset):
    coords = CITY_PRESETS[preset]
    city_display = preset
else:
    if city_display:
        with st.spinner("Finding your city on the map..."):
            coords = geocode_city(city_display)

if not coords:
    st.warning("City lookup failed (geocoding). You can still continue by entering coordinates manually.")
    c1, c2 = st.columns(2)
    with c1:
        lat = st.number_input("Latitude", value=39.7392, format="%.6f")
    with c2:
        lon = st.number_input("Longitude", value=-104.9903, format="%.6f")
    st.info("Tip: In Google Maps, right-click → 'What's here?' to copy coordinates.")
else:
    lat, lon = coords
    st.success(f"Location set: {city_display} (lat: {lat:.4f}, lon: {lon:.4f})")


# ----------------------------
# Load POIs (Overpass)
# ----------------------------
with st.spinner("Loading places nearby (OpenStreetMap)…"):
    try:
        if force_refresh:
            pois = fetch_pois(
                lat=float(lat),
                lon=float(lon),
                radius_km=float(radius_km),
                limit=int(max_pois),
                relaxed=bool(relaxed_tags),
            )
        else:
            pois = load_pois_cached(
                lat=float(lat),
                lon=float(lon),
                radius_km=float(radius_km),
                limit=int(max_pois),
                relaxed=bool(relaxed_tags),
            )
    except Exception as e:
        st.error(f"Failed to load places. Try reducing radius / max places. Error: {e}")
        st.stop()

if not pois:
    st.warning("No places found.")
    st.stop()

df_pois = normalize_poi_defaults(pd.DataFrame(pois))

# normalize category naming a bit (optional)
df_pois["category"] = df_pois["category"].replace({"museum": "museums"})

# only show categories we support in UI; everything else becomes "other"
df_pois.loc[~df_pois["category"].isin(ALL_CATEGORIES), "category"] = "other"

# Default include based on selected categories
df_pois["include"] = df_pois["category"].isin(selected_categories)

# If mode is "Night dinner only", default include only food/nightlife/coffee
if mode == "Night dinner only":
    df_pois["include"] = df_pois["category"].isin(["food", "nightlife", "coffee"])


# ----------------------------
# Category browsing in separate columns/tabs
# ----------------------------
st.subheader("✅ Browse places by category")
st.caption("Each tab shows places from that category. Toggle “Use” to include/exclude.")

tabs = st.tabs(["All"] + [c.capitalize() for c in ALL_CATEGORIES] + ["Other"])

def editor_for(df_master: pd.DataFrame, category: str | None, key: str) -> pd.DataFrame:
    if category is None:
        df_view = df_master.copy()
    elif category == "other":
        df_view = df_master[df_master["category"] == "other"].copy()
    else:
        df_view = df_master[df_master["category"] == category].copy()

    if df_view.empty:
        st.info("No places found in this category.")
        return df_master

    edited = st.data_editor(
        df_view[["include", "name", "category", "avg_cost", "visit_duration_mins", "rating"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "include": st.column_config.CheckboxColumn("Use"),
            "avg_cost": st.column_config.NumberColumn("Avg Cost ($)", min_value=0, max_value=500, step=1),
            "visit_duration_mins": st.column_config.NumberColumn("Time (mins)", min_value=15, max_value=480, step=15),
            "rating": st.column_config.NumberColumn("Rating", min_value=0.0, max_value=5.0, step=0.1),
        },
        key=key,
    )
    return apply_editor_edits(df_master, edited)

with tabs[0]:
    df_pois = editor_for(df_pois, None, "editor_all")

for i, cat in enumerate(ALL_CATEGORIES, start=1):
    with tabs[i]:
        df_pois = editor_for(df_pois, cat, f"editor_{cat}")

with tabs[len(ALL_CATEGORIES) + 1]:
    df_pois = editor_for(df_pois, "other", "editor_other")


# Summary + map
chosen_df = df_pois[df_pois["include"]].copy()
chosen_df = chosen_df.dropna(subset=["lat", "lon"]).copy()

chosen_pois = chosen_df[["name", "category", "avg_cost", "visit_duration_mins", "rating", "lat", "lon"]].to_dict("records")
st.write(f"Places selected: {len(chosen_pois)}")

st.subheader("🗺 Map preview of selected places")
if chosen_pois:
    st.map(pd.DataFrame([{"lat": p["lat"], "lon": p["lon"]} for p in chosen_pois]))
else:
    st.info("No places selected.")


# ----------------------------
# Generate
# ----------------------------
st.subheader("📅 Generate itinerary")
generate = st.button("Build my itinerary", type="primary")

if generate:
    if len(chosen_pois) < 5 and mode != "Night dinner only":
        st.warning("Select at least ~5 places so the planner has enough options.")
        st.stop()

    # Convert selected categories into planner prefs (no weights UI)
    prefs = prefs_from_categories(selected_categories)

    # Must-visits: push them to the front so planner tends to pick them
    chosen_pois = reorder_with_must_visits(chosen_pois, must_visits)

    # For night dinner only, reduce how “busy” it is by forcing relaxed pace
    if mode == "Night dinner only":
        pace_run = "relaxed"
        # Also reduce days to 1 if user wants one evening plan (optional; keeping days as chosen)
    else:
        pace_run = pace

    with st.spinner("Optimizing your itinerary (including travel time)..."):
        itinerary = plan_itinerary(
            pois=chosen_pois,
            days=int(days),
            budget=float(budget),
            prefs=prefs,
            pace=pace_run,
            start_hour=int(start_hour),
            travel_mode=travel_mode,
        )

    m1, m2, m3 = st.columns(3)
    m1.metric("Estimated Total Cost", f"${itinerary.get('total_cost', 0)}")
    m2.metric("Remaining Budget", f"${itinerary.get('remaining_budget', 0)}")
    m3.metric("Total Time (activities + travel)", f"{itinerary.get('total_time_mins', 0)} mins")

    st.info(explain_plan(itinerary, selected_categories, pace_run, travel_mode, mode), icon="ℹ️")

    for day in itinerary.get("days", []):
        st.markdown("---")
        st.subheader(f"Day {day['day']} • Cost: ${day['day_cost']} • Time: {day['day_time_mins']} mins")

        timeline = day.get("timeline", [])
        if not timeline:
            st.warning("No activities selected for this day.")
            continue

        rows = []
        for i, e in enumerate(timeline, start=1):
            rows.append({
                "#": i,
                "Time": f"{fmt_time(int(e['start_min']))} → {fmt_time(int(e['end_min']))}",
                "Place": e["name"],
                "Category": e["category"],
                "Travel (mins)": int(e.get("travel_from_prev_mins", 0)),
                "Est. Cost ($)": round(float(e.get("avg_cost", 0.0)), 2),
            })

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.caption("Map links:")
        any_link = False
        for i, e in enumerate(timeline, start=1):
            lat_e, lon_e = e.get("lat"), e.get("lon")
            if lat_e is None or lon_e is None:
                continue
            any_link = True
            st.link_button(
                f"Open {i}. {e['name']} in Google Maps",
                f"https://www.google.com/maps/search/?api=1&query={lat_e},{lon_e}",
            )
        if not any_link:
            st.info("No map links available (missing coordinates).")

    st.markdown("---")
    st.subheader("⬇️ Export")

    safe_city = (city_display or "trip").replace(" ", "_").replace(",", "")
    pdf_bytes = itinerary_to_pdf(itinerary, title=f"Itinerary for {city_display or 'Trip'}")
    st.download_button(
        "Download PDF",
        data=pdf_bytes,
        file_name=f"itinerary_{safe_city}.pdf",
        mime="application/pdf",
    )

    ics_bytes = itinerary_to_ics(itinerary, trip_start_date=trip_start_date)
    st.download_button(
        "Download Calendar (.ics)",
        data=ics_bytes,
        file_name=f"itinerary_{safe_city}.ics",
        mime="text/calendar",
    )

    with st.expander("🔧 Debug: Raw itinerary JSON"):
        st.json(itinerary)
